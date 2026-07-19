"""15-minute health-event detector and Gmail notification dispatcher."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fitlit import (
    ai_insights,
    components,
    config,
    gmail_auth,
    gmail_client,
    insights,
    weekly_catalog,
)
from fitlit.gmail_templates import (
    Metric,
    Report,
    append_ai_insight,
    report,
    weekly_report,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger("fitlit.gmail_service")
PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Notification:
    event_key: str
    kind: str
    report: Report
    mandatory: bool = False
    send_if_below: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    ai_payload: dict | None = None


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _duration_seconds(value) -> float:
    return _num(str(value or "").rstrip("s"))


def _dated_subject(kind: str, when: datetime, summary: str) -> str:
    return f"FitLit {kind} | {when.strftime('%b %-d')} | {summary}"


def _ro(db_name: str) -> sqlite3.Connection | None:
    path = config.DB_DIR / f"{db_name}.db"
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


class NotificationStore:
    """At-most-once ledger; attempted sends count toward the hard daily cap."""

    def __init__(self, path: Path = config.GMAIL_NOTIFICATION_DB):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS notifications (
                    event_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    pacific_date TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    message_id TEXT,
                    error TEXT,
                    window_start TEXT,
                    window_end TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_gmail_day
                ON notifications(pacific_date, status);
            """)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(notifications)")
            }
            if "window_start" not in columns:
                connection.execute("ALTER TABLE notifications ADD COLUMN window_start TEXT")
            if "window_end" not in columns:
                connection.execute("ALTER TABLE notifications ADD COLUMN window_end TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def attempted_today(self, day: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE pacific_date=? "
                "AND status IN ('sending','sent','failed','unknown')",
                (day,),
            ).fetchone()[0]

    def sent_today(self, day: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE pacific_date=? AND status='sent'",
                (day,),
            ).fetchone()[0]

    def has_event(self, event_key: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM notifications WHERE event_key=?", (event_key,)
            ).fetchone() is not None

    def has_sent_kind(self, day: str, kinds: tuple[str, ...]) -> bool:
        placeholders = ",".join("?" for _ in kinds)
        with self._connect() as connection:
            return connection.execute(
                f"SELECT 1 FROM notifications WHERE pacific_date=? AND status='sent' "
                f"AND kind IN ({placeholders}) LIMIT 1",
                (day, *kinds),
            ).fetchone() is not None

    def reserve(self, notification: Notification, now: datetime) -> bool:
        day = now.astimezone(PACIFIC).date().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM notifications WHERE event_key=?", (notification.event_key,)
            ).fetchone():
                return False
            if notification.kind == "workout" and notification.window_start and notification.window_end:
                start = (notification.window_start - timedelta(minutes=15)).astimezone(
                    timezone.utc
                ).isoformat()
                end = (notification.window_end + timedelta(minutes=15)).astimezone(
                    timezone.utc
                ).isoformat()
                if connection.execute(
                    "SELECT 1 FROM notifications WHERE kind='workout' "
                    "AND status IN ('sending','sent','failed','unknown') "
                    "AND window_start IS NOT NULL AND window_end IS NOT NULL "
                    "AND window_start <= ? AND window_end >= ? LIMIT 1",
                    (end, start),
                ).fetchone():
                    return False
            attempted = connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE pacific_date=? "
                "AND status IN ('sending','sent','failed','unknown')",
                (day,),
            ).fetchone()[0]
            sent = connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE pacific_date=? AND status='sent'",
                (day,),
            ).fetchone()[0]
            if attempted >= config.GMAIL_DAILY_MAX:
                return False
            if not notification.mandatory and attempted >= config.GMAIL_DAILY_MAX - 1:
                return False
            if notification.send_if_below is not None and sent >= notification.send_if_below:
                return False
            connection.execute(
                "INSERT INTO notifications(event_key,kind,pacific_date,subject,status,created_at,"
                "window_start,window_end) VALUES(?,?,?,?,?,?,?,?)",
                (
                    notification.event_key,
                    notification.kind,
                    day,
                    notification.report.subject,
                    "sending",
                    now.astimezone(timezone.utc).isoformat(),
                    notification.window_start.astimezone(timezone.utc).isoformat()
                    if notification.window_start else None,
                    notification.window_end.astimezone(timezone.utc).isoformat()
                    if notification.window_end else None,
                ),
            )
            connection.commit()
            return True

    def finish(self, event_key: str, *, message_id: str | None = None, error: str | None = None) -> None:
        status = "sent" if message_id else "failed"
        with self._connect() as connection:
            connection.execute(
                "UPDATE notifications SET status=?,sent_at=?,message_id=?,error=? WHERE event_key=?",
                (
                    status,
                    datetime.now(timezone.utc).isoformat() if message_id else None,
                    message_id,
                    error,
                    event_key,
                ),
            )
            connection.commit()

    def release(self, event_key: str) -> None:
        """Remove a reservation when authorization failed before Gmail accepted it."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM notifications WHERE event_key=? AND status='sending'",
                (event_key,),
            )
            connection.commit()

    def recent(self, limit: int = 10) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_key,kind,pacific_date,subject,status,sent_at,error "
                "FROM notifications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _sleep_candidate(now: datetime) -> Notification | None:
    connection = _ro("sleep")
    if not connection:
        return None
    try:
        try:
            rows = connection.execute(
                "SELECT name,start_time,end_time,data_json FROM sleep "
                "WHERE end_time IS NOT NULL ORDER BY end_time DESC LIMIT 5"
            ).fetchall()
        except sqlite3.OperationalError:
            return None
    finally:
        connection.close()
    selected = None
    for row in rows:
        ended = _iso(row["end_time"]).astimezone(PACIFIC)
        if ended <= now and ended.date() == now.date() and now - ended <= timedelta(hours=18):
            selected = row
            break
    if not selected:
        return None

    data = json.loads(selected["data_json"]) or {}
    summary = data.get("summary", {})
    asleep = _num(summary.get("minutesAsleep"))
    in_bed = _num(summary.get("minutesInSleepPeriod"))
    awake = _num(summary.get("minutesAwake"))
    latency = (
        _num(summary.get("minutesToFallAsleep"))
        if summary.get("minutesToFallAsleep") is not None else None
    )
    efficiency = 100 * asleep / in_bed if in_bed else 0
    stages = {
        str(stage.get("type", "")).lower(): _num(stage.get("minutes"))
        for stage in summary.get("stagesSummary", [])
    }
    start = _iso(selected["start_time"]).astimezone(PACIFIC)
    end = _iso(selected["end_time"]).astimezone(PACIFIC)
    trend = insights.sleep_trend(7)
    recovery = components.recovery_history(30)
    recovery_day = next(
        (row for row in recovery.get("series", []) if row["day"] == end.date().isoformat()), {}
    )
    avg_hours = trend.get("avg_hours_asleep")
    delta = asleep / 60 - avg_hours if avg_hours is not None else None
    subject = _dated_subject("Sleep", end, f"{asleep / 60:.2f}h · {efficiency:.0f}%")
    sleep_report = report(
        subject=subject,
        kicker="Morning recovery",
        title="Sleep report",
        subtitle=end.strftime("%A, %B %-d · wake %-I:%M %p PT"),
        metrics=[
            Metric("Asleep", f"{asleep / 60:.2f}", "hours", "#5f8579"),
            Metric("Efficiency", f"{efficiency:.1f}", "%", "#7c8154"),
            Metric("Deep", f"{stages.get('deep', 0):.0f}", "min", "#5f8579"),
            Metric("REM", f"{stages.get('rem', 0):.0f}", "min", "#b07766"),
        ],
        details=[
            ("Date", end.strftime("%A, %B %-d, %Y")),
            ("Sleep window", f"{start.strftime('%-I:%M %p')} – {end.strftime('%-I:%M %p')}"),
            ("Time to sleep", f"{latency:.0f} min" if latency is not None else "Unavailable"),
            ("Awake", f"{awake:.0f} min"),
            ("7-day duration", f"{avg_hours:.2f} h" if avg_hours is not None else "No baseline"),
            ("Vs 7-day average", f"{delta:+.2f} h" if delta is not None else "No baseline"),
            ("HRV", f"{recovery_day.get('hrv_ms')} ms" if recovery_day.get("hrv_ms") is not None else "No data"),
            ("Resting HR", f"{recovery_day.get('resting_hr')} bpm" if recovery_day.get("resting_hr") is not None else "No data"),
        ],
        note="Protect 7.5+ hours; use the trend, not one night, to adjust training.",
    )
    return Notification(
        event_key=f"sleep:{selected['name']}",
        kind="sleep",
        report=sleep_report,
        mandatory=True,
        ai_payload={
            "report_type": "sleep",
            "hours_asleep": round(asleep / 60, 2),
            "efficiency_pct": round(efficiency, 1),
            "deep_min": round(stages.get("deep", 0)),
            "rem_min": round(stages.get("rem", 0)),
            "awake_min": round(awake),
            "latency_min": round(latency) if latency is not None else None,
            "seven_day_hours": round(avg_hours, 2) if avg_hours is not None else None,
            "vs_seven_day_hours": round(delta, 2) if delta is not None else None,
            "hrv_ms": recovery_day.get("hrv_ms"),
            "resting_hr_bpm": recovery_day.get("resting_hr"),
        },
    )


def _formal_workout_candidates(now: datetime) -> list[Notification]:
    connection = _ro("daily_summaries")
    if not connection:
        return []
    try:
        try:
            rows = connection.execute(
                "SELECT name,start_time,end_time,exercise_type,display_name,active_duration,"
                "calories_kcal,distance_millimeters,active_zone_minutes,data_json "
                "FROM exercise WHERE end_time IS NOT NULL ORDER BY end_time DESC LIMIT 20"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        connection.close()

    notifications = []
    for row in reversed(rows):
        ended = _iso(row["end_time"]).astimezone(PACIFIC)
        if not timedelta(minutes=10) <= now - ended <= timedelta(hours=8):
            continue
        started = _iso(row["start_time"]).astimezone(PACIFIC)
        data = json.loads(row["data_json"]) or {}
        summary = data.get("metricsSummary", {})
        avg_hr = _num(summary.get("averageHeartRateBeatsPerMinute"), default=-1)
        duration_min = _duration_seconds(row["active_duration"]) / 60
        zone_min = _num(row["active_zone_minutes"])
        exercise_type = row["exercise_type"] or "EXERCISE"
        if exercise_type == "WALKING" and avg_hr < 105 and zone_min < 10:
            continue
        distance_km = _num(row["distance_millimeters"]) / 1_000_000
        steps = int(_num(summary.get("steps")))
        calories = int(_num(row["calories_kcal"]))
        title = row["display_name"] or exercise_type.replace("_", " ").title()
        subject = _dated_subject(
            "Workout",
            started,
            f"{duration_min:.0f} min · {max(avg_hr, 0):.0f} avg BPM",
        )
        workout_report = report(
            subject=subject,
            kicker="Workout complete",
            title=title,
            subtitle=(
                f"{started.strftime('%A, %B %-d')} · "
                f"{started.strftime('%-I:%M %p')} – {ended.strftime('%-I:%M %p')} PT"
            ),
            metrics=[
                Metric("Duration", f"{duration_min:.0f}", "min", "#bd6a4a"),
                Metric("Average HR", f"{avg_hr:.0f}" if avg_hr >= 0 else "—", "bpm", "#a94e33"),
                Metric("Distance", f"{distance_km:.2f}", "km", "#5f8579"),
                Metric("Exercise energy", f"{calories:,}", "kcal", "#c8973f"),
            ],
            details=[
                ("Date", started.strftime("%A, %B %-d, %Y")),
                ("Steps", f"{steps:,}"),
                ("Active-zone load", f"{zone_min:.0f} min"),
                ("Exercise type", exercise_type.replace("_", " ").title()),
                ("Source", "Fitbit exercise record"),
            ],
        )
        notifications.append(Notification(
            event_key=f"exercise:{row['name']}",
            kind="workout",
            report=workout_report,
            mandatory=True,
            window_start=started,
            window_end=ended,
            ai_payload={
                "report_type": "workout",
                "exercise_type": exercise_type,
                "duration_min": round(duration_min),
                "average_hr_bpm": round(avg_hr) if avg_hr >= 0 else None,
                "distance_km": round(distance_km, 2),
                "calories_kcal": calories,
                "steps": steps,
                "active_zone_min": round(zone_min),
            },
        ))
    return notifications


def _inferred_workout_candidate(now: datetime, formal: list[Notification]) -> Notification | None:
    trace = components.training_trace()
    points = trace.get("points", [])
    if not points or trace.get("date") != now.date().isoformat():
        return None
    rest = components.heart_today(bucket_min=5).get("resting_hr") or 60
    threshold = max(100, rest + 30)
    active = [
        point for point in points
        if point.get("bpm") is not None and point["bpm"] >= threshold and point.get("steps", 0) < 80
    ]
    groups: list[list[dict]] = []
    for point in active:
        point_time = datetime.strptime(point["t"], "%H:%M")
        if not groups:
            groups.append([point])
            continue
        previous = datetime.strptime(groups[-1][-1]["t"], "%H:%M")
        if point_time - previous <= timedelta(minutes=15):
            groups[-1].append(point)
        else:
            groups.append([point])
    groups = [group for group in groups if len(group) >= 4]
    if not groups:
        return None
    group = max(groups, key=lambda rows: sum((row["bpm"] - threshold) for row in rows))
    start_time = datetime.combine(now.date(), datetime.strptime(group[0]["t"], "%H:%M").time(), PACIFIC)
    end_time = datetime.combine(now.date(), datetime.strptime(group[-1]["t"], "%H:%M").time(), PACIFIC) + timedelta(minutes=5)
    if now - end_time < timedelta(minutes=15):
        return None
    session = [
        point for point in points
        if start_time.time() <= datetime.strptime(point["t"], "%H:%M").time() < end_time.time()
    ]
    heart = [point["bpm"] for point in session if point.get("bpm") is not None]
    if not heart:
        return None
    duration_min = round((end_time - start_time).total_seconds() / 60)
    avg_hr = statistics.mean(heart)
    max_hr = max(heart)
    if duration_min < 25 or avg_hr < 105 or max_hr < 135:
        return None
    after = [
        point["bpm"] for point in points
        if point.get("bpm") is not None
        and end_time.time() <= datetime.strptime(point["t"], "%H:%M").time()
        < (end_time + timedelta(minutes=15)).time()
    ]
    end_values = [point["bpm"] for point in session[-2:] if point.get("bpm") is not None]
    recovery = statistics.mean(end_values) - statistics.mean(after) if after and end_values else None
    if recovery is None or recovery < 12:
        return None
    for item in formal:
        if not item.window_start or not item.window_end:
            continue
        if item.window_start <= end_time + timedelta(minutes=15) and item.window_end >= start_time - timedelta(minutes=15):
            return None
    peak = max(session, key=lambda point: point.get("bpm") or 0)
    ordered = sorted(heart)
    p90 = ordered[round((len(ordered) - 1) * .9)]
    zdefs = trace.get("zones", [])
    zones = {zone["zone"]: 0 for zone in zdefs}
    below = 0
    for bpm in heart:
        matched = next((zone for zone in zdefs if zone["low"] <= bpm <= zone["high"]), None)
        if matched:
            zones[matched["zone"]] += 5
        else:
            below += 5
    zone_text = " · ".join(
        [f"Below Z1 {below}m"] + [f"{name} {minutes}m" for name, minutes in zones.items() if minutes]
    )
    subject = _dated_subject(
        "Workout",
        start_time,
        f"{duration_min} min · {avg_hr:.0f} avg · {max_hr} peak",
    )
    workout_report = report(
        subject=subject,
        kicker="Training-like session detected",
        title="Workout summary",
        subtitle=(
            f"{start_time.strftime('%A, %B %-d')} · "
            f"{start_time.strftime('%-I:%M %p')} – {end_time.strftime('%-I:%M %p')} PT · inferred"
        ),
        metrics=[
            Metric("Duration", str(duration_min), "min", "#bd6a4a"),
            Metric("Average HR", f"{avg_hr:.0f}", "bpm", "#a94e33"),
            Metric("Peak HR", str(max_hr), "bpm", "#a94e33"),
            Metric("Recovery", f"{recovery:.0f}", "bpm drop", "#7c8154"),
        ],
        details=[
            ("Date", start_time.strftime("%A, %B %-d, %Y")),
            ("90th percentile HR", f"{p90} bpm"),
            ("Movement", f"{sum(point.get('steps', 0) for point in session):,} steps"),
            ("Heart-rate distribution", zone_text),
            ("Detection", "Heart + movement pattern; no formal exercise record"),
        ],
        note="Heart rate measures session density, not muscular load; track sets, reps, and load for hypertrophy progression.",
    )
    return Notification(
        event_key=f"inferred-workout:{now.date().isoformat()}:{peak['t']}",
        kind="workout",
        report=workout_report,
        mandatory=True,
        window_start=start_time,
        window_end=end_time,
        ai_payload={
            "report_type": "workout",
            "exercise_type": "INFERRED",
            "duration_min": duration_min,
            "average_hr_bpm": round(avg_hr),
            "peak_hr_bpm": max_hr,
            "p90_hr_bpm": p90,
            "recovery_drop_bpm": round(recovery),
            "steps": sum(point.get("steps", 0) for point in session),
        },
    )


def _step_milestone_candidate(now: datetime) -> Notification | None:
    today = now.date().isoformat()
    activity = insights.activity_summary(2)
    row = next((item for item in activity.get("series", []) if item["day"] == today), None)
    if not row or (row.get("steps") or 0) < 10_000:
        return None
    steps = int(row["steps"])
    milestone_report = report(
        subject=f"FitLit Activity | {steps:,} steps",
        kicker="Daily milestone",
        title="10,000 steps reached",
        subtitle=now.strftime("%A, %B %-d · %-I:%M %p PT"),
        metrics=[
            Metric("Steps", f"{steps:,}", "", "#7c8154"),
            Metric("Goal", f"{steps / 10_000 * 100:.0f}", "%", "#c8973f"),
        ],
        details=[
            ("Calories out", f"{row.get('calories_out'):,} kcal" if row.get("calories_out") else "Pending"),
            ("Remaining to 15k", f"{max(0, 15_000 - steps):,} steps"),
        ],
    )
    return Notification(
        event_key=f"milestone:steps:10000:{today}",
        kind="activity",
        report=milestone_report,
    )


def _heart_signal_candidate(now: datetime, workout_detected: bool) -> Notification | None:
    if workout_detected:
        return None
    trace = components.training_trace()
    points = trace.get("points", [])[-3:]
    if len(points) < 3:
        return None
    peak = max((point.get("bpm") or 0) for point in points)
    steps = sum(point.get("steps", 0) for point in points)
    if peak < 160 or steps >= 100:
        return None
    average = statistics.mean(point["bpm"] for point in points if point.get("bpm") is not None)
    signal_report = report(
        subject=f"FitLit Signal | {peak} BPM low-movement peak",
        kicker="15-minute body signal",
        title="Elevated heart-rate window",
        subtitle=now.strftime("%A, %B %-d · %-I:%M %p PT"),
        metrics=[
            Metric("Peak HR", str(peak), "bpm", "#a94e33"),
            Metric("15-min average", f"{average:.0f}", "bpm", "#bd6a4a"),
            Metric("Movement", f"{steps:,}", "steps", "#5f8579"),
        ],
        details=[("Interpretation", "Interesting signal; not a diagnosis")],
        note="If this was unexpected, persistent, or accompanied by symptoms, use appropriate medical judgment rather than relying on wearable data.",
    )
    return Notification(
        event_key=f"heart-signal:{now.date().isoformat()}",
        kind="signal",
        report=signal_report,
        ai_payload={
            "report_type": "signal",
            "peak_hr_bpm": peak,
            "average_hr_bpm": round(average),
            "movement_steps": steps,
            "window_min": 15,
        },
    )


def _morning_fallback(now: datetime) -> Notification:
    recovery = components.recovery_history(30)
    today = next(
        (row for row in recovery.get("series", []) if row["day"] == now.date().isoformat()), {}
    )
    fallback_report = report(
        subject="FitLit Morning | Recovery data pending",
        kicker="Morning recovery",
        title="Recovery snapshot",
        subtitle=now.strftime("%A, %B %-d · %-I:%M %p PT"),
        metrics=[
            Metric("HRV", str(today.get("hrv_ms") or "—"), "ms", "#5f8579"),
            Metric("Resting HR", str(today.get("resting_hr") or "—"), "bpm", "#a94e33"),
        ],
        details=[
            ("Sleep session", "Not synced yet"),
            ("30-day HRV", f"{recovery.get('summary', {}).get('avg_hrv_ms', 0)} ms"),
            ("30-day resting HR", f"{recovery.get('summary', {}).get('avg_resting_hr', 0)} bpm"),
        ],
        note="FitLit will send the full sleep report if the wearable sync arrives later today.",
    )
    return Notification(
        event_key=f"morning-fallback:{now.date().isoformat()}",
        kind="morning",
        report=fallback_report,
        mandatory=True,
    )


def _evening_fill(now: datetime) -> Notification:
    today = now.date().isoformat()
    overview = components.overview_history(14)
    row = next((item for item in overview.get("series", []) if item["day"] == today), {})
    weight = insights.weight_trend(14)
    summary_report = report(
        subject=f"FitLit Daily | {(row.get('steps') or 0):,} steps · {row.get('calories_out') or 0:,} kcal",
        kicker="Daily body summary",
        title="Today in numbers",
        subtitle=now.strftime("%A, %B %-d · %-I:%M %p PT"),
        metrics=[
            Metric("Steps", f"{row.get('steps') or 0:,}", "", "#7c8154"),
            Metric("Calories out", f"{row.get('calories_out') or 0:,}", "kcal", "#c8973f"),
            Metric("HRV", str(row.get("hrv_ms") or "—"), "ms", "#5f8579"),
            Metric("Resting HR", str(row.get("resting_hr") or "—"), "bpm", "#a94e33"),
        ],
        details=[
            ("7-day weight average", f"{weight.get('avg7_lb')} lb" if weight.get("avg7_lb") else "No data"),
            ("Step goal", f"{(row.get('steps') or 0) / 10_000 * 100:.0f}%"),
            ("Data date", today),
        ],
    )
    return Notification(
        event_key=f"daily-fill:{today}",
        kind="daily",
        report=summary_report,
        mandatory=True,
        send_if_below=config.GMAIL_DAILY_MIN,
    )


def _weekly_candidate(now: datetime) -> Notification | None:
    bounds = weekly_catalog.delivery_week(now)
    if not bounds:
        return None
    start, end = bounds
    catalog = weekly_catalog.build(start, end)
    return Notification(
        event_key=f"weekly:{end.isoformat()}",
        kind="weekly",
        report=weekly_report(catalog),
        mandatory=True,
        ai_payload=weekly_catalog.ai_payload(catalog),
    )


def build_candidates(now: datetime, store: NotificationStore) -> list[Notification]:
    candidates: list[Notification] = []
    sleep = _sleep_candidate(now)
    if sleep:
        candidates.append(sleep)
    formal = _formal_workout_candidates(now)
    candidates.extend(formal)
    inferred = _inferred_workout_candidate(now, formal)
    if inferred:
        candidates.append(inferred)

    day = now.date().isoformat()
    if (
        now.hour >= config.GMAIL_MORNING_FALLBACK_HOUR
        and sleep is None
        and not store.has_sent_kind(day, ("sleep", "morning"))
    ):
        candidates.append(_morning_fallback(now))
    milestone = _step_milestone_candidate(now)
    if milestone:
        candidates.append(milestone)
    signal = _heart_signal_candidate(now, bool(formal or inferred))
    if signal:
        candidates.append(signal)
    weekly = _weekly_candidate(now)
    if weekly:
        candidates.append(weekly)
    if now.hour >= config.GMAIL_EVENING_FILL_HOUR:
        candidates.append(_evening_fill(now))
    return candidates


def dispatch(
    candidates: list[Notification],
    store: NotificationStore,
    now: datetime,
    *,
    sender=gmail_client.send,
    dry_run: bool = False,
) -> dict:
    result = {"sent": [], "skipped": [], "failed": [], "preview": []}
    for candidate in candidates:
        if dry_run:
            result["preview"].append({
                "event_key": candidate.event_key,
                "kind": candidate.kind,
                "subject": candidate.report.subject,
            })
            continue
        if not store.reserve(candidate, now):
            result["skipped"].append(candidate.event_key)
            continue
        rendered = candidate.report
        if candidate.ai_payload:
            insight = ai_insights.generate(candidate.ai_payload)
            if insight:
                rendered = append_ai_insight(
                    rendered,
                    headline=insight.headline,
                    observations=insight.observations,
                    confidence=insight.confidence,
                    provider=insight.provider,
                )
        try:
            message_id = sender(
                rendered.subject,
                rendered.text,
                rendered.html,
            )
        except gmail_auth.GmailAuthError as exc:
            store.release(candidate.event_key)
            result["failed"].append({"event_key": candidate.event_key, "error": str(exc)})
            continue
        except gmail_client.GmailSendError as exc:
            if exc.retryable:
                store.release(candidate.event_key)
            else:
                store.finish(candidate.event_key, error=str(exc))
            result["failed"].append({"event_key": candidate.event_key, "error": str(exc)})
            continue
        store.finish(candidate.event_key, message_id=message_id)
        result["sent"].append({"event_key": candidate.event_key, "message_id": message_id})
    return result


def run_once(*, now: datetime | None = None, dry_run: bool = False) -> dict:
    now = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    store = NotificationStore()
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(config.GMAIL_SERVICE_LOCK, "a+")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy", "sent": []}
        candidates = build_candidates(now, store)
        if not dry_run and not gmail_auth.is_configured():
            return {
                "status": "not-configured",
                "recipient_configured": bool(config.GMAIL_TO),
                "oauth_configured": bool(config.GMAIL_REFRESH_TOKEN),
                "candidates": [
                    {"kind": item.kind, "subject": item.report.subject}
                    for item in candidates
                ],
            }
        if not dry_run:
            try:
                gmail_auth.get_access_token()
            except gmail_auth.GmailAuthError as exc:
                return {
                    "status": "auth-error",
                    "error": str(exc),
                    "sent_today": store.sent_today(now.date().isoformat()),
                }
        return {
            "status": "dry-run" if dry_run else "ok",
            **dispatch(candidates, store, now, dry_run=dry_run),
            "sent_today": store.sent_today(now.date().isoformat()),
            "daily_min": config.GMAIL_DAILY_MIN,
            "daily_max": config.GMAIL_DAILY_MAX,
        }
    finally:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _masked_recipient() -> str:
    if not config.GMAIL_TO or "@" not in config.GMAIL_TO:
        return "not configured"
    local, domain = config.GMAIL_TO.split("@", 1)
    return f"{local[:1]}***@{domain}"


def status() -> dict:
    now = datetime.now(PACIFIC)
    store = NotificationStore()
    return {
        "configured": gmail_auth.is_configured(),
        "recipient": _masked_recipient(),
        "sent_today": store.sent_today(now.date().isoformat()),
        "attempted_today": store.attempted_today(now.date().isoformat()),
        "daily_min": config.GMAIL_DAILY_MIN,
        "daily_max": config.GMAIL_DAILY_MAX,
        "weekly_catalog": {
            "weekday": "Sunday",
            "hour_pacific": config.GMAIL_WEEKLY_REPORT_HOUR,
            "monday_retry_until": config.GMAIL_WEEKLY_RETRY_UNTIL_HOUR,
        },
        "recent": store.recent(),
    }


def preview_weekly(end: datetime | None = None, html_path: Path | None = None) -> dict:
    local = (end or datetime.now(PACIFIC)).astimezone(PACIFIC)
    week_start, week_end = weekly_catalog.week_bounds(local.date())
    catalog = weekly_catalog.build(week_start, week_end)
    rendered = weekly_report(catalog)
    if html_path:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(rendered.html)
    return {
        "subject": rendered.subject,
        "week": catalog["week"],
        "training": catalog["training"],
        "activity": catalog["activity"],
        "sleep": catalog["sleep"],
        "recovery": catalog["recovery"],
        "coverage": catalog["coverage"],
        "html_path": str(html_path) if html_path else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FitLit Gmail notification service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="evaluate and send due notifications")
    run_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("status", help="show configuration and delivery ledger")
    preview_parser = subparsers.add_parser(
        "weekly-preview",
        help="build the current weekly catalog without sending or reserving it",
    )
    preview_parser.add_argument(
        "--html",
        type=Path,
        help="write the rendered HTML to this path",
    )
    subparsers.add_parser("consent-url", help="print the gmail.send OAuth consent URL")
    args = parser.parse_args(argv)

    if args.command == "run":
        print(json.dumps(run_once(dry_run=args.dry_run), indent=2, default=str))
        return 0
    if args.command == "status":
        print(json.dumps(status(), indent=2, default=str))
        return 0
    if args.command == "weekly-preview":
        print(json.dumps(preview_weekly(html_path=args.html), indent=2, default=str))
        return 0
    if args.command == "consent-url":
        if not (config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET):
            print("Google OAuth client credentials are not configured.", file=sys.stderr)
            return 1
        print(gmail_auth.build_consent_url())
        return 0
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    raise SystemExit(main())
