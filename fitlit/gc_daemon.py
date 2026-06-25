"""FitLit garbage-collector **daemon**.

A long-lived background process that periodically runs the lossless archive +
prune (``fitlit.gc``) so the fetcher databases never fill the disk. It is the
storage-side counterpart to the orchestrator: where the orchestrator keeps data
*flowing in*, this keeps the footprint *bounded*.

Run it:

    uv run python -m fitlit.gc_daemon                 # daemon loop
    uv run python -m fitlit.gc_daemon --once          # one sweep, then exit
    uv run python -m fitlit.gc_daemon --older-than 14 # keep 14 days hot

Behaviour
---------
* Wakes every ``GC_INTERVAL_SECONDS`` (default 6h), archives + prunes everything
  older than ``--older-than`` days (default 7), then compacts to return space to
  the OS. Between sweeps it sleeps on an interruptible event.
* **Graceful shutdown.** SIGTERM/SIGINT set a stop event; an in-progress sweep
  is allowed to finish a batch and the loop exits cleanly — important under
  systemd, which sends SIGTERM on stop. The gc itself commits per batch, so even
  a hard kill never corrupts or loses data (archive-before-delete + verify).
* **Single-run guarded.** Designed to run as exactly one instance (one daemon),
  the same way the orchestrator owns the schedule.

Stdlib only.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time

from fitlit import config, gc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("fitlit.gc_daemon")

# How often the daemon runs a sweep. Default 6h — GC is cheap relative to the
# day-scale of data it consolidates, and frequent enough to keep disk bounded.
GC_INTERVAL_SECONDS = int(os.environ.get("FITLIT_GC_INTERVAL_SECONDS", str(6 * 3600)))

_stop = threading.Event()


def _install_signal_handlers() -> None:
    def _handle(signum, _frame):
        log.info("received signal %s — finishing current batch then stopping", signum)
        _stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except ValueError:
            # signal() only works in the main thread; ignore if embedded elsewhere
            pass


def sweep(older_than_days: int, *, do_compact: bool = True) -> dict:
    """Run a single archive+prune(+compact) sweep across all fetcher DBs."""
    log.info("gc sweep start — consolidating data older than %d days", older_than_days)
    result = gc.run_all(older_than_days=older_than_days, do_compact=do_compact)
    reclaimed = 0
    for fetcher, rep in result.items():
        comp = rep.get("compact") if isinstance(rep, dict) else None
        if comp and "reclaimed_bytes" in comp:
            reclaimed += comp["reclaimed_bytes"]
            log.info("gc %s: reclaimed %.2f MB", fetcher, comp["reclaimed_bytes"] / 1e6)
    log.info("gc sweep done — reclaimed %.2f MB total", reclaimed / 1e6)
    return result


def run_forever(older_than_days: int, stop: threading.Event | None = None) -> None:
    """Loop sweeps every GC_INTERVAL_SECONDS until stopped."""
    stop = stop or _stop
    log.info("gc daemon up — sweep every %ds, keep %d days hot, %d fetchers",
             GC_INTERVAL_SECONDS, older_than_days, len(config.FETCHERS))
    while not stop.is_set():
        try:
            sweep(older_than_days)
        except Exception:  # noqa: BLE001 — a daemon must survive one bad sweep
            log.exception("gc sweep failed; will retry next interval")
        stop.wait(GC_INTERVAL_SECONDS)
    log.info("gc daemon shutting down")


def main() -> None:
    parser = argparse.ArgumentParser(description="FitLit lossless GC daemon")
    parser.add_argument("--once", action="store_true",
                        help="run a single sweep and exit (handy for cron / testing)")
    parser.add_argument("--older-than", type=int, default=gc.DEFAULT_OLDER_THAN_DAYS,
                        help="consolidate data older than this many days (default 7)")
    parser.add_argument("--no-compact", action="store_true",
                        help="skip the VACUUM step (faster; doesn't shrink the file)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be consolidated without changing anything")
    args = parser.parse_args()

    if args.dry_run:
        import json
        print(json.dumps(gc.run_all(older_than_days=args.older_than, dry_run=True),
                         indent=2, default=str))
        return

    _install_signal_handlers()
    if args.once:
        sweep(args.older_than, do_compact=not args.no_compact)
    else:
        run_forever(args.older_than)


if __name__ == "__main__":
    main()
