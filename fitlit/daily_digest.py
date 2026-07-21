"""Daily sleep and day-in-review data for the Gmail notification service."""
from __future__ import annotations

import calendar
import json
import sqlite3
import statistics
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fitlit import config, insights, weekly_catalog
from fitlit.journal import PACIFIC


def _query(db_name: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    path = config.DB_DIR / f"{db_name}.db"
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.mean(clean) if clean else None


def _clock_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = (int(part) for part in value.split(":"))
    if hour < 12:
        hour += 24
    return hour * 60 + minute


def date_context(day: date) -> dict:
    days_in_year = 366 if calendar.isleap(day.year) else 365
    day_number = day.timetuple().tm_yday
    return {
        "iso": day.isoformat(),
        "full": day.strftime("%A, %B %-d, %Y"),
        "short": day.strftime("%a, %b %-d"),
        "weekday": day.strftime("%A"),
        "day_of_year": day_number,
        "days_in_year": days_in_year,
        "days_remaining": days_in_year - day_number,
        "iso_week": day.isocalendar().week,
    }


def _sleep_rows(start: date, end: date) -> list[dict]:
    start_utc = datetime.combine(start, time.min, PACIFIC).astimezone(timezone.utc)
    end_utc = datetime.combine(end + timedelta(days=1), time.min, PACIFIC).astimezone(
        timezone.utc
    )
    rows = _query("sleep", """
        SELECT name,start_time,end_time,data_json
        FROM sleep
        WHERE julianday(end_time) >= julianday(?)
          AND julianday(end_time) < julianday(?)
        ORDER BY end_time
    """, (start_utc.isoformat(), end_utc.isoformat()))
    normalized = []
    for row in rows:
        data = json.loads(row["data_json"] or "{}")
        summary = data.get("summary", {})
        started = _iso(row["start_time"]).astimezone(PACIFIC)
        ended = _iso(row["end_time"]).astimezone(PACIFIC)
        if not start <= ended.date() <= end:
            continue
        asleep = _optional_number(summary.get("minutesAsleep"))
        in_bed = _optional_number(summary.get("minutesInSleepPeriod"))
        elapsed = max(0, round((ended - started).total_seconds() / 60))
        stages = {
            str(stage.get("type", "")).lower(): _optional_number(stage.get("minutes"))
            for stage in summary.get("stagesSummary", [])
        }
        normalized.append({
            "record_id": row["name"],
            "day": ended.date().isoformat(),
            "start": started,
            "end": ended,
            "bedtime": started.strftime("%H:%M"),
            "wake": ended.strftime("%H:%M"),
            "minutes_asleep": round(asleep) if asleep is not None else None,
            "minutes_in_bed": round(in_bed) if in_bed is not None else None,
            "selection_minutes": round(in_bed) if in_bed is not None else elapsed,
            "hours_asleep": round(asleep / 60, 2) if asleep is not None else None,
            "efficiency_pct": (
                round(100 * asleep / in_bed, 1)
                if asleep is not None and in_bed else None
            ),
            "awake_min": (
                round(value)
                if (value := _optional_number(summary.get("minutesAwake"))) is not None
                else None
            ),
            "latency_min": (
                round(value)
                if (value := _optional_number(summary.get("minutesToFallAsleep"))) is not None
                else None
            ),
            "stages": {
                "deep": round(stages["deep"]) if stages.get("deep") is not None else None,
                "rem": round(stages["rem"]) if stages.get("rem") is not None else None,
                "light": round(stages["light"]) if stages.get("light") is not None else None,
                "awake": round(stages["awake"]) if stages.get("awake") is not None else None,
            },
        })
    return normalized


def _main_sleep_by_day(rows: list[dict]) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    for row in rows:
        existing = selected.get(row["day"])
        if existing is None or row["selection_minutes"] > existing["selection_minutes"]:
            selected[row["day"]] = row
    return selected


def _daily_vital(
    table: str,
    path: str,
    day: date,
) -> tuple[float | None, float | None, int]:
    rows = weekly_catalog.daily_metric_records(
        table,
        path,
        day - timedelta(days=7),
        day,
    )
    current = next(
        (row["value"] for row in rows if row["day"] == day.isoformat()),
        None,
    )
    baseline_values = [
        row["value"] for row in rows
        if day - timedelta(days=7) <= date.fromisoformat(row["day"]) < day
    ]
    baseline = _mean(baseline_values)
    return current, baseline, len(baseline_values)


def _oxygen(day: date) -> tuple[dict | None, float | None, int]:
    rows = weekly_catalog.oxygen_records(day - timedelta(days=7), day)
    current = next((row for row in rows if row["day"] == day.isoformat()), None)
    baseline_values = [
        row["value"] for row in rows
        if day - timedelta(days=7) <= date.fromisoformat(row["day"]) < day
    ]
    return current, _mean(baseline_values), len(baseline_values)


def _sleep_read(sleep: dict, baseline: dict, recovery: dict) -> tuple[list[str], str]:
    observations = []
    hours = sleep.get("hours_asleep")
    efficiency = sleep.get("efficiency_pct")
    delta = baseline.get("duration_delta_hours")
    deep = sleep["stages"]["deep"]
    rem = sleep["stages"]["rem"]
    restorative = deep + rem if deep is not None and rem is not None else None
    restorative_pct = (
        round(restorative / sleep["minutes_asleep"] * 100, 1)
        if restorative is not None and sleep["minutes_asleep"] else None
    )

    if delta is not None:
        observations.append(
            f"Sleep duration was {abs(delta):.2f} hours "
            f"{'above' if delta >= 0 else 'below'} the prior seven-night average."
        )
    if restorative_pct is not None:
        observations.append(
            f"Deep plus REM sleep contributed {restorative} minutes "
            f"({restorative_pct:.1f}% of sleep)."
        )
    if sleep.get("latency_min") is not None:
        observations.append(
            f"Sleep onset took {sleep['latency_min']} minutes; "
            f"the recorded window was {sleep['minutes_in_bed']} minutes."
        )
    if recovery.get("hrv_delta_pct") is not None:
        observations.append(
            f"HRV was {abs(recovery['hrv_delta_pct']):.1f}% "
            f"{'above' if recovery['hrv_delta_pct'] >= 0 else 'below'} its seven-day baseline."
        )

    if hours is None or efficiency is None:
        priority = "The wearable sleep summary is incomplete; use the recorded window cautiously until sync finishes."
    elif hours < 6.5:
        priority = "Protect an earlier bedtime tonight; sleep volume is the clearest recovery lever."
    elif efficiency < 85:
        priority = "Keep the sleep window long and reduce interruptions before adding training intensity."
    elif recovery.get("strain_flag"):
        priority = "Keep training flexible today because HRV and resting heart rate both moved unfavorably."
    elif hours >= 7.5 and efficiency >= 90:
        priority = "Recovery inputs support a normal training day; preserve the same sleep window."
    else:
        priority = "Use normal training volume, but avoid borrowing from tonight's sleep window."
    return observations[:4], priority


def build_sleep(day: date) -> dict | None:
    rows = _sleep_rows(day - timedelta(days=7), day)
    selected = _main_sleep_by_day(rows)
    sleep = selected.get(day.isoformat())
    if not sleep:
        return None
    baseline_rows = [
        row for wake_day, row in selected.items()
        if day - timedelta(days=7) <= date.fromisoformat(wake_day) < day
    ]
    baseline_hours = _mean([row["hours_asleep"] for row in baseline_rows])
    baseline_efficiency = _mean([row["efficiency_pct"] for row in baseline_rows])
    bedtimes = [
        value for value in (_clock_minutes(row["bedtime"]) for row in baseline_rows)
        if value is not None
    ]

    hrv, hrv_baseline, hrv_coverage = _daily_vital(
        "dailyHeartRateVariability",
        "averageHeartRateVariabilityMilliseconds",
        day,
    )
    resting_hr, rhr_baseline, rhr_coverage = _daily_vital(
        "dailyRestingHeartRate",
        "beatsPerMinute",
        day,
    )
    respiratory, respiratory_baseline, respiratory_coverage = _daily_vital(
        "dailyRespiratoryRate",
        "breathsPerMinute",
        day,
    )
    oxygen, oxygen_baseline, oxygen_coverage = _oxygen(day)
    hrv_delta = (
        round((hrv - hrv_baseline) / hrv_baseline * 100, 1)
        if hrv is not None and hrv_baseline else None
    )
    rhr_delta = (
        round(resting_hr - rhr_baseline, 1)
        if resting_hr is not None and rhr_baseline is not None else None
    )
    recovery = {
        "hrv_ms": round(hrv, 1) if hrv is not None else None,
        "hrv_baseline_ms": round(hrv_baseline, 1) if hrv_baseline is not None else None,
        "hrv_delta_pct": hrv_delta,
        "resting_hr_bpm": round(resting_hr, 1) if resting_hr is not None else None,
        "resting_hr_baseline_bpm": (
            round(rhr_baseline, 1) if rhr_baseline is not None else None
        ),
        "resting_hr_delta_bpm": rhr_delta,
        "spo2_pct": round(oxygen["value"], 1) if oxygen else None,
        "spo2_lower_pct": round(oxygen["lower"], 1) if oxygen and oxygen.get("lower") is not None else None,
        "spo2_baseline_pct": round(oxygen_baseline, 1) if oxygen_baseline is not None else None,
        "respiratory_rate": round(respiratory, 1) if respiratory is not None else None,
        "respiratory_baseline": (
            round(respiratory_baseline, 1) if respiratory_baseline is not None else None
        ),
        "strain_flag": bool(
            hrv_delta is not None
            and rhr_delta is not None
            and hrv_delta < -10
            and rhr_delta > 3
        ),
    }
    baseline = {
        "nights": len(baseline_rows),
        "avg_hours": round(baseline_hours, 2) if baseline_hours is not None else None,
        "avg_efficiency_pct": (
            round(baseline_efficiency, 1) if baseline_efficiency is not None else None
        ),
        "duration_delta_hours": (
            round(sleep["hours_asleep"] - baseline_hours, 2)
            if sleep["hours_asleep"] is not None and baseline_hours is not None else None
        ),
        "bedtime_consistency_min": (
            round(statistics.pstdev(bedtimes)) if len(bedtimes) >= 2 else None
        ),
    }
    observations, priority = _sleep_read(sleep, baseline, recovery)
    return {
        "date": date_context(day),
        "sleep": sleep,
        "baseline": baseline,
        "recovery": recovery,
        "observations": observations,
        "priority": priority,
        "coverage": {
            "sleep_baseline_nights": len(baseline_rows),
            "hrv_baseline_days": hrv_coverage,
            "resting_hr_baseline_days": rhr_coverage,
            "oxygen_baseline_days": oxygen_coverage,
            "respiratory_baseline_days": respiratory_coverage,
        },
    }


def _hourly_steps(day: date) -> list[dict]:
    rows = _query("live_activity", """
        SELECT hour, SUM(value) steps FROM (
            SELECT CAST(
                       strftime(
                           '%H',
                           datetime(
                               start_time,
                               printf(
                                   '%+d seconds',
                                   CAST(REPLACE(COALESCE(start_utc_offset,'-28800s'),'s','') AS INTEGER)
                               )
                           )
                       ) AS INTEGER
                   ) hour,
                   start_time,
                   MAX(count) value
            FROM steps
            WHERE date(
                      start_time,
                      printf(
                          '%+d seconds',
                          CAST(REPLACE(COALESCE(start_utc_offset,'-28800s'),'s','') AS INTEGER)
                      )
                  ) = ?
            GROUP BY start_time
        )
        GROUP BY hour ORDER BY hour
    """, (day.isoformat(),))
    by_hour = {int(row["hour"]): int(row["steps"]) for row in rows}
    return [{"hour": hour, "steps": by_hour.get(hour, 0)} for hour in range(24)]


def _movement_blocks(hours: list[dict]) -> list[dict]:
    blocks = []
    for start in range(0, 24, 3):
        steps = sum(row["steps"] for row in hours[start:start + 3])
        blocks.append({
            "label": f"{start % 12 or 12}{'a' if start < 12 else 'p'}",
            "start_hour": start,
            "steps": steps,
        })
    return blocks


def _hour_label(hour: int) -> str:
    return f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'}"


def _daily_facts(
    day: date,
    activity: dict,
    recent_activity: list[dict],
    hours: list[dict],
    sessions: list[dict],
    sleep: dict | None,
) -> list[str]:
    facts = []
    steps = activity.get("steps")
    captured = [row for row in recent_activity if row.get("steps") is not None]
    if steps is not None and captured:
        ranked = sorted((row["steps"] for row in captured), reverse=True)
        rank = ranked.index(steps) + 1 if steps in ranked else None
        if rank is not None:
            facts.append(
                f"This was the #{rank} step day across {len(ranked)} recently captured days."
            )
    peak = max(hours, key=lambda row: row["steps"], default=None)
    if peak and peak["steps"] and steps:
        end_hour = (peak["hour"] + 1) % 24
        facts.append(
            f"Peak movement was {_hour_label(peak['hour'])}–{_hour_label(end_hour)} "
            f"with {peak['steps']:,} steps ({peak['steps'] / steps * 100:.0f}% of the day)."
        )
    trusted = [session for session in sessions if not session["quality_flags"]]
    exercise_calories = sum(session["calories"] for session in trusted)
    if exercise_calories and activity.get("calories_out"):
        facts.append(
            f"Recorded exercise contributed {exercise_calories:,} kcal, "
            f"{exercise_calories / activity['calories_out'] * 100:.0f}% of total energy output."
        )
    if sleep and sleep["sleep"].get("hours_asleep") is not None:
        efficiency = sleep["sleep"].get("efficiency_pct")
        if efficiency is not None:
            facts.append(
                f"The day started after {sleep['sleep']['hours_asleep']:.2f} hours of sleep "
                f"at {efficiency:.1f}% efficiency."
            )
    return facts[:4]


def build_day(day: date) -> dict:
    activity_rows = weekly_catalog.activity_records(day - timedelta(days=6), day)
    recent_activity = [
        row for row in activity_rows
        if day - timedelta(days=13) <= date.fromisoformat(row["day"]) <= day
    ]
    activity = next(
        (row for row in recent_activity if row["day"] == day.isoformat()),
        {"day": day.isoformat(), "steps": None, "calories_out": None},
    )
    sessions = weekly_catalog.session_records(day, day)
    trusted_sessions = [session for session in sessions if not session["quality_flags"]]
    hours = _hourly_steps(day)
    movement_blocks = _movement_blocks(hours)
    sleep = build_sleep(day)
    weight = insights.weight_trend(14)
    facts = _daily_facts(day, activity, recent_activity, hours, sessions, sleep)
    seven_day_steps = [
        row["steps"] for row in recent_activity[-7:] if row.get("steps") is not None
    ]
    avg_steps = _mean(seven_day_steps)
    exercise_calories = sum(session["calories"] for session in trusted_sessions)
    workout_minutes = sum(session["duration_min"] for session in trusted_sessions)
    zone_minutes = sum(session["active_zone_minutes"] for session in trusted_sessions)
    recovery = sleep["recovery"] if sleep else {}

    observations = []
    if activity.get("steps") is not None and avg_steps is not None:
        delta = activity["steps"] - avg_steps
        observations.append(
            f"Steps finished {abs(delta):,.0f} "
            f"{'above' if delta >= 0 else 'below'} the seven-day average."
        )
    if trusted_sessions:
        observations.append(
            f"{len(trusted_sessions)} trusted exercise record(s) added "
            f"{workout_minutes} minutes and {exercise_calories:,} kcal."
        )
    if recovery.get("hrv_delta_pct") is not None:
        observations.append(
            f"HRV was {abs(recovery['hrv_delta_pct']):.1f}% "
            f"{'above' if recovery['hrv_delta_pct'] >= 0 else 'below'} baseline."
        )

    return {
        "date": date_context(day),
        "activity": {
            **activity,
            "seven_day_avg_steps": round(avg_steps) if avg_steps is not None else None,
            "step_goal_pct": (
                round(activity["steps"] / 10_000 * 100)
                if activity.get("steps") is not None else None
            ),
        },
        "movement": {
            "hours": hours,
            "blocks": movement_blocks,
            "peak_hour": max(hours, key=lambda row: row["steps"], default=None),
        },
        "training": {
            "formal_records": len(sessions),
            "trusted_records": len(trusted_sessions),
            "workout_minutes": workout_minutes,
            "exercise_calories": exercise_calories,
            "active_zone_minutes": zone_minutes,
            "sessions": sessions,
        },
        "sleep": sleep,
        "recovery": recovery,
        "weight": {
            "avg7_lb": weight.get("avg7_lb"),
            "trend_lb": weight.get("trend_lb"),
            "readings": weight.get("n_readings"),
        },
        "facts": facts,
        "observations": observations,
        "coverage": {
            "activity_days": len(recent_activity),
            "hourly_step_samples": sum(1 for row in hours if row["steps"]),
            "formal_workouts": len(sessions),
            "sleep_available": bool(sleep),
        },
    }


def sleep_ai_payload(digest: dict) -> dict:
    sleep = digest["sleep"]
    baseline = digest["baseline"]
    recovery = digest["recovery"]
    return {
        "report_type": "sleep",
        "hours_asleep": sleep["hours_asleep"],
        "efficiency_pct": sleep["efficiency_pct"],
        "deep_min": sleep["stages"]["deep"],
        "rem_min": sleep["stages"]["rem"],
        "awake_min": sleep["awake_min"],
        "latency_min": sleep["latency_min"],
        "seven_day_hours": baseline["avg_hours"],
        "vs_seven_day_hours": baseline["duration_delta_hours"],
        "hrv_ms": recovery["hrv_ms"],
        "resting_hr_bpm": recovery["resting_hr_bpm"],
        "spo2_pct": recovery["spo2_pct"],
        "respiratory_rate": recovery["respiratory_rate"],
    }


def day_ai_payload(digest: dict) -> dict:
    activity = digest["activity"]
    training = digest["training"]
    sleep = digest["sleep"]["sleep"] if digest["sleep"] else {}
    recovery = digest["recovery"]
    return {
        "report_type": "daily",
        "steps": activity["steps"],
        "step_goal_pct": activity["step_goal_pct"],
        "calories_out": activity["calories_out"],
        "seven_day_average_steps": activity["seven_day_avg_steps"],
        "workout_records": training["trusted_records"],
        "workout_minutes": training["workout_minutes"],
        "exercise_calories": training["exercise_calories"],
        "active_zone_minutes": training["active_zone_minutes"],
        "sleep_hours": sleep.get("hours_asleep"),
        "sleep_efficiency_pct": sleep.get("efficiency_pct"),
        "hrv_ms": recovery.get("hrv_ms"),
        "hrv_delta_pct": recovery.get("hrv_delta_pct"),
        "resting_hr_bpm": recovery.get("resting_hr_bpm"),
        "spo2_pct": recovery.get("spo2_pct"),
        "respiratory_rate": recovery.get("respiratory_rate"),
    }
