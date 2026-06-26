"""Dashboard snapshot — one JSON payload powering the live frontend.

The web dashboard polls a single endpoint and renders metric cards from the
result. This module assembles that snapshot by fusing every analytics module
(readiness, recomp, weight/sleep/activity insights, protein, HR zones) plus a
couple of live wearable reads (current heart rate, today's steps). It is
read-only and defensive: any one section failing returns ``{"error": ...}`` for
that card instead of breaking the whole page.

Metric-first by design — the payload is numbers and short trend arrays (for
sparklines), not prose.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fitlit import config, insights, protein, readiness, recomp, zones
from fitlit.journal import PACIFIC


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001 — a broken card must not sink the page
        return {"error": str(exc)}


def _heart(db_minutes: int = 30) -> dict:
    """Latest bpm + a short per-minute series for the live sparkline."""
    path = config.DB_DIR / "heart.db"
    if not path.exists():
        return {"error": "no heart data"}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        latest = conn.execute(
            "SELECT json_extract(data_json,'$.sampleTime.physicalTime') t, beats_per_minute b "
            "FROM heartRate ORDER BY json_extract(data_json,'$.sampleTime.physicalTime') DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return {"error": "no heart samples"}
        cutoff = (datetime.fromisoformat(latest[0].replace("Z", "+00:00"))
                  - timedelta(minutes=db_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute(
            "SELECT substr(json_extract(data_json,'$.sampleTime.physicalTime'),1,16) m, "
            "AVG(beats_per_minute) b FROM heartRate "
            "WHERE json_extract(data_json,'$.sampleTime.physicalTime') >= ? GROUP BY m ORDER BY m",
            (cutoff,)).fetchall()
        series = [round(r[1]) for r in rows if r[1] is not None]
        as_of_local = datetime.fromisoformat(latest[0].replace("Z", "+00:00")).astimezone(PACIFIC)
        return {
            "live_bpm": latest[1],
            "as_of": as_of_local.strftime("%Y-%m-%d %H:%M"),
            "series": series[-60:],
            "min": min(series) if series else None,
            "max": max(series) if series else None,
        }
    finally:
        conn.close()


def _resting_hr() -> dict:
    series = readiness._resting_hr_series(14)  # newest first, deduped per day
    return {"latest": round(series[0]) if series else None,
            "series": [round(x) for x in reversed(series)]}


def snapshot() -> dict:
    """Full metric snapshot for the dashboard. All times Pacific."""
    now = datetime.now(PACIFIC)
    weight = _safe(insights.weight_trend, 30)
    sleep = _safe(insights.sleep_trend, 14)
    activity = _safe(insights.activity_summary, 10)

    # last-night sleep convenience
    last_night = None
    if isinstance(sleep, dict) and sleep.get("nights"):
        last_night = sleep["nights"][-1]

    # today's steps from the activity series
    steps_today = None
    if isinstance(activity, dict):
        today = now.strftime("%Y-%m-%d")
        steps_today = next((s["steps"] for s in activity.get("series", [])
                            if s["day"] == today), None)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "tz": "America/Los_Angeles",
        "readiness": _safe(readiness.score),
        "recomp": _safe(recomp.plan),
        "recomp_progress": _safe(recomp.progress),
        "weight": weight,
        "sleep": {"last_night": last_night,
                  "avg_hours": sleep.get("avg_hours_asleep") if isinstance(sleep, dict) else None,
                  "avg_eff": sleep.get("avg_efficiency_pct") if isinstance(sleep, dict) else None,
                  "nights": sleep.get("nights", []) if isinstance(sleep, dict) else []},
        "activity": {"avg_steps": activity.get("avg_steps") if isinstance(activity, dict) else None,
                     "steps_today": steps_today,
                     "series": activity.get("series", []) if isinstance(activity, dict) else []},
        "heart": _safe(_heart),
        "resting_hr": _safe(_resting_hr),
        "protein": _safe(protein.daily_status),
        "zones": _safe(zones.zones),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2, default=str))
