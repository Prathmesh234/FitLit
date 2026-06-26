"""FastAPI server — the long-lived process for a 24/7 container.

One process does two things:

  1. runs the orchestrator (the 10s scheduler) in a background thread, so the
     fetchers keep pulling data on their cadence, and
  2. exposes HTTP endpoints for health checks, observability, and manual
     triggering — the things a web app / container platform needs.

Run it:

    uv run uvicorn fitlit.server:app --host 0.0.0.0 --port 8000
    # or:  uv run python -m fitlit.server

Endpoints:

    GET  /health                 liveness  — always 200 if the process is up
    GET  /ready                  readiness — 200 only if a token is configured
    GET  /                       service summary
    GET  /fetchers               list fetchers (cadence, scope, data types)
    GET  /status                 scheduler state + rate-limit budget
    POST /fetchers/{name}/run    fetch one fetcher now (in-process), return summary
"""
from __future__ import annotations

import logging
import pathlib
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fitlit import auth, config, dashboard, insights, journal, orchestrator, ratelimit, storage
from fitlit.client import GoogleHealthClient, MissingTokenError
from fitlit.fetchers.base import fetch_once

_STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("fitlit.server")

# Background scheduler handles (populated in the lifespan).
_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_thread
    if config.RUN_SCHEDULER:
        _stop.clear()
        _scheduler_thread = threading.Thread(
            target=orchestrator.run_forever, args=(_stop,), name="fitlit-scheduler", daemon=True
        )
        _scheduler_thread.start()
        log.info("background scheduler started")
    else:
        log.info("scheduler disabled (FITLIT_RUN_SCHEDULER=false)")
    try:
        yield
    finally:
        _stop.set()
        if _scheduler_thread is not None:
            _scheduler_thread.join(timeout=config.TICK_SECONDS + 5)
        log.info("server shutting down")


app = FastAPI(
    title="FitLit",
    summary="Cron-orchestrated Google Health API (v4) data fetcher",
    version="0.1.0",
    lifespan=lifespan,
)


def _scheduler_alive() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()


@app.get("/health")
def health() -> dict:
    """Liveness probe — the process is running."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Readiness probe — 200 once a token can be obtained (OAuth refresh creds
    or a static access token)."""
    if not auth.is_configured():
        raise HTTPException(
            status_code=503,
            detail="no credentials — set GOOGLE_HEALTH_ACCESS_TOKEN or OAuth "
            "refresh vars (see docs/DEPLOYMENT.md §2)",
        )
    return {"status": "ready"}


@app.get("/")
def root() -> dict:
    return {
        "service": "fitlit",
        "api": config.BASE_URL,
        "scheduler_enabled": config.RUN_SCHEDULER,
        "scheduler_running": _scheduler_alive(),
        "tick_seconds": config.TICK_SECONDS,
        "fetchers": list(config.FETCHERS),
        "endpoints": ["/health", "/ready", "/fetchers", "/status", "/stats",
                      "/fetchers/{name}/run", "/insights", "/insights/weight",
                      "/insights/sleep", "/insights/activity", "/journal",
                      "/journal/weight", "/aggregate/{data_type}"],
    }


@app.get("/fetchers")
def list_fetchers() -> dict:
    return {
        name: {
            "interval_seconds": f.interval_seconds,
            "scopes": list(f.scopes),
            "data_types": f.data_types,
        }
        for name, f in config.FETCHERS.items()
    }


@app.get("/status")
def status() -> dict:
    return {
        "scheduler_running": _scheduler_alive(),
        "token_configured": auth.is_configured(),
        "rate_limit": ratelimit.snapshot(),
        "fetchers": orchestrator.schedule_status(),
    }


@app.get("/stats")
def stats() -> dict:
    """Stored row counts per data type per fetcher database."""
    return {"databases": storage.stats()}


@app.post("/fetchers/{name}/run")
async def run_fetcher_now(name: str) -> dict:
    """Trigger one fetcher immediately (in-process, off the event loop)."""
    if name not in config.FETCHERS:
        raise HTTPException(status_code=404, detail=f"unknown fetcher {name!r}")
    try:
        # fetch_once does blocking HTTP — run it in a worker thread.
        return await run_in_threadpool(fetch_once, name)
    except MissingTokenError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# --------------------------------------------------------------------------- #
# Analytics — read-only coaching insights over the fetcher DBs + journal.
# --------------------------------------------------------------------------- #
@app.get("/insights")
def insights_briefing(date: str | None = None) -> dict:
    """One cross-domain daily briefing (weight, sleep, activity, energy balance)."""
    return insights.daily_briefing(date)


@app.get("/insights/weight")
def insights_weight(days: int = 30, fasted_only: bool = False) -> dict:
    """Weight series + 7-day moving average from the journal."""
    return insights.weight_trend(days, fasted_only=fasted_only)


@app.get("/insights/sleep")
def insights_sleep(days: int = 14) -> dict:
    """Per-night sleep duration / efficiency from the wearable data."""
    return insights.sleep_trend(days)


@app.get("/insights/activity")
def insights_activity(days: int = 7) -> dict:
    """Dedup-safe daily steps + calories-out."""
    return insights.activity_summary(days)


# --------------------------------------------------------------------------- #
# Journal — the writable, user-owned log.
# --------------------------------------------------------------------------- #
class WeighIn(BaseModel):
    weight_lb: float | None = None
    weight_kg: float | None = None
    conditions: str = "unspecified"
    date: str | None = None
    weighed_at: str | None = None
    note: str | None = None


@app.get("/journal")
def journal_summary() -> dict:
    """Row counts per journal table + recent weigh-ins."""
    return {"counts": journal.summary(), "recent_weights": journal.recent_weights(7)}


@app.post("/journal/weight")
def journal_log_weight(entry: WeighIn) -> dict:
    """Record a weigh-in into the user-owned journal."""
    try:
        return journal.log_weight(
            weight_lb=entry.weight_lb, weight_kg=entry.weight_kg,
            conditions=entry.conditions, date=entry.date,
            weighed_at=entry.weighed_at, note=entry.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# On-demand aggregation — straight from the Google Health rollUp API.
# --------------------------------------------------------------------------- #
@app.get("/aggregate/{data_type}")
async def aggregate(data_type: str, hours: int = 24, window_seconds: int = 3600) -> dict:
    """Roll up a data type into fixed windows over the trailing ``hours``.

    e.g. ``/aggregate/steps?hours=24&window_seconds=3600`` → hourly step sums;
    ``/aggregate/heartRate?hours=2&window_seconds=300`` → 5-min HR avg/min/max.
    """
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    client = GoogleHealthClient("aggregate")
    try:
        windows = await run_in_threadpool(
            client.roll_up, data_type, start, end, window_seconds)
    except MissingTokenError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if windows is None:
        raise HTTPException(status_code=502, detail=f"rollUp failed for {data_type!r}")
    return {"data_type": data_type, "hours": hours, "window_seconds": window_seconds,
            "windows": windows}


# --------------------------------------------------------------------------- #
# Live dashboard — one JSON snapshot + the static frontend.
# --------------------------------------------------------------------------- #
@app.get("/dashboard/data")
async def dashboard_data() -> dict:
    """Full metric snapshot powering the live dashboard (polled by the frontend)."""
    return await run_in_threadpool(dashboard.snapshot)


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    """Serve the dashboard HTML."""
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="dashboard not built")
    return FileResponse(index)


if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
