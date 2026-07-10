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
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fitlit import (
    auth,
    config,
    insights,
    journal,
    orchestrator,
    protein,
    ratelimit,
    readiness,
    recomp,
    zones,
)
from fitlit.journal import PACIFIC

_PT = "-7 hours"
_HEART_CACHE_TTL_SECONDS = 12
_heart_cache_lock = threading.Lock()
_heart_cache: dict = {"day": None, "expires": 0.0, "rows": []}


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


def _age_label(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _clock(epoch: float | None, *, seconds: bool = False) -> str | None:
    if not epoch:
        return None
    fmt = "%H:%M:%S" if seconds else "%H:%M"
    return datetime.fromtimestamp(epoch, PACIFIC).strftime(fmt)


def _mean(values: list[float | int | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


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


def _heart_rows_today() -> list[sqlite3.Row]:
    """Cache today's expensive JSON-timestamp scan across heart components."""
    now = datetime.now(PACIFIC)
    day = now.strftime("%Y-%m-%d")
    monotonic_now = time.monotonic()
    with _heart_cache_lock:
        if _heart_cache["day"] == day and monotonic_now < _heart_cache["expires"]:
            return _heart_cache["rows"]

    from datetime import timezone
    c = _ro("heart")
    if not c:
        return []
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

    with _heart_cache_lock:
        _heart_cache.update({
            "day": day,
            "expires": monotonic_now + _HEART_CACHE_TTL_SECONDS,
            "rows": rows,
        })
    return rows


def heart_today(bucket_min: int = 5) -> dict:
    """Intraday heart rate for the Pacific day, bucketed, with zone bounds."""
    rows = _heart_rows_today()
    if not rows:
        return {"points": [], "zones": []}
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


def _latest_fetch_epoch(db: str) -> float | None:
    c = _ro(db)
    if not c:
        return None
    latest: str | None = None
    try:
        tables = [
            row[0] for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = {row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}
            if "fetched_at" not in columns:
                continue
            value = c.execute(f'SELECT MAX(fetched_at) FROM "{table}"').fetchone()[0]
            if value and (latest is None or value > latest):
                latest = value
    except sqlite3.OperationalError:
        return None
    finally:
        c.close()
    if not latest:
        return None
    try:
        return datetime.fromisoformat(latest.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def operations_trace() -> dict:
    """Production-style collection health: scheduler, quota, storage, freshness."""
    now = datetime.now(PACIFIC)
    now_epoch = now.timestamp()
    schedule = orchestrator.schedule_status()
    rate = ratelimit.snapshot()
    fetchers = []

    for name, spec in config.FETCHERS.items():
        db = config.DB_DIR / f"{name}.db"
        files = [p for p in (db, db.with_name(f"{db.name}-wal")) if p.exists()]
        last_write = max((p.stat().st_mtime for p in files), default=None)
        write_age = now_epoch - last_write if last_write is not None else None
        last_fetch = _latest_fetch_epoch(name)
        fetch_age = now_epoch - last_fetch if last_fetch is not None else None
        size_mb = sum(p.stat().st_size for p in files) / 1_000_000
        live_window = max(spec.interval_seconds * 2, 180)
        if last_fetch is None:
            state = "no-data"
        elif fetch_age is not None and fetch_age <= live_window:
            state = "live"
        elif fetch_age is not None and fetch_age <= live_window * 3:
            state = "quiet"
        else:
            state = "stale"

        sched = schedule.get(name, {})
        fetchers.append({
            "name": name,
            "state": state,
            "cadence_seconds": spec.interval_seconds,
            "data_types": len(spec.data_types),
            "last_dispatch_epoch": sched.get("last_run_epoch"),
            "last_dispatch": _clock(sched.get("last_run_epoch"), seconds=True),
            "due_in_seconds": round(sched.get("due_in_seconds", 0)),
            "last_fetch": _clock(last_fetch, seconds=True),
            "fetch_age": _age_label(fetch_age),
            "last_write": _clock(last_write, seconds=True),
            "write_age": _age_label(write_age),
            "storage_mb": round(size_mb, 1),
        })

    attention = [f for f in fetchers if f["state"] in {"stale", "no-data"}]
    overall = "healthy" if auth.is_configured() and not attention else "attention"
    traces = [{
        "time": now.strftime("%H:%M:%S"),
        "level": "ok" if auth.is_configured() else "error",
        "source": "oauth",
        "message": "refresh credentials ready" if auth.is_configured() else "credentials missing",
    }, {
        "time": now.strftime("%H:%M:%S"),
        "level": "warn" if rate["remaining"] < 20 else "ok",
        "source": "quota",
        "message": f'{rate["used_this_window"]}/{rate["limit_per_minute"]} requests used this minute',
    }]
    for f in sorted(fetchers, key=lambda item: item["last_dispatch_epoch"] or 0, reverse=True)[:5]:
        traces.append({
            "time": f["last_dispatch"] or "--:--:--",
            "level": "ok" if f["state"] == "live" else ("warn" if f["state"] == "quiet" else "error"),
            "source": f["name"],
            "message": f'{f["data_types"]} types · fetch age {f["fetch_age"]} · write age {f["write_age"]}',
        })

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "token_ready": auth.is_configured(),
        "rate_limit": rate,
        "fetchers": fetchers,
        "trace": traces,
    }


def insight_feed() -> dict:
    """Cross-domain, prioritized daily signals for the overview tab."""
    ready = readiness.score()
    sleep = insights.sleep_trend(7)
    activity = insights.activity_summary(7)
    protein_status = protein.daily_status()
    progress = recomp.progress()
    today = _today()
    last_sleep = sleep["nights"][-1] if sleep["nights"] else None
    today_activity = next((x for x in activity["series"] if x["day"] == today), {})
    items = []

    score = ready.get("readiness")
    if score is not None:
        items.append({
            "level": "ok" if score >= 75 else ("warn" if score >= 55 else "error"),
            "metric": f"{score:.0f}/100",
            "title": "Recovery capacity",
            "detail": ready.get("recommendation"),
        })

    if last_sleep and last_sleep.get("hours_asleep") is not None:
        hours = last_sleep["hours_asleep"]
        items.append({
            "level": "ok" if hours >= 7.5 else ("warn" if hours >= 6.5 else "error"),
            "metric": f"{hours:.1f}h",
            "title": "Last sleep opportunity",
            "detail": f'{last_sleep.get("efficiency_pct") or 0:.1f}% efficiency · {last_sleep.get("awake_min") or 0:.0f} min awake',
        })

    steps = today_activity.get("steps")
    if steps is not None:
        pct = round(100 * steps / 10_000)
        items.append({
            "level": "ok" if pct >= 100 else ("warn" if pct >= 70 else "info"),
            "metric": f"{steps:,}",
            "title": "Daily movement",
            "detail": f"{pct}% of the 10,000-step operating target",
        })

    logged = protein_status.get("logged_g")
    target = protein_status.get("target_g")
    items.append({
        "level": "ok" if logged is not None and target and logged >= target else "warn",
        "metric": f"{logged:g}g" if logged is not None else "unlogged",
        "title": "Protein coverage",
        "detail": f"target {target}g · muscle-retention lever during the cut" if target else protein_status.get("note"),
    })

    readings = progress.get("fasted_readings", 0)
    items.append({
        "level": "ok" if readings >= 5 else "warn",
        "metric": str(readings),
        "title": "Fasted trend confidence",
        "detail": "enough readings for a stable trend" if readings >= 5 else "need at least 5 comparable AM readings",
    })
    return {"generated_at": datetime.now(PACIFIC).strftime("%H:%M:%S"), "items": items}


def sleep_insights(nights: int = 21) -> dict:
    """Sleep debt, consistency, trends, and recent nightly quality trace."""
    trend = insights.sleep_trend(nights)
    stage_history = sleep_history(nights).get("nights", [])
    rows = trend.get("nights", [])
    if not rows:
        return {"nights": 0, "trace": []}

    recent = rows[-7:]
    hours = [r["hours_asleep"] for r in recent if r.get("hours_asleep") is not None]
    efficiency = [r["efficiency_pct"] for r in recent if r.get("efficiency_pct") is not None]

    def clock_minutes(value: str | None) -> int | None:
        if not value:
            return None
        hour, minute = (int(v) for v in value.split(":"))
        if hour < 12:
            hour += 24
        return hour * 60 + minute

    bedtimes = [clock_minutes(r.get("bedtime")) for r in recent]
    bedtimes = [v for v in bedtimes if v is not None]
    consistency = round(statistics.pstdev(bedtimes)) if len(bedtimes) >= 2 else None
    debt = round(sum(max(0, 7.5 - h) for h in hours), 1)
    last3 = _mean(hours[-3:])
    prior3 = _mean(hours[-6:-3])
    duration_delta = round(last3 - prior3, 2) if last3 is not None and prior3 is not None else None

    latest_stage = stage_history[-1] if stage_history else {}
    stage_total = sum(latest_stage.get(k, 0) for k in ("deep", "rem", "light", "awake")) or 1
    stages = {
        key: {
            "minutes": latest_stage.get(key, 0),
            "pct": round(100 * latest_stage.get(key, 0) / stage_total, 1),
        }
        for key in ("deep", "rem", "light", "awake")
    }

    trace = []
    for row in reversed(recent):
        h = row.get("hours_asleep") or 0
        eff = row.get("efficiency_pct") or 0
        level = "ok" if h >= 7.5 and eff >= 90 else ("warn" if h >= 6.5 else "error")
        trace.append({
            "time": row["night"],
            "level": level,
            "title": f'{h:.2f}h asleep · {eff:.1f}% efficient',
            "detail": f'{row.get("bedtime") or "--:--"} → {row.get("wake") or "--:--"} · {row.get("awake_min") or 0:.0f} min awake',
        })

    avg_hours = _mean(hours)
    avg_eff = _mean(efficiency)
    recommendation = (
        "Sleep volume and timing are stable; protect the same window."
        if debt <= 1 and (consistency or 999) <= 45
        else "Recover the accumulated sleep debt before adding training volume."
    )
    return {
        "nights": len(recent),
        "avg_hours": round(avg_hours, 2) if avg_hours is not None else None,
        "avg_efficiency": round(avg_eff, 1) if avg_eff is not None else None,
        "sleep_debt_hours": debt,
        "bedtime_consistency_min": consistency,
        "duration_delta_hours": duration_delta,
        "latest_stages": stages,
        "recommendation": recommendation,
        "trace": trace,
    }


def _bucketed_activity_today(bucket_min: int = 5) -> dict[str, dict]:
    now = datetime.now(PACIFIC)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day0.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    buckets: dict[str, dict] = defaultdict(lambda: {"steps": 0, "meters": 0.0})
    c = _ro("live_activity")
    if not c:
        return buckets
    try:
        step_rows = c.execute("""
            SELECT start_time, MAX(count) value FROM steps
            WHERE start_time >= ? AND start_time < ? GROUP BY start_time
        """, (start, end)).fetchall()
        distance_rows = c.execute("""
            SELECT start_time, MAX(CAST(json_extract(data_json,'$.millimeters') AS REAL)) value
            FROM distance WHERE start_time >= ? AND start_time < ? GROUP BY start_time
        """, (start, end)).fetchall()
    except sqlite3.OperationalError:
        return buckets
    finally:
        c.close()

    def key_for(raw: str) -> str:
        local = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PACIFIC)
        local = local.replace(minute=(local.minute // bucket_min) * bucket_min, second=0, microsecond=0)
        return local.strftime("%H:%M")

    for row in step_rows:
        buckets[key_for(row["start_time"])]["steps"] += int(_num(row["value"]))
    for row in distance_rows:
        buckets[key_for(row["start_time"])]["meters"] += _num(row["value"]) / 1000
    return buckets


def training_trace() -> dict:
    """Five-minute heart/movement trace plus the strongest detected training block."""
    heart = heart_today(bucket_min=5)
    movement = _bucketed_activity_today()
    by_time = {p["t"]: {"bpm": p["bpm"], "steps": 0, "meters": 0.0} for p in heart.get("points", [])}
    for key, values in movement.items():
        by_time.setdefault(key, {"bpm": None, "steps": 0, "meters": 0.0}).update(values)
    points = [{"t": key, **by_time[key]} for key in sorted(by_time)]
    if not points:
        return {"points": [], "session": None, "trace": []}

    rest = heart.get("resting_hr") or 60
    activation_hr = max(95, rest + 25)
    active = [p for p in points if (p["bpm"] or 0) >= activation_hr or p["steps"] >= 100]
    groups: list[list[dict]] = []
    for point in active:
        current = datetime.strptime(point["t"], "%H:%M")
        if not groups:
            groups.append([point])
            continue
        previous = datetime.strptime(groups[-1][-1]["t"], "%H:%M")
        if (current - previous).total_seconds() <= 15 * 60:
            groups[-1].append(point)
        else:
            groups.append([point])

    def group_score(group: list[dict]) -> float:
        return sum(max(0, (p["bpm"] or rest) - activation_hr) * 2 + p["steps"] / 20 for p in group)

    candidates = [g for g in groups if len(g) >= 3]
    selected = max(candidates, key=group_score) if candidates else []
    if not selected:
        return {"points": points, "session": None, "trace": []}

    first_hr_active = next(
        (p for p in selected if p["bpm"] is not None and p["bpm"] >= activation_hr),
        selected[0],
    )
    start_t, last_t = first_hr_active["t"], selected[-1]["t"]
    start_dt = datetime.strptime(start_t, "%H:%M")
    end_dt = datetime.strptime(last_t, "%H:%M") + timedelta(minutes=5)
    session_points = [
        p for p in points
        if start_dt <= datetime.strptime(p["t"], "%H:%M") < end_dt
    ]
    hr_values = [p["bpm"] for p in session_points if p["bpm"] is not None]
    if not hr_values:
        return {"points": points, "session": None, "trace": []}
    peak = max((p for p in session_points if p["bpm"] is not None), key=lambda p: p["bpm"])
    zdefs = heart.get("zones", [])
    zone_minutes = {z["zone"]: 0 for z in zdefs}
    below = 0
    for p in session_points:
        bpm = p["bpm"]
        if bpm is None:
            continue
        matched = next((z for z in zdefs if z["low"] <= bpm <= z["high"]), None)
        if matched:
            zone_minutes[matched["zone"]] += 5
        else:
            below += 5

    after = [
        p["bpm"] for p in points
        if end_dt <= datetime.strptime(p["t"], "%H:%M") < end_dt + timedelta(minutes=15)
        and p["bpm"] is not None
    ]
    end_hr_values = [
        p["bpm"] for p in session_points[-2:] if p["bpm"] is not None
    ]
    end_hr = _mean(end_hr_values)
    recovery = round(end_hr - _mean(after)) if after and end_hr is not None else None
    duration = round((end_dt - start_dt).total_seconds() / 60)
    avg_hr = round(_mean(hr_values)) if hr_values else None
    max_hr = round(max(hr_values)) if hr_values else None
    intensity = "high" if max_hr and max_hr >= 160 else ("moderate" if max_hr and max_hr >= 135 else "light")
    session = {
        "start": start_t,
        "end": end_dt.strftime("%H:%M"),
        "duration_min": duration,
        "avg_bpm": avg_hr,
        "max_bpm": max_hr,
        "steps": sum(p["steps"] for p in session_points),
        "distance_km": round(sum(p["meters"] for p in session_points) / 1000, 2),
        "intensity": intensity,
        "zone_minutes": {"below_zone_1": below, **zone_minutes},
        "recovery_drop_bpm": recovery,
    }
    trace = [{
        "time": start_t,
        "level": "info",
        "title": "training block detected",
        "detail": f"{duration} min · activation threshold {activation_hr} bpm",
    }, {
        "time": peak["t"],
        "level": "warn" if (peak["bpm"] or 0) >= 160 else "ok",
        "title": f'peak {peak["bpm"]} bpm',
        "detail": f'{peak["steps"]} steps in the five-minute bucket',
    }, {
        "time": end_dt.strftime("%H:%M"),
        "level": "ok" if recovery is not None and recovery >= 25 else "info",
        "title": "post-session recovery",
        "detail": f"{recovery} bpm drop over the next 15 min" if recovery is not None else "no post-session samples yet",
    }]
    return {
        "date": _today(),
        "data_as_of": points[-1]["t"],
        "points": points,
        "session": session,
        "zones": zdefs,
        "trace": trace,
    }


def body_insights() -> dict:
    """Recomposition trajectory, fuel coverage, logging confidence, priorities."""
    plan = recomp.plan()
    progress = recomp.progress(60)
    weight = insights.weight_trend(60, fasted_only=True)
    energy = insights.energy_balance()
    protein_status = protein.daily_status()
    activity = insights.activity_summary(14)
    counts = journal.summary()
    waist = list(reversed(journal.recent_waist(12)))
    calories = [x["calories_out"] for x in activity["series"] if x.get("calories_out") is not None]
    avg_out = round(_mean(calories)) if calories else None

    weekly_change = None
    if weight["n_readings"] >= 5 and len(weight["series"]) >= 2:
        first, last = weight["series"][0], weight["series"][-1]
        elapsed = (datetime.fromisoformat(last["date"]) - datetime.fromisoformat(first["date"])).days
        if elapsed >= 7:
            weekly_change = round((last["avg7_lb"] - first["avg7_lb"]) / (elapsed / 7), 2)

    trace = [{
        "time": plan.get("as_of") or "no date",
        "level": "ok" if progress.get("fasted_readings", 0) >= 5 else "warn",
        "title": "weight signal",
        "detail": f'{progress.get("fasted_readings", 0)} fasted readings · {progress.get("latest_avg7_lb") or "—"} lb 7-day average',
    }, {
        "time": energy.get("date"),
        "level": "ok" if energy.get("balance_kcal") is not None else "warn",
        "title": "energy balance",
        "detail": (
            f'{energy["balance_kcal"]:+} kcal · {energy["status"]}'
            if energy.get("balance_kcal") is not None else "meal calories not logged"
        ),
    }, {
        "time": protein_status.get("date") or _today(),
        "level": "ok" if protein_status.get("status") == "met" else "warn",
        "title": "protein",
        "detail": (
            f'{protein_status.get("logged_g")}g / {protein_status.get("target_g")}g'
            if protein_status.get("logged_g") is not None else f'target {protein_status.get("target_g")}g · intake unlogged'
        ),
    }]

    return {
        "plan": {
            "current_lb": plan.get("current", {}).get("weight_lb"),
            "target_lb": plan.get("target", {}).get("weight_lb"),
            "bodyfat_pct": plan.get("assumptions", {}).get("bodyfat_pct"),
            "target_bodyfat_pct": plan.get("target", {}).get("bodyfat_pct"),
            "to_go_lb": progress.get("to_go_lb"),
            "eta_weeks": plan.get("eta_weeks"),
            "target_weekly_loss_lb": plan.get("safe_weekly_loss_lb"),
            "actual_weekly_change_lb": weekly_change,
        },
        "fuel": {
            "protein_logged_g": protein_status.get("logged_g"),
            "protein_target_g": protein_status.get("target_g"),
            "calories_in": energy.get("calories_in"),
            "calories_out": energy.get("calories_out"),
            "balance_kcal": energy.get("balance_kcal"),
            "avg_calories_out": avg_out,
        },
        "coverage": {
            "fasted_weights": progress.get("fasted_readings", 0),
            "meal_days": counts.get("meals", 0),
            "waist_readings": len(waist),
            "workouts_logged": counts.get("workout_log", 0),
        },
        "waist": waist,
        "priorities": [
            "Lat width + mid-back thickness",
            "Upper chest",
            "Rear + lateral delts",
            "Long-head triceps",
            "Core tonus",
        ],
        "trace": trace,
    }


# Dispatch table for /api/comp/{name}
REGISTRY = {
    "hypnogram": hypnogram,
    "heart_today": heart_today,
    "zones_today": zones_today,
    "hourly_steps": hourly_steps,
    "vitals": vitals,
    "weight_scatter": weight_scatter,
    "sleep_history": sleep_history,
    "operations_trace": operations_trace,
    "insight_feed": insight_feed,
    "sleep_insights": sleep_insights,
    "training_trace": training_trace,
    "body_insights": body_insights,
}


def get(name: str) -> dict:
    fn = REGISTRY.get(name)
    if not fn:
        raise KeyError(name)
    return fn()


if __name__ == "__main__":
    import sys
    print(json.dumps(get(sys.argv[1] if len(sys.argv) > 1 else "vitals"), indent=2, default=str))
