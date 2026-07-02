"""Per-component data API — each dashboard component pulls its own rich slice.

The redesigned dashboard is component-driven: every visual (hypnogram, intraday
heart rate, zone donut, hourly steps, vitals, weight scatter, sleep history)
fetches its *own* endpoint on its *own* cadence instead of sharing one snapshot.
This module is the data layer behind that — one function per component, each
returning a compact, chart-ready payload.

All reads are read-only against the fetcher DBs (+ the journal). Times are
Pacific (the user's tz). Heavy intraday reads dedup to one value per timestamp
before aggregating (the live tables can hold repeats). Defensive throughout —
a missing table returns an empty series, never an exception.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from fitlit import config, journal, zones
from fitlit.journal import PACIFIC

_PT = "-7 hours"


def _ro(db: str) -> sqlite3.Connection | None:
    path = config.DB_DIR / f"{db}.db"
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _today() -> str:
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _daily_series(table: str, path: str, limit: int = 21) -> list[dict]:
    """Deduped per-day series from a daily_summaries table, oldest→newest."""
    c = _ro("daily_summaries")
    if not c:
        return []
    try:
        rows = c.execute(f"""
            SELECT printf('%04d-%02d-%02d',
                json_extract(data_json,'$.date.year'),
                json_extract(data_json,'$.date.month'),
                json_extract(data_json,'$.date.day')) day,
                json_extract(data_json, ?) v
            FROM "{table}"
            GROUP BY day ORDER BY day DESC LIMIT ?
        """, (path, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()
    out = [{"day": r["day"], "v": r["v"]} for r in reversed(rows) if r["v"] is not None]
    return out


# --------------------------------------------------------------------------- #
def hypnogram() -> dict:
    """Last night's stage-by-stage timeline (for a hypnogram chart)."""
    c = _ro("sleep")
    if not c:
        return {"stages": []}
    try:
        row = c.execute("SELECT data_json FROM sleep ORDER BY start_time DESC LIMIT 1").fetchone()
    finally:
        c.close()
    if not row:
        return {"stages": []}
    data = json.loads(row[0]) or {}
    stages = data.get("stages", [])
    if not stages:
        return {"stages": []}

    def to_local(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PACIFIC)

    start = to_local(stages[0]["startTime"])
    end = to_local(stages[-1]["endTime"])
    total = (end - start).total_seconds() / 60
    segs = []
    for s in stages:
        st, en = to_local(s["startTime"]), to_local(s["endTime"])
        segs.append({
            "type": (s.get("type") or "").lower(),
            "offset_min": round((st - start).total_seconds() / 60, 1),
            "dur_min": round((en - st).total_seconds() / 60, 1),
        })
    summ = data.get("summary", {})
    return {
        "bedtime": start.strftime("%H:%M"),
        "wake": end.strftime("%H:%M"),
        "total_min": round(total),
        "asleep_min": int(_num(summ.get("minutesAsleep"))),
        "efficiency": round(100 * _num(summ.get("minutesAsleep")) / _num(summ.get("minutesInSleepPeriod"), 1), 1),
        "stages": segs,
    }


def heart_today(bucket_min: int = 5) -> dict:
    """Intraday heart rate for the Pacific day, bucketed, with zone bounds."""
    from datetime import timezone
    c = _ro("heart")
    if not c:
        return {"points": [], "zones": []}
    now = datetime.now(PACIFIC)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    s_iso = day0.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e_iso = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = c.execute("""
            SELECT json_extract(data_json,'$.sampleTime.physicalTime') pt, beats_per_minute b
            FROM heartRate
            WHERE json_extract(data_json,'$.sampleTime.physicalTime') >= ?
              AND json_extract(data_json,'$.sampleTime.physicalTime') <  ?
        """, (s_iso, e_iso)).fetchall()
    finally:
        c.close()
    from collections import defaultdict
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        if not r["pt"] or r["b"] is None:
            continue
        dt = datetime.fromisoformat(r["pt"].replace("Z", "+00:00")).astimezone(PACIFIC)
        key = dt.replace(minute=(dt.minute // bucket_min) * bucket_min, second=0, microsecond=0)
        buckets[key.strftime("%H:%M")].append(r["b"])
    points = [{"t": k, "bpm": round(sum(v) / len(v))} for k, v in sorted(buckets.items())]
    z = zones.zones()
    zdefs = [{"zone": x["zone"], "low": x["bpm_low"], "high": x["bpm_high"]}
             for x in z.get("zones", [])]
    return {"points": points, "zones": zdefs, "resting_hr": z.get("resting_hr"),
            "max_hr": z.get("max_hr")}


def zones_today() -> dict:
    """Minutes in each HR zone for the Pacific day (dedup per minute)."""
    hr = heart_today(bucket_min=1)
    zdefs = hr["zones"]
    if not zdefs or not hr["points"]:
        return {"zones": [], "total_min": 0}

    def classify(bpm):
        for z in zdefs:
            if z["low"] <= bpm <= z["high"]:
                return z["zone"]
        return zdefs[0]["zone"] if bpm < zdefs[0]["low"] else zdefs[-1]["zone"]

    counts = {z["zone"]: 0 for z in zdefs}
    for p in hr["points"]:
        counts[classify(p["bpm"])] += 1
    total = sum(counts.values()) or 1
    return {
        "total_min": sum(counts.values()),
        "zones": [{"zone": z["zone"], "minutes": counts[z["zone"]],
                   "pct": round(100 * counts[z["zone"]] / total, 1),
                   "range": f'{z["low"]}–{z["high"]}'} for z in zdefs],
    }


def hourly_steps() -> dict:
    """Steps per hour for the Pacific day (dedup per timestamp)."""
    c = _ro("live_activity")
    if not c:
        return {"hours": []}
    try:
        rows = c.execute(f"""
            SELECT CAST(strftime('%H', datetime(start_time,'{_PT}')) AS INT) hr, SUM(cnt) steps
            FROM (SELECT start_time, MAX(count) cnt FROM steps
                  WHERE date(start_time,'{_PT}') = ? GROUP BY start_time)
            GROUP BY hr ORDER BY hr
        """, (_today(),)).fetchall()
    except sqlite3.OperationalError:
        return {"hours": []}
    finally:
        c.close()
    by = {r["hr"]: r["steps"] for r in rows}
    hours = [{"hour": h, "steps": by.get(h, 0)} for h in range(24)]
    return {"hours": hours, "total": sum(by.values())}


def vitals() -> dict:
    """Latest + short trend for SpO2, respiratory rate, HRV, resting HR, VO2max."""
    spo2 = _daily_series("dailyOxygenSaturation", "$.averagePercentage")
    resp = _daily_series("dailyRespiratoryRate", "$.breathsPerMinute")
    hrv = _daily_series("dailyHeartRateVariability", "$.averageHeartRateVariabilityMilliseconds")
    rhr = _daily_series("dailyRestingHeartRate", "$.beatsPerMinute")
    vo2 = _daily_series("dailyVo2Max", "$.value")

    def pack(series, unit, digits=0):
        vals = [_num(x["v"]) for x in series]
        latest = vals[-1] if vals else None
        return {"latest": round(latest, digits) if latest is not None else None,
                "unit": unit, "series": [round(v, digits) for v in vals]}
    return {
        "spo2": pack(spo2, "%", 1),
        "respiratory": pack(resp, "br/min", 1),
        "hrv": pack(hrv, "ms", 0),
        "resting_hr": pack(rhr, "bpm", 0),
        "vo2max": pack(vo2, "", 1),
    }


def weight_scatter() -> dict:
    """Every weigh-in (fasted flagged) + 7-day moving average + target line."""
    from fitlit import recomp
    rows = sorted(journal.recent_weights(120), key=lambda r: r["date"])
    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["date"]] = r  # last per day
    pts = list(seen.values())
    points, window, avg = [], [], []
    for r in pts:
        fasted = "fasted" in (r.get("conditions") or "").lower() and "not fasted" not in (r.get("conditions") or "").lower()
        points.append({"date": r["date"], "lb": r["weight_lb"], "fasted": fasted})
        window.append(r["weight_lb"]); window[:] = window[-7:]
        avg.append({"date": r["date"], "lb": round(sum(window) / len(window), 1)})
    plan = recomp.plan()
    return {"points": points, "avg7": avg,
            "target": plan.get("target", {}).get("weight_lb")}


def sleep_history(nights: int = 14) -> dict:
    """Per-night stage minutes (deep/rem/light/awake) for stacked bars."""
    c = _ro("sleep")
    if not c:
        return {"nights": []}
    since = (datetime.now(PACIFIC) - timedelta(days=nights)).strftime("%Y-%m-%d")
    try:
        rows = c.execute(f"""
            SELECT date(start_time,'{_PT}') night, data_json
            FROM sleep WHERE date(start_time,'{_PT}') >= ? ORDER BY start_time
        """, (since,)).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        summ = (json.loads(r["data_json"]) or {}).get("summary", {})
        st = {s.get("type", "").lower(): int(_num(s.get("minutes")))
              for s in summ.get("stagesSummary", [])}
        out.append({"night": r["night"], "deep": st.get("deep", 0), "rem": st.get("rem", 0),
                    "light": st.get("light", 0), "awake": st.get("awake", 0),
                    "asleep": int(_num(summ.get("minutesAsleep")))})
    return {"nights": out}


# Dispatch table for /api/comp/{name}
REGISTRY = {
    "hypnogram": hypnogram,
    "heart_today": heart_today,
    "zones_today": zones_today,
    "hourly_steps": hourly_steps,
    "vitals": vitals,
    "weight_scatter": weight_scatter,
    "sleep_history": sleep_history,
}


def get(name: str) -> dict:
    fn = REGISTRY.get(name)
    if not fn:
        raise KeyError(name)
    return fn()


if __name__ == "__main__":
    import sys
    print(json.dumps(get(sys.argv[1] if len(sys.argv) > 1 else "vitals"), indent=2, default=str))
