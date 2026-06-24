"""Personalized heart-rate zones + time-in-zone analysis.

The user is adding 25 min of daily Zone-2 cardio as a fat-loss lever, and the
coaching rule is *keep it genuinely easy*. "Zone 2" is meaningless without the
user's own numbers, so this module computes their zones from real data and lets
them check whether a given session actually stayed in the target zone.

Two methods, because they diverge and the user likes seeing the numbers:

* **%HRmax** — simple: zone bounds as a fraction of max HR.
* **Karvonen (Heart-Rate Reserve)** — uses resting HR too
  (``target = pct × (HRmax − HRrest) + HRrest``); more personalized, and the
  better choice for prescribing easy cardio. Used as the primary.

Inputs come straight from FitLit: max HR is the observed peak in ``heart.db``
(~197), resting HR the latest ``dailyRestingHeartRate`` (~58). Override either if
you do a proper max-HR test. Read-only + stdlib.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fitlit import config
from fitlit.journal import PACIFIC

# 5-zone model, as fractions. (lower, upper, label, purpose)
_ZONE_MODEL = [
    (0.50, 0.60, "Zone 1", "Recovery / warm-up"),
    (0.60, 0.70, "Zone 2", "Fat-burning aerobic base — the daily-cardio target"),
    (0.70, 0.80, "Zone 3", "Aerobic / tempo"),
    (0.80, 0.90, "Zone 4", "Threshold / hard"),
    (0.90, 1.01, "Zone 5", "VO2max / maximal"),
]


def _query(db_name: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    path = config.DB_DIR / f"{db_name}.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def observed_max_hr() -> int | None:
    rows = _query("heart", "SELECT MAX(beats_per_minute) m FROM heartRate")
    return int(rows[0]["m"]) if rows and rows[0]["m"] is not None else None


def latest_resting_hr() -> int | None:
    rows = _query("daily_summaries", """
        SELECT json_extract(data_json,'$.beatsPerMinute') bpm
        FROM dailyRestingHeartRate
        ORDER BY printf('%04d-%02d-%02d',
            json_extract(data_json,'$.date.year'),
            json_extract(data_json,'$.date.month'),
            json_extract(data_json,'$.date.day')) DESC LIMIT 1
    """)
    return int(rows[0]["bpm"]) if rows and rows[0]["bpm"] is not None else None


def _bounds(lo: float, hi: float, max_hr: int, rest_hr: int | None, method: str) -> tuple[int, int]:
    if method == "karvonen" and rest_hr is not None:
        reserve = max_hr - rest_hr
        return round(lo * reserve + rest_hr), round(hi * reserve + rest_hr)
    return round(lo * max_hr), round(hi * max_hr)


def zones(
    *,
    max_hr: int | None = None,
    resting_hr: int | None = None,
    method: str = "karvonen",
) -> dict:
    """Compute the 5 HR zones (bpm ranges) from max + resting HR."""
    max_hr = max_hr or observed_max_hr()
    if not max_hr:
        return {"error": "no heart-rate data yet to derive max HR"}
    resting_hr = resting_hr if resting_hr is not None else latest_resting_hr()

    zlist = []
    for lo, hi, label, purpose in _ZONE_MODEL:
        bpm_lo, bpm_hi = _bounds(lo, hi, max_hr, resting_hr, method)
        zlist.append({"zone": label, "bpm_low": bpm_lo, "bpm_high": bpm_hi,
                      "purpose": purpose})
    return {
        "method": method,
        "max_hr": max_hr,
        "max_hr_source": "observed peak in heart.db (not a tested max — do a field test for precision)",
        "resting_hr": resting_hr,
        "zones": zlist,
        "daily_cardio_target": next(z for z in zlist if z["zone"] == "Zone 2"),
    }


def _classify(bpm: float, zlist: list[dict]) -> str:
    for z in zlist:
        if z["bpm_low"] <= bpm <= z["bpm_high"]:
            return z["zone"]
    return "Zone 1" if bpm < zlist[0]["bpm_low"] else "Zone 5"


def time_in_zone(
    start: datetime,
    end: datetime,
    *,
    method: str = "karvonen",
) -> dict:
    """Bucket every heart-rate sample in ``[start, end)`` into zones.

    Reads the live ``heartRate`` samples for the window (deduping the many rows
    per timestamp), classifies each into a zone, and reports minutes + % per zone
    plus avg/max bpm — so the user can confirm a "Zone-2" session really stayed
    easy, or see the zone split of a hard run.
    """
    z = zones(method=method)
    if "error" in z:
        return z
    zlist = z["zones"]

    # heartRate timestamps live in data_json.sampleTime.physicalTime (UTC), sampled
    # every few seconds. Collapse to ONE representative bpm per clock-minute (avg)
    # so each bucket is a real minute — otherwise sub-minute sampling inflates the
    # "minutes" by ~25x. Track the true peak separately from the raw samples.
    rows = _query("heart", """
        SELECT substr(pt,1,16) minute, AVG(bpm) bpm FROM (
            SELECT json_extract(data_json,'$.sampleTime.physicalTime') pt,
                   beats_per_minute bpm
            FROM heartRate
            WHERE json_extract(data_json,'$.sampleTime.physicalTime') >= ?
              AND json_extract(data_json,'$.sampleTime.physicalTime') <  ?
        ) GROUP BY minute ORDER BY minute
    """, (_rfc3339(start), _rfc3339(end)))

    per_minute = [float(r["bpm"]) for r in rows if r["bpm"] is not None]
    if not per_minute:
        return {"window": {"start": _rfc3339(start), "end": _rfc3339(end)},
                "minutes": 0, "note": "no heart-rate samples in this window"}
    peak_rows = _query("heart", """
        SELECT MAX(beats_per_minute) m FROM heartRate
        WHERE json_extract(data_json,'$.sampleTime.physicalTime') >= ?
          AND json_extract(data_json,'$.sampleTime.physicalTime') <  ?
    """, (_rfc3339(start), _rfc3339(end)))

    # Each per-minute value = one minute in its zone.
    counts: dict[str, int] = {z["zone"]: 0 for z in zlist}
    for bpm in per_minute:
        counts[_classify(bpm, zlist)] += 1
    total = len(per_minute)
    breakdown = [{"zone": zz["zone"], "minutes": counts[zz["zone"]],
                  "pct": round(100 * counts[zz["zone"]] / total, 1),
                  "bpm_range": f"{zz['bpm_low']}-{zz['bpm_high']}"} for zz in zlist]

    return {
        "window": {"start": _rfc3339(start), "end": _rfc3339(end)},
        "method": method,
        "minutes_with_data": total,
        "avg_bpm": round(sum(per_minute) / total),
        "max_bpm": round(peak_rows[0]["m"]) if peak_rows and peak_rows[0]["m"] else None,
        "by_zone": breakdown,
    }


def last_hours(hours: int = 2, *, method: str = "karvonen") -> dict:
    """Convenience: time-in-zone for the trailing ``hours`` (e.g. a workout)."""
    end = datetime.now(timezone.utc)
    return time_in_zone(end - timedelta(hours=hours), end, method=method)


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    import json
    print(json.dumps(zones(), indent=2))
