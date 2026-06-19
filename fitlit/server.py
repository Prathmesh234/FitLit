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
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from fitlit import config, orchestrator, ratelimit, storage
from fitlit.client import MissingTokenError
from fitlit.fetchers.base import fetch_once

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
    """Readiness probe — 200 only once an access token is configured."""
    if not config.ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="no GOOGLE_HEALTH_ACCESS_TOKEN configured")
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
        "endpoints": ["/health", "/ready", "/fetchers", "/status", "/stats", "/fetchers/{name}/run"],
    }


@app.get("/fetchers")
def list_fetchers() -> dict:
    return {
        name: {
            "interval_seconds": f.interval_seconds,
            "scope": f.scope,
            "data_types": f.data_types,
        }
        for name, f in config.FETCHERS.items()
    }


@app.get("/status")
def status() -> dict:
    return {
        "scheduler_running": _scheduler_alive(),
        "token_configured": bool(config.ACCESS_TOKEN),
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


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
