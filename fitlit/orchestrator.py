"""The main orchestrator — FitLit's cron handler.

It wakes up every TICK_SECONDS (10s by default), looks at each fetcher's
cadence, and launches the ones whose interval has elapsed since they last ran.
Each fetcher is launched as its own subprocess (``python -m fitlit.fetchers.<name>``),
exactly as the brief describes: "every 10 seconds it calls all the other scripts."

Why a 10s loop instead of plain crontab? crontab's finest resolution is one
minute, so the orchestrator *is* the scheduler — it owns the per-fetcher
cadence. cron's only job is to keep this process alive (see crontab.example:
an ``@reboot`` entry). Run it directly with:

    uv run python -m fitlit.orchestrator              # daemon loop (default)
    uv run python -m fitlit.orchestrator --once       # one dispatch tick, then exit

Last-run times are persisted to data/state/schedule.json, so cadence survives a
restart. A fetcher that is still running when its next slot arrives is skipped
(no overlapping runs of the same script).
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import threading
import time

from fitlit import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("fitlit.orchestrator")


def _load_schedule() -> dict[str, float]:
    if config.SCHEDULE_STATE.exists():
        try:
            return json.loads(config.SCHEDULE_STATE.read_text())
        except json.JSONDecodeError:
            log.warning("schedule state was corrupt; starting fresh")
    return {}


def _save_schedule(state: dict[str, float]) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.SCHEDULE_STATE.write_text(json.dumps(state, indent=2))


def _due(name: str, last_run: dict[str, float], now: float) -> bool:
    fetcher = config.FETCHERS[name]
    return now - last_run.get(name, 0.0) >= fetcher.interval_seconds


def tick(last_run: dict[str, float], running: dict[str, subprocess.Popen], now: float) -> None:
    """Dispatch every fetcher that is due and not already running."""
    for name in config.FETCHERS:
        # Skip if a previous run of this same fetcher hasn't finished.
        proc = running.get(name)
        if proc is not None and proc.poll() is None:
            log.debug("[%s] still running; skip", name)
            continue

        if not _due(name, last_run, now):
            continue

        log.info("dispatch %s", name)
        running[name] = subprocess.Popen(
            [sys.executable, "-m", f"fitlit.fetchers.{name}"],
            cwd=str(config.BASE_DIR),
        )
        last_run[name] = now

    _save_schedule(last_run)


def run_forever(stop: threading.Event | None = None) -> None:
    """Loop until interrupted (CLI) or ``stop`` is set (background thread)."""
    log.info("orchestrator up — tick every %ds, %d fetchers", config.TICK_SECONDS, len(config.FETCHERS))
    last_run = _load_schedule()
    running: dict[str, subprocess.Popen] = {}
    try:
        while stop is None or not stop.is_set():
            tick(last_run, running, time.time())
            if stop is not None:
                stop.wait(config.TICK_SECONDS)   # interruptible sleep
            else:
                time.sleep(config.TICK_SECONDS)
    except KeyboardInterrupt:
        log.info("orchestrator shutting down")


def schedule_status() -> dict:
    """Snapshot for the HTTP API: per-fetcher cadence + when each is next due."""
    last_run = _load_schedule()
    now = time.time()
    fetchers = {}
    for name, fetcher in config.FETCHERS.items():
        last = last_run.get(name, 0.0)
        due_in = max(0.0, fetcher.interval_seconds - (now - last))
        fetchers[name] = {
            "interval_seconds": fetcher.interval_seconds,
            "data_types": len(fetcher.data_types),
            "last_run_epoch": last or None,
            "due_in_seconds": round(due_in, 1),
            "due_now": due_in == 0.0,
        }
    return fetchers


def main() -> None:
    parser = argparse.ArgumentParser(description="FitLit orchestrator (10s scheduler)")
    parser.add_argument("--once", action="store_true",
                        help="run a single dispatch tick and exit (handy for testing / minute-cron)")
    args = parser.parse_args()

    if args.once:
        last_run = _load_schedule()
        tick(last_run, {}, time.time())
    else:
        run_forever()


if __name__ == "__main__":
    main()
