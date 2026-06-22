"""Cross-domain analytics — turn the raw FitLit databases into coaching signal.

The fetcher databases store *raw* time-series; the journal stores *user* logs.
This module is the read-only analytics layer on top of both: weight trends,
sleep trends, activity/energy summaries, and a single daily briefing that fuses
them. The chat coach and the HTTP API call these instead of hand-rolling SQL.

Two correctness rules this module bakes in so callers don't have to:

* **Read-only.** Every fetcher DB is opened ``mode=ro`` so we never block the
  24/7 writers. Missing files / tables degrade to empty results, never errors.
* **Dedupe before aggregating.** The live tables contain *many* rows per
  timestamp — each fetch cycle re-pulls history and the API hands back
  overlapping points with distinct ids (e.g. one day of ``steps`` is ~300k rows
  across only ~340 distinct minutes). Summing raw rows overcounts by ~1000x, so
  every aggregate first collapses to one value per ``start_time``
  (``GROUP BY start_time``, taking the max) before summing.

Times: wearable data is stored UTC; the user is Pacific, so calendar-day
grouping shifts by ``PACIFIC`` (see fitlit.journal). Standard library only.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from fitlit import config, journal
from fitlit.journal import PACIFIC

# Pacific offset as the SQLite modifier used for day-bucketing UTC timestamps.
_PT = "-7 hours"


# --------------------------------------------------------------------------- #
# Read-only query helper
# --------------------------------------------------------------------------- #
def _query(db_name: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Run a read-only query against ``data/db/<db_name>.db``.

    Returns [] if the database file or a referenced table doesn't exist yet —
    an empty metric is a normal state (no data captured), not an error.
    """
    path = config.DB_DIR / f"{db_name}.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # no such table
    finally:
        conn.close()


def _pacific_today() -> str:
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Weight trend (from the user journal)
# --------------------------------------------------------------------------- #
def weight_trend(days: int = 30, *, fasted_only: bool = False) -> dict:
    """Weight series + a 7-day moving average from the journal.

    ``fasted_only`` restricts to clean fasted readings (the only ones that
    compare apples-to-apples). Returns per-day points (one per day, latest wins)
    and the moving average so a caller can see signal through the water noise.
    """
    rows = journal.recent_weights(limit=max(days * 3, 60))
    points: dict[str, dict] = {}
    for r in rows:  # rows are newest-first; keep the first (latest) per day
        cond = (r.get("conditions") or "").lower()
        if fasted_only and not ("fasted" in cond and "not fasted" not in cond):
            continue
        points.setdefault(r["date"], r)
    series = sorted(points.values(), key=lambda r: r["date"])

    ma: list[dict] = []
    window: list[float] = []
    for r in series:
        window.append(r["weight_lb"])
        window[:] = window[-7:]
        ma.append({"date": r["date"], "weight_lb": r["weight_lb"],
                   "avg7_lb": round(sum(window) / len(window), 1),
                   "conditions": r.get("conditions")})

    delta = None
    if len(ma) >= 2:
        delta = round(ma[-1]["avg7_lb"] - ma[0]["avg7_lb"], 1)
    return {
        "days": days,
        "fasted_only": fasted_only,
        "n_readings": len(series),
        "latest_lb": series[-1]["weight_lb"] if series else None,
        "avg7_lb": ma[-1]["avg7_lb"] if ma else None,
        "trend_lb": delta,
        "series": ma,
    }


# --------------------------------------------------------------------------- #
# Sleep trend (from sleep.db)
# --------------------------------------------------------------------------- #
def sleep_trend(days: int = 14) -> dict:
    """Per-night duration / efficiency / REM / deep from the wearable sleep data."""
    since = (datetime.now(PACIFIC) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = _query("sleep", f"""
        SELECT date(start_time,'{_PT}') night,
               strftime('%H:%M', datetime(start_time,'{_PT}')) bedtime,
               strftime('%H:%M', datetime(end_time,'{_PT}')) wake,
               json_extract(data_json,'$.summary.minutesAsleep')        asleep,
               json_extract(data_json,'$.summary.minutesInSleepPeriod')  in_bed,
               json_extract(data_json,'$.summary.minutesAwake')          awake
        FROM sleep
        WHERE date(start_time,'{_PT}') >= ?
        ORDER BY start_time
    """, (since,))

    nights = []
    for r in rows:
        in_bed = _num(r["in_bed"])
        asleep = _num(r["asleep"])
        eff = round(100.0 * asleep / in_bed, 1) if in_bed else None
        nights.append({
            "night": r["night"], "bedtime": r["bedtime"], "wake": r["wake"],
            "hours_asleep": round(asleep / 60.0, 2) if asleep else None,
            "efficiency_pct": eff, "awake_min": _num(r["awake"]),
        })
    avg_hours = _avg([n["hours_asleep"] for n in nights])
    avg_eff = _avg([n["efficiency_pct"] for n in nights])
    return {
        "days": days, "n_nights": len(nights),
        "avg_hours_asleep": round(avg_hours, 2) if avg_hours is not None else None,
        "avg_efficiency_pct": round(avg_eff, 1) if avg_eff is not None else None,
        "nights": nights,
    }


# --------------------------------------------------------------------------- #
# Activity + energy (live_activity.db, dedup-safe)
# --------------------------------------------------------------------------- #
def activity_summary(days: int = 7) -> dict:
    """Daily steps (dedup-safe) + calories-out from the dailyRollup table."""
    since = (datetime.now(PACIFIC) - timedelta(days=days)).strftime("%Y-%m-%d")
    # Collapse duplicate points per minute (max), THEN sum per Pacific day.
    steps = _query("live_activity", f"""
        SELECT day, SUM(c) steps FROM (
            SELECT date(start_time,'{_PT}') day, start_time, MAX(count) c
            FROM steps WHERE date(start_time,'{_PT}') >= ?
            GROUP BY start_time
        ) GROUP BY day ORDER BY day
    """, (since,))
    step_by_day = {r["day"]: r["steps"] for r in steps}

    # totalCalories is stored via dailyRollup; name ends in the YYYY-MM-DD day.
    cals = _query("live_activity", """
        SELECT substr(name,-10) day, json_extract(data_json,'$.kcalSum') kcal
        FROM totalCalories ORDER BY name
    """)
    cal_by_day = {r["day"]: round(r["kcal"]) for r in cals if r["kcal"] is not None}

    days_set = sorted(set(step_by_day) | set(cal_by_day))
    series = [{"day": d, "steps": step_by_day.get(d),
               "calories_out": cal_by_day.get(d)} for d in days_set]
    return {
        "days": days,
        "avg_steps": round(_avg(list(step_by_day.values())) or 0),
        "series": series,
    }


def energy_balance(date: str | None = None) -> dict:
    """Calories out (wearable) vs in (journal) for a day — the deficit check."""
    date = date or _pacific_today()
    out_rows = _query("live_activity", """
        SELECT json_extract(data_json,'$.kcalSum') kcal FROM totalCalories
        WHERE substr(name,-10)=?
    """, (date,))
    cal_out = round(out_rows[0]["kcal"]) if out_rows and out_rows[0]["kcal"] else None

    meal_rows = journal.recent_meals(limit=60)
    cal_in = next((m["total_kcal"] for m in meal_rows
                   if m["date"] == date and m["total_kcal"] is not None), None)

    balance = (cal_in - cal_out) if (cal_in is not None and cal_out is not None) else None
    return {
        "date": date, "calories_in": cal_in, "calories_out": cal_out,
        "balance_kcal": balance,
        "status": None if balance is None else ("deficit" if balance < 0 else "surplus"),
    }


# --------------------------------------------------------------------------- #
# Daily briefing — the one-call fusion
# --------------------------------------------------------------------------- #
def daily_briefing(date: str | None = None) -> dict:
    """A single cross-domain snapshot the coach can lead a check-in with."""
    date = date or _pacific_today()
    sleep = sleep_trend(days=1)
    last_night = sleep["nights"][-1] if sleep["nights"] else None
    return {
        "date": date,
        "weight": weight_trend(days=14),
        "last_night_sleep": last_night,
        "activity": activity_summary(days=3),
        "energy_balance": energy_balance(date),
        "journal_counts": journal.summary(),
    }


# --------------------------------------------------------------------------- #
def _avg(values: list) -> float | None:
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _num(value) -> float:
    """Coerce a value to a number, tolerating the JSON-string ints the Google
    Health summary fields use (e.g. minutesAsleep = "393"). 0 on failure."""
    if value is None:
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    import json
    print(json.dumps(daily_briefing(), indent=2, default=str))
