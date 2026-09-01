"""Configuration for the personal section.

Importing `fitlit.config` first is deliberate: it loads `.env`, sets the
process umask, and defines the shared data/state paths. Everything here is a
personal-domain knob layered on top of that.
"""
from __future__ import annotations

import os

from fitlit import config as fitlit_config
from fitlit.config import _env, _env_bool, _env_budget  # noqa: PLC2701

BASE_DIR = fitlit_config.BASE_DIR
PERSONAL_DIR = BASE_DIR / "personal"
SKILLS_DIR = PERSONAL_DIR / "skills"

# One ledger for every personal task. Separate from the Gmail health ledger so
# a personal job neither consumes nor is throttled by the health send budget.
PERSONAL_DB = fitlit_config.STATE_DIR / "personal.db"
PERSONAL_LOCK = fitlit_config.STATE_DIR / "personal-tasks.lock"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Daily coffee-shop recommendation
# --------------------------------------------------------------------------- #
COFFEE_ENABLED = _env_bool("FITLIT_PERSONAL_COFFEE_ENABLED", True)

# Where the drive starts and how far it may reasonably go. The hard ceiling is
# the soft target plus the tolerance; a candidate beyond it is rejected.
COFFEE_ORIGIN = _env("FITLIT_PERSONAL_COFFEE_ORIGIN", "South Lake Union, Seattle, WA")
COFFEE_CITY = _env("FITLIT_PERSONAL_COFFEE_CITY", "Seattle, Washington")
COFFEE_TARGET_DRIVE_MINUTES = _env_int(
    "FITLIT_PERSONAL_COFFEE_DRIVE_MINUTES", 15, 5, 60
)
COFFEE_DRIVE_TOLERANCE_MINUTES = _env_int(
    "FITLIT_PERSONAL_COFFEE_DRIVE_TOLERANCE_MINUTES", 3, 0, 15
)
COFFEE_MAX_DRIVE_MINUTES = COFFEE_TARGET_DRIVE_MINUTES + COFFEE_DRIVE_TOLERANCE_MINUTES

# Duplicate control. Every shop recommended inside the repeat window is sent to
# the model as an exclusion list, and a repeat inside it is retried once. The
# owner accepts an occasional repeat, so a surviving repeat is delivered with a
# recorded note rather than dropping the day's email entirely.
COFFEE_REPEAT_WINDOW_DAYS = _env_int(
    "FITLIT_PERSONAL_COFFEE_REPEAT_WINDOW_DAYS", 60, 1, 3650
)
COFFEE_HISTORY_LIMIT = _env_int("FITLIT_PERSONAL_COFFEE_HISTORY_LIMIT", 80, 5, 400)
COFFEE_FEEDBACK_LIMIT = _env_int("FITLIT_PERSONAL_COFFEE_FEEDBACK_LIMIT", 25, 1, 200)

# Atmosphere. "moderate" is allowed because the owner said a little noise is
# fine; anything the model would describe as lively or loud is not.
COFFEE_NOISE_LEVELS = ("very quiet", "quiet", "moderate")

# Delivery hour in Pacific time. The systemd timer fires here; the guard rail
# below keeps a stray manual run from mailing at an unexpected hour.
COFFEE_SEND_HOUR = _env_int("FITLIT_PERSONAL_COFFEE_SEND_HOUR", 9, 0, 23)

# A morning that produces nothing is indistinguishable from a morning where the
# timer was never installed, which is how a broken deployment stays hidden.
# Mail a short notice instead so silence always means "nothing ran at all".
COFFEE_NOTIFY_ON_FAILURE = _env_bool("FITLIT_PERSONAL_COFFEE_NOTIFY_ON_FAILURE", True)

# Harness settings. Web search is mandatory for this task, so the timeout is
# generous compared with the deterministic health-insight calls.
COFFEE_ATTEMPTS = _env_int("FITLIT_PERSONAL_COFFEE_ATTEMPTS", 3, 1, 5)
COFFEE_TIMEOUT_SECONDS = _env_int(
    "FITLIT_PERSONAL_COFFEE_TIMEOUT_SECONDS", 420, 60, 1800
)
COFFEE_MAX_TURNS = _env_int("FITLIT_PERSONAL_COFFEE_MAX_TURNS", 24, 4, 60)
COFFEE_MAX_OUTPUT_CHARS = _env_int(
    "FITLIT_PERSONAL_COFFEE_MAX_OUTPUT_CHARS", 200_000, 10_000, 1_000_000
)
COFFEE_MIN_WEB_SEARCHES = _env_int("FITLIT_PERSONAL_COFFEE_MIN_WEB_SEARCHES", 1, 0, 10)
COFFEE_CLAUDE_MODEL = _env("FITLIT_PERSONAL_COFFEE_CLAUDE_MODEL", "claude-sonnet-5")
COFFEE_REASONING_EFFORT = _env(
    "FITLIT_PERSONAL_COFFEE_REASONING_EFFORT", "high"
).lower()
COFFEE_MAX_BUDGET_USD = _env_budget("FITLIT_PERSONAL_COFFEE_MAX_BUDGET_USD")

# Every personal task the runner knows about, in registration order.
TASKS = ("coffee",)
