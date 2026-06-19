"""Dead-simple cross-process rate limiter.

The fetchers run as separate processes (launched by the orchestrator), so the
budget has to be shared *between* processes — an in-memory counter wouldn't
work.  We keep one small JSON file under data/state/ and guard it with an
exclusive file lock (``fcntl``).  The algorithm is a fixed window:

    * a window is RATE_LIMIT_WINDOW_SECONDS (60s) long,
    * at most RATE_LIMIT_PER_MINUTE requests are allowed per window,
    * when the budget for the current window is gone, ``acquire`` sleeps until
      the window rolls over, then continues.

Fixed-window is slightly bursty at the boundary, but it is trivial to reason
about and more than good enough to stay under Google Health's ~120 req/min.
The client *also* honours any 429 / Retry-After from the server as a backstop.
"""
from __future__ import annotations

import json
import logging
import os
import time

from fitlit import config

log = logging.getLogger("fitlit.ratelimit")

# fcntl is POSIX-only (macOS/Linux).  Degrade gracefully elsewhere.
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


def _read(fh) -> dict:
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        return {"window_start": 0.0, "count": 0}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"window_start": 0.0, "count": 0}


def _write(fh, state: dict) -> None:
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(state))
    fh.flush()
    os.fsync(fh.fileno())


def snapshot(*, now: float | None = None) -> dict:
    """Read-only view of the shared budget for the current window (no claim)."""
    now = time.time() if now is None else now
    state = {"window_start": 0.0, "count": 0}
    if config.RATELIMIT_STATE.exists():
        try:
            with open(config.RATELIMIT_STATE) as fh:
                state = _read(fh)
        except OSError:
            pass
    in_window = now - float(state.get("window_start", 0.0)) < config.RATE_LIMIT_WINDOW_SECONDS
    used = int(state.get("count", 0)) if in_window else 0
    return {
        "limit_per_minute": config.RATE_LIMIT_PER_MINUTE,
        "used_this_window": used,
        "remaining": max(0, config.RATE_LIMIT_PER_MINUTE - used),
    }


def acquire(*, now: float | None = None) -> None:
    """Block until one request is allowed under the shared budget, then claim it."""
    now = time.time() if now is None else now
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Open r+ if it exists, else create it.
    fh = open(config.RATELIMIT_STATE, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

        state = _read(fh)
        window_start = float(state.get("window_start", 0.0))
        count = int(state.get("count", 0))

        # Roll the window over if the current one has elapsed.
        if now - window_start >= config.RATE_LIMIT_WINDOW_SECONDS:
            window_start, count = now, 0

        if count < config.RATE_LIMIT_PER_MINUTE:
            _write(fh, {"window_start": window_start, "count": count + 1})
            return

        # Budget exhausted for this window — wait it out, then claim the first
        # slot of the fresh window.
        sleep_for = config.RATE_LIMIT_WINDOW_SECONDS - (now - window_start)
    finally:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()

    if sleep_for > 0:
        log.info("rate limit reached (%s/min); sleeping %.1fs", config.RATE_LIMIT_PER_MINUTE, sleep_for)
        time.sleep(sleep_for)
    # Re-enter with a rolled-over window.
    acquire()
