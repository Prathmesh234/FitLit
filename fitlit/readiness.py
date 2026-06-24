"""Daily readiness / recovery score — should you train hard today, or recover?

Hard training only builds muscle if you recover from it. This module fuses the
three recovery signals FitLit captures into a single 0–100 readiness score and a
plain recommendation, so the user can decide each morning whether to push a heavy
lift / threshold run or keep it easy:

    * **Last night's sleep** — duration + efficiency (from sleep.db).
    * **Resting heart rate** — today vs the user's trailing baseline. A jump is a
      classic sign of under-recovery, illness, or overreaching.
    * **HRV** (heart-rate variability) — today vs baseline. Higher = better
      recovered / parasympathetic; a drop means train easier.

Each sub-signal is scored 0–100 against the user's *own* baseline (trends beat
absolutes for HR/HRV), then weighted into the composite. Read-only + stdlib.
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta

from fitlit import config, insights
from fitlit.journal import PACIFIC

_PT = "-7 hours"

# Composite weights — sleep is the foundation, HRV the most sensitive day-to-day
# recovery dial, resting HR the steady confirmer.
_WEIGHTS = {"sleep": 0.45, "hrv": 0.35, "rhr": 0.20}

SLEEP_TARGET_HOURS = 7.5
SLEEP_TARGET_EFF = 92.0


def _query(db_name: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    path = config.DB_DIR / f"{db_name}.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


# --------------------------------------------------------------------------- #
# Signal readers
# --------------------------------------------------------------------------- #
def _resting_hr_series(limit: int = 14) -> list[float]:
    # Daily rows have an empty start_time column (the date lives in data_json) and
    # appear multiple times per day; dedupe to one value per calendar date, newest
    # first, so the "today vs baseline" comparison isn't biased by duplicates.
    rows = _query("daily_summaries", """
        SELECT MAX(json_extract(data_json,'$.beatsPerMinute')) bpm,
               printf('%04d-%02d-%02d',
                   json_extract(data_json,'$.date.year'),
                   json_extract(data_json,'$.date.month'),
                   json_extract(data_json,'$.date.day')) day
        FROM dailyRestingHeartRate GROUP BY day ORDER BY day DESC LIMIT ?
    """, (limit,))
    return [float(r["bpm"]) for r in rows if r["bpm"] is not None]


def _hrv_series(limit: int = 14) -> list[float]:
    rows = _query("daily_summaries", """
        SELECT MAX(json_extract(data_json,'$.averageHeartRateVariabilityMilliseconds')) ms,
               printf('%04d-%02d-%02d',
                   json_extract(data_json,'$.date.year'),
                   json_extract(data_json,'$.date.month'),
                   json_extract(data_json,'$.date.day')) day
        FROM dailyHeartRateVariability GROUP BY day ORDER BY day DESC LIMIT ?
    """, (limit,))
    return [float(r["ms"]) for r in rows if r["ms"] is not None]


def _last_night() -> dict | None:
    trend = insights.sleep_trend(days=2)
    return trend["nights"][-1] if trend["nights"] else None


# --------------------------------------------------------------------------- #
# Sub-scores (0–100)
# --------------------------------------------------------------------------- #
def _sleep_score(night: dict | None) -> tuple[float | None, dict]:
    if not night:
        return None, {"reason": "no sleep data"}
    hours = night.get("hours_asleep") or 0
    eff = night.get("efficiency_pct") or 0
    dur_score = _clamp(100 * hours / SLEEP_TARGET_HOURS)
    eff_score = _clamp(100 * eff / SLEEP_TARGET_EFF)
    score = round(0.6 * dur_score + 0.4 * eff_score, 1)
    return score, {"hours_asleep": hours, "efficiency_pct": eff}


def _rhr_score(series: list[float]) -> tuple[float | None, dict]:
    """Lower-than-baseline resting HR = recovered. Each bpm above baseline costs."""
    if len(series) < 2:
        return None, {"reason": "insufficient resting-HR history"}
    today = series[0]
    baseline = statistics.mean(series[1:])
    delta = today - baseline           # +ve = elevated = worse
    score = round(_clamp(100 - delta * 8), 1)  # ~8 pts per bpm above baseline
    return score, {"today_bpm": round(today), "baseline_bpm": round(baseline, 1),
                   "delta_bpm": round(delta, 1)}


def _hrv_score(series: list[float]) -> tuple[float | None, dict]:
    """Higher-than-baseline HRV = recovered. Scaled to % deviation from baseline."""
    if len(series) < 2:
        return None, {"reason": "insufficient HRV history"}
    today = series[0]
    baseline = statistics.mean(series[1:])
    pct = (today - baseline) / baseline * 100 if baseline else 0
    score = round(_clamp(75 + pct * 2.5), 1)  # at baseline → 75; ±1% → ±2.5 pts
    return score, {"today_ms": round(today, 1), "baseline_ms": round(baseline, 1),
                   "delta_pct": round(pct, 1)}


# --------------------------------------------------------------------------- #
# Composite
# --------------------------------------------------------------------------- #
def _recommendation(score: float) -> tuple[str, str]:
    if score >= 75:
        return "green", "Well recovered — good day for a heavy lift or threshold run."
    if score >= 55:
        return "amber", "Moderately recovered — train, but cap intensity/volume."
    return "red", "Under-recovered — keep it easy (Zone-2 walk/mobility) and prioritise sleep."


def score(date: str | None = None) -> dict:
    """Composite readiness (0–100) + recommendation from sleep, resting HR, HRV."""
    date = date or datetime.now(PACIFIC).strftime("%Y-%m-%d")
    night = _last_night()
    sleep_s, sleep_d = _sleep_score(night)
    rhr_s, rhr_d = _rhr_score(_resting_hr_series())
    hrv_s, hrv_d = _hrv_score(_hrv_series())

    parts = {"sleep": sleep_s, "hrv": hrv_s, "rhr": rhr_s}
    available = {k: v for k, v in parts.items() if v is not None}
    if not available:
        return {"date": date, "readiness": None,
                "reason": "no recovery data yet (need sleep + resting-HR/HRV history)"}

    wsum = sum(_WEIGHTS[k] for k in available)
    composite = round(sum(parts[k] * _WEIGHTS[k] for k in available) / wsum, 1)
    band, advice = _recommendation(composite)

    return {
        "date": date,
        "readiness": composite,
        "band": band,
        "recommendation": advice,
        "components": {
            "sleep": {"score": sleep_s, **sleep_d},
            "hrv": {"score": hrv_s, **hrv_d},
            "resting_hr": {"score": rhr_s, **rhr_d},
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(score(), indent=2))
