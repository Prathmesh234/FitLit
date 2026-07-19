"""Weekly training, activity, sleep, and recovery catalog from local SQLite."""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fitlit import config
from fitlit.journal import PACIFIC

def week_bounds(day: date) -> tuple[date, date]:
    """Return the Monday-Sunday week containing ``day``."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def delivery_week(now: datetime) -> tuple[date, date] | None:
    """Return the due week during the Sunday window or Monday retry window."""
    local = now.astimezone(PACIFIC)
    if (
        local.weekday() == 6
        and local.hour >= config.GMAIL_WEEKLY_REPORT_HOUR
    ):
        return week_bounds(local.date())
    if local.weekday() == 0 and local.hour < config.GMAIL_WEEKLY_RETRY_UNTIL_HOUR:
        end = local.date() - timedelta(days=1)
        return end - timedelta(days=6), end
    return None


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


def _mean(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return statistics.mean(clean) if clean else None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 1)


def _split(
    rows: list[dict], start: date, end: date
) -> tuple[list[dict], list[dict]]:
    previous_start = start - timedelta(days=7)
    previous_end = start - timedelta(days=1)
    current = [row for row in rows if start <= date.fromisoformat(row["day"]) <= end]
    previous = [
        row for row in rows
        if previous_start <= date.fromisoformat(row["day"]) <= previous_end
    ]
    return current, previous


def _activity(start: date, end: date) -> list[dict]:
    since = (start - timedelta(days=7)).isoformat()
    steps = _query("live_activity", """
        SELECT day, SUM(value) steps FROM (
            SELECT date(
                       start_time,
                       printf(
                           '%+d seconds',
                           CAST(REPLACE(COALESCE(start_utc_offset,'-28800s'),'s','') AS INTEGER)
                       )
                   ) day,
                   start_time,
                   MAX(count) value
            FROM steps
            WHERE date(
                      start_time,
                      printf(
                          '%+d seconds',
                          CAST(REPLACE(COALESCE(start_utc_offset,'-28800s'),'s','') AS INTEGER)
                      )
                  ) >= ?
            GROUP BY start_time
        )
        GROUP BY day ORDER BY day
    """, (since,))
    step_by_day = {row["day"]: int(row["steps"]) for row in steps}
    calorie_rows = _query(
        "live_activity",
        "SELECT data_json FROM totalCalories ORDER BY fetched_at",
    )
    calories: dict[str, int] = {}
    for row in calorie_rows:
        data = json.loads(row["data_json"] or "{}")
        day = data.get("interval", {}).get("startTime")
        if day and day >= since and data.get("kcalSum") is not None:
            calories[day] = round(_number(data["kcalSum"]))
    days = sorted(set(step_by_day) | set(calories))
    return [
        {
            "day": day,
            "steps": step_by_day.get(day),
            "calories_out": calories.get(day),
        }
        for day in days
        if day <= end.isoformat()
    ]


def _sessions(start: date, end: date) -> list[dict]:
    rows = _query("daily_summaries", """
        SELECT name,start_time,end_time,exercise_type,display_name,active_duration,
               calories_kcal,distance_millimeters,exercise_steps,active_zone_minutes,
               json_extract(data_json,'$.metricsSummary.averageHeartRateBeatsPerMinute') avg_hr
        FROM exercise
        WHERE date(
                  start_time,
                  printf(
                      '%+d seconds',
                      CAST(REPLACE(COALESCE(start_utc_offset,'-28800s'),'s','') AS INTEGER)
                  )
              ) BETWEEN ? AND ?
        ORDER BY start_time
    """, (start.isoformat(), end.isoformat()))
    sessions = []
    for row in rows:
        started = _iso(row["start_time"]).astimezone(PACIFIC)
        ended = _iso(row["end_time"]).astimezone(PACIFIC)
        elapsed_seconds = max(0.0, (ended - started).total_seconds())
        active_seconds = _number(str(row["active_duration"] or "").rstrip("s"))
        duration_min = elapsed_seconds / 60
        calories = round(_number(row["calories_kcal"]))
        zone_minutes = round(_number(row["active_zone_minutes"]))
        average_hr = (
            round(_number(row["avg_hr"])) if row["avg_hr"] is not None else None
        )
        flags = []
        if active_seconds and elapsed_seconds and active_seconds > elapsed_seconds * 1.25 + 300:
            flags.append("active duration exceeds elapsed window")
        if duration_min < 2:
            flags.append("very short recorded window")
        if duration_min > 360:
            flags.append("recorded window exceeds 6 hours")
        if calories > 2500:
            flags.append("exercise energy unusually high")
        exercise_type = row["exercise_type"] or "EXERCISE"
        training = exercise_type != "WALKING" or zone_minutes >= 10 or (average_hr or 0) >= 105
        sessions.append({
            "id": row["name"],
            "day": started.date().isoformat(),
            "start": started.strftime("%-I:%M %p"),
            "type": exercise_type.replace("_", " ").title(),
            "name": row["display_name"] or exercise_type.replace("_", " ").title(),
            "duration_min": round(duration_min),
            "active_duration_min": round(active_seconds / 60) if active_seconds else None,
            "calories": calories,
            "distance_km": round(_number(row["distance_millimeters"]) / 1_000_000, 2),
            "steps": round(_number(row["exercise_steps"])),
            "active_zone_minutes": zone_minutes,
            "avg_hr": average_hr,
            "is_training": training,
            "quality_flags": flags,
        })
    return sessions


def _sleep(start: date, end: date) -> list[dict]:
    since = (start - timedelta(days=7)).isoformat()
    rows = _query("sleep", """
        SELECT start_time,end_time,data_json
        FROM sleep
        WHERE date(
                  end_time,
                  printf(
                      '%+d seconds',
                      CAST(REPLACE(COALESCE(end_utc_offset,'-28800s'),'s','') AS INTEGER)
                  )
              ) BETWEEN ? AND ?
        ORDER BY end_time
    """, (since, end.isoformat()))
    nights = []
    for row in rows:
        data = json.loads(row["data_json"] or "{}")
        summary = data.get("summary", {})
        asleep = _number(summary.get("minutesAsleep"))
        in_bed = _number(summary.get("minutesInSleepPeriod"))
        ended = _iso(row["end_time"]).astimezone(PACIFIC)
        started = _iso(row["start_time"]).astimezone(PACIFIC)
        nights.append({
            "day": ended.date().isoformat(),
            "hours_asleep": round(asleep / 60, 2) if asleep else None,
            "efficiency_pct": round(100 * asleep / in_bed, 1) if in_bed else None,
            "awake_min": round(_number(summary.get("minutesAwake"))),
            "bedtime": started.strftime("%H:%M"),
            "wake": ended.strftime("%H:%M"),
        })
    return nights


def _daily_values(table: str, value_path: str, start: date, end: date) -> list[dict]:
    rows = _query(
        "daily_summaries",
        f"SELECT data_json FROM {table} ORDER BY fetched_at",
    )
    values: dict[str, float] = {}
    since = start - timedelta(days=7)
    for row in rows:
        data = json.loads(row["data_json"] or "{}")
        raw_date = data.get("date", {})
        try:
            day = date(int(raw_date["year"]), int(raw_date["month"]), int(raw_date["day"]))
        except (KeyError, TypeError, ValueError):
            continue
        value: Any = data
        for part in value_path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if since <= day <= end and value is not None:
            values[day.isoformat()] = _number(value)
    return [{"day": day, "value": value} for day, value in sorted(values.items())]


def _oxygen(start: date, end: date) -> list[dict]:
    rows = _query(
        "daily_summaries",
        "SELECT data_json FROM dailyOxygenSaturation ORDER BY fetched_at",
    )
    values: dict[str, dict] = {}
    since = start - timedelta(days=7)
    for row in rows:
        data = json.loads(row["data_json"] or "{}")
        raw_date = data.get("date", {})
        try:
            day = date(int(raw_date["year"]), int(raw_date["month"]), int(raw_date["day"]))
        except (KeyError, TypeError, ValueError):
            continue
        if since <= day <= end and data.get("averagePercentage") is not None:
            values[day.isoformat()] = {
                "day": day.isoformat(),
                "value": _number(data["averagePercentage"]),
                "lower": (
                    _number(data["lowerBoundPercentage"])
                    if data.get("lowerBoundPercentage") is not None else None
                ),
                "upper": (
                    _number(data["upperBoundPercentage"])
                    if data.get("upperBoundPercentage") is not None else None
                ),
            }
    return [values[day] for day in sorted(values)]


def _clock_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = (int(part) for part in value.split(":"))
    if hour < 12:
        hour += 24
    return hour * 60 + minute


def _trend_text(label: str, value: float | None, unit: str, better: str) -> str | None:
    if value is None:
        return None
    direction = "up" if value > 0 else "down" if value < 0 else "flat"
    assessment = ""
    if direction != "flat":
        favorable = (direction == "up" and better == "up") or (
            direction == "down" and better == "down"
        )
        assessment = " · favorable" if favorable else " · watch"
    return f"{label} {direction} {abs(value):g}{unit} vs prior week{assessment}"


def summarize(
    start: date,
    end: date,
    *,
    activity: list[dict],
    sessions: list[dict],
    sleep: list[dict],
    hrv: list[dict],
    resting_hr: list[dict],
    oxygen: list[dict],
    respiratory: list[dict],
) -> dict:
    """Create a deterministic weekly catalog from normalized records."""
    sleep_by_wake_day: dict[str, dict] = {}
    for row in sleep:
        existing = sleep_by_wake_day.get(row["day"])
        if existing is None or (row.get("hours_asleep") or 0) > (
            existing.get("hours_asleep") or 0
        ):
            sleep_by_wake_day[row["day"]] = row
    sleep = [sleep_by_wake_day[day] for day in sorted(sleep_by_wake_day)]

    current_activity, previous_activity = _split(activity, start, end)
    current_sleep, previous_sleep = _split(sleep, start, end)
    current_hrv, previous_hrv = _split(hrv, start, end)
    current_rhr, previous_rhr = _split(resting_hr, start, end)
    current_oxygen, previous_oxygen = _split(oxygen, start, end)
    current_resp, previous_resp = _split(respiratory, start, end)

    activity_by_day = {row["day"]: row for row in current_activity}
    sleep_by_day = {row["day"]: row for row in current_sleep}
    days = []
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        act = activity_by_day.get(day, {})
        night = sleep_by_day.get(day, {})
        days.append({
            "day": day,
            "label": (start + timedelta(days=offset)).strftime("%a"),
            "steps": act.get("steps"),
            "calories_out": act.get("calories_out"),
            "sleep_hours": night.get("hours_asleep"),
        })

    trusted_sessions = [row for row in sessions if not row["quality_flags"]]
    training_sessions = [row for row in trusted_sessions if row["is_training"]]
    walking_sessions = [row for row in trusted_sessions if row["type"] == "Walking"]
    total_duration = sum(row["duration_min"] for row in trusted_sessions)
    training_duration = sum(row["duration_min"] for row in training_sessions)
    exercise_calories = sum(row["calories"] for row in trusted_sessions)
    active_zone_minutes = sum(row["active_zone_minutes"] for row in trusted_sessions)
    distance_km = sum(row["distance_km"] for row in trusted_sessions)
    total_steps = sum(row["steps"] or 0 for row in current_activity)
    avg_steps = _mean([row["steps"] for row in current_activity])
    total_calories = sum(row["calories_out"] or 0 for row in current_activity)
    previous_avg_steps = _mean([row["steps"] for row in previous_activity])
    previous_total_calories = sum(row["calories_out"] or 0 for row in previous_activity)

    sleep_hours = [row["hours_asleep"] for row in current_sleep if row["hours_asleep"] is not None]
    previous_sleep_hours = [
        row["hours_asleep"] for row in previous_sleep if row["hours_asleep"] is not None
    ]
    efficiencies = [
        row["efficiency_pct"] for row in current_sleep if row["efficiency_pct"] is not None
    ]
    previous_efficiencies = [
        row["efficiency_pct"] for row in previous_sleep
        if row["efficiency_pct"] is not None
    ]
    bedtimes = [
        value for value in (_clock_minutes(row.get("bedtime")) for row in current_sleep)
        if value is not None
    ]
    avg_sleep = _mean(sleep_hours)
    previous_avg_sleep = _mean(previous_sleep_hours)
    avg_efficiency = _mean(efficiencies)
    previous_avg_efficiency = _mean(previous_efficiencies)
    sleep_debt = round(sum(max(0, 7.5 - value) for value in sleep_hours), 1)
    bedtime_consistency = (
        round(statistics.pstdev(bedtimes)) if len(bedtimes) >= 2 else None
    )

    def values(rows: list[dict]) -> list[float]:
        return [row["value"] for row in rows if row.get("value") is not None]

    avg_hrv = _mean(values(current_hrv))
    prior_hrv = _mean(values(previous_hrv))
    avg_rhr = _mean(values(current_rhr))
    prior_rhr = _mean(values(previous_rhr))
    avg_oxygen = _mean(values(current_oxygen))
    prior_oxygen = _mean(values(previous_oxygen))
    avg_resp = _mean(values(current_resp))
    prior_resp = _mean(values(previous_resp))
    oxygen_lows = [row["lower"] for row in current_oxygen if row.get("lower") is not None]

    hrv_by_day = {row["day"]: row["value"] for row in current_hrv}
    rhr_by_day = {row["day"]: row["value"] for row in current_rhr}
    strain_days = []
    if prior_hrv and prior_rhr is not None:
        for day in sorted(set(hrv_by_day) & set(rhr_by_day)):
            if hrv_by_day[day] < prior_hrv * 0.9 and rhr_by_day[day] > prior_rhr + 3:
                strain_days.append(day)

    most_active = max(
        (row for row in current_activity if row.get("steps") is not None),
        key=lambda row: row["steps"],
        default=None,
    )
    standout = max(
        trusted_sessions,
        key=lambda row: row["active_zone_minutes"] * 5 + row["calories"],
        default=None,
    )
    type_counts = Counter(row["type"] for row in trusted_sessions)
    quality_flags = [
        {"session": row["name"], "day": row["day"], "flags": row["quality_flags"]}
        for row in sessions if row["quality_flags"]
    ]

    steps_change = (
        _pct_change(avg_steps, previous_avg_steps)
        if len(current_activity) >= 4 and len(previous_activity) >= 4 else None
    )
    current_avg_calories = _mean([row["calories_out"] for row in current_activity])
    previous_avg_calories = _mean([row["calories_out"] for row in previous_activity])
    calories_change = (
        _pct_change(current_avg_calories, previous_avg_calories)
        if len(current_activity) >= 4 and len(previous_activity) >= 4 else None
    )
    sleep_change = (
        round(avg_sleep - previous_avg_sleep, 2)
        if (
            avg_sleep is not None
            and previous_avg_sleep is not None
            and len(current_sleep) >= 3
            and len(previous_sleep) >= 3
        )
        else None
    )
    efficiency_change = (
        round(avg_efficiency - previous_avg_efficiency, 1)
        if (
            avg_efficiency is not None
            and previous_avg_efficiency is not None
            and len(efficiencies) >= 3
            and len(previous_efficiencies) >= 3
        )
        else None
    )
    hrv_change = (
        _pct_change(avg_hrv, prior_hrv)
        if len(current_hrv) >= 3 and len(previous_hrv) >= 3 else None
    )
    rhr_change = (
        round(avg_rhr - prior_rhr, 1)
        if (
            avg_rhr is not None
            and prior_rhr is not None
            and len(current_rhr) >= 3
            and len(previous_rhr) >= 3
        )
        else None
    )
    oxygen_change = (
        round(avg_oxygen - prior_oxygen, 1)
        if (
            avg_oxygen is not None
            and prior_oxygen is not None
            and len(current_oxygen) >= 3
            and len(previous_oxygen) >= 3
        )
        else None
    )
    resp_change = (
        round(avg_resp - prior_resp, 1)
        if (
            avg_resp is not None
            and prior_resp is not None
            and len(current_resp) >= 3
            and len(previous_resp) >= 3
        )
        else None
    )

    insights = [
        value for value in (
            (
                f"Most active day: {date.fromisoformat(most_active['day']).strftime('%A')} "
                f"with {most_active['steps']:,} steps."
                if most_active else None
            ),
            (
                f"Standout session: {standout['name']} · {standout['duration_min']} min · "
                f"{standout['calories']:,} kcal · {standout['active_zone_minutes']} zone min."
                if standout else None
            ),
            _trend_text("Average steps", steps_change, "%", "up"),
            _trend_text("Sleep duration", sleep_change, "h", "up"),
            _trend_text("HRV", hrv_change, "%", "up"),
            _trend_text("Resting heart rate", rhr_change, " bpm", "down"),
        )
        if value
    ]

    priorities = []
    if avg_sleep is not None and avg_sleep < 7:
        priorities.append(
            f"Recover sleep volume: average was {avg_sleep:.2f}h with {sleep_debt:.1f}h cumulative debt."
        )
    if bedtime_consistency is not None and bedtime_consistency > 60:
        priorities.append(
            f"Tighten sleep timing: bedtime varied by about {bedtime_consistency} minutes."
        )
    if avg_steps is not None and avg_steps < 8_000:
        priorities.append(
            f"Lift baseline movement from {avg_steps:,.0f} toward 8,000+ daily steps."
        )
    if not training_sessions:
        priorities.append("Schedule at least two intentional training sessions next week.")
    elif active_zone_minutes > 180 and strain_days:
        priorities.append(
            "Keep the next hard session flexible; training load and recovery-strain flags overlapped."
        )
    if len(current_oxygen) < 4:
        priorities.append("Improve overnight wear consistency to strengthen oxygen trend coverage.")
    if not priorities:
        priorities.append("Maintain this training and recovery rhythm; progress load gradually.")

    return {
        "week": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}",
        },
        "training": {
            "sessions": len(sessions),
            "trusted_sessions": len(trusted_sessions),
            "training_sessions": len(training_sessions),
            "walking_sessions": len(walking_sessions),
            "duration_min": round(total_duration),
            "training_duration_min": round(training_duration),
            "exercise_calories": exercise_calories,
            "distance_km": round(distance_km, 2),
            "active_zone_minutes": active_zone_minutes,
            "types": dict(type_counts),
            "standout": standout,
            "quality_flags": quality_flags,
        },
        "activity": {
            "total_steps": total_steps,
            "avg_steps": round(avg_steps) if avg_steps is not None else None,
            "total_calories_out": total_calories,
            "days": len(current_activity),
            "steps_change_pct": steps_change,
            "calories_change_pct": calories_change,
            "most_active": most_active,
        },
        "sleep": {
            "nights": len(current_sleep),
            "avg_hours": round(avg_sleep, 2) if avg_sleep is not None else None,
            "avg_efficiency_pct": (
                round(avg_efficiency, 1) if avg_efficiency is not None else None
            ),
            "efficiency_change_points": efficiency_change,
            "sleep_debt_hours": sleep_debt,
            "bedtime_consistency_min": bedtime_consistency,
            "change_hours": sleep_change,
        },
        "recovery": {
            "avg_hrv_ms": round(avg_hrv, 1) if avg_hrv is not None else None,
            "hrv_change_pct": hrv_change,
            "avg_resting_hr_bpm": round(avg_rhr, 1) if avg_rhr is not None else None,
            "resting_hr_change_bpm": rhr_change,
            "avg_spo2_pct": round(avg_oxygen, 1) if avg_oxygen is not None else None,
            "spo2_change_points": oxygen_change,
            "lowest_spo2_bound_pct": round(min(oxygen_lows), 1) if oxygen_lows else None,
            "avg_respiratory_rate": round(avg_resp, 1) if avg_resp is not None else None,
            "respiratory_change": resp_change,
            "strain_flag_days": strain_days,
        },
        "daily": days,
        "sessions": sessions,
        "insights": insights[:6],
        "priorities": priorities[:4],
        "coverage": {
            "activity_days": len(current_activity),
            "sleep_nights": len(current_sleep),
            "hrv_days": len(current_hrv),
            "resting_hr_days": len(current_rhr),
            "oxygen_days": len(current_oxygen),
            "respiratory_days": len(current_resp),
        },
    }


def build(start: date, end: date) -> dict:
    """Read local health databases and build the completed weekly catalog."""
    return summarize(
        start,
        end,
        activity=_activity(start, end),
        sessions=_sessions(start, end),
        sleep=_sleep(start, end),
        hrv=_daily_values(
            "dailyHeartRateVariability",
            "averageHeartRateVariabilityMilliseconds",
            start,
            end,
        ),
        resting_hr=_daily_values(
            "dailyRestingHeartRate",
            "beatsPerMinute",
            start,
            end,
        ),
        oxygen=_oxygen(start, end),
        respiratory=_daily_values(
            "dailyRespiratoryRate",
            "breathsPerMinute",
            start,
            end,
        ),
    )


def ai_payload(catalog: dict) -> dict:
    """Return the shallow numerical subset allowed to reach an AI provider."""
    training = catalog["training"]
    activity = catalog["activity"]
    sleep = catalog["sleep"]
    recovery = catalog["recovery"]
    return {
        "report_type": "weekly",
        "training_sessions": training["training_sessions"],
        "walking_sessions": training["walking_sessions"],
        "workout_minutes": training["training_duration_min"],
        "exercise_calories": training["exercise_calories"],
        "active_zone_minutes": training["active_zone_minutes"],
        "distance_km": training["distance_km"],
        "average_steps": activity["avg_steps"],
        "total_steps": activity["total_steps"],
        "total_calories_out": activity["total_calories_out"],
        "steps_change_pct": activity["steps_change_pct"],
        "average_sleep_hours": sleep["avg_hours"],
        "sleep_debt_hours": sleep["sleep_debt_hours"],
        "sleep_change_hours": sleep["change_hours"],
        "average_hrv_ms": recovery["avg_hrv_ms"],
        "hrv_change_pct": recovery["hrv_change_pct"],
        "average_resting_hr_bpm": recovery["avg_resting_hr_bpm"],
        "resting_hr_change_bpm": recovery["resting_hr_change_bpm"],
        "average_spo2_pct": recovery["avg_spo2_pct"],
        "average_respiratory_rate": recovery["avg_respiratory_rate"],
        "strain_flag_days": len(recovery["strain_flag_days"]),
    }
