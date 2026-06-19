"""Central configuration for FitLit.

Everything tunable lives here:

* where files go (raw data, scheduler/rate-limit state),
* the Google Health API target + credentials (read from the environment / .env),
* the rate-limit budget, and
* the FETCHERS map: each fetcher's cadence + the data types it pulls.

The fetcher -> data-types assignment covers *every* Google Health data type in
``data/fitbit_endpoints.yaml`` exactly once.  ``catalog.validate_coverage`` (run
on import) shouts if the catalogue and this map ever drift apart.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

# The endpoint catalogue ships *inside* the image and never changes at runtime.
CATALOG_PATH = BASE_DIR / "data" / "fitbit_endpoints.yaml"

# Runtime data (SQLite DBs + scheduler/rate-limit state) is the part that must
# survive restarts. In a container the filesystem is ephemeral, so point
# FITLIT_DATA_DIR at a mounted volume / Azure Files share to persist it.
DATA_DIR = pathlib.Path(os.environ.get("FITLIT_DATA_DIR", str(BASE_DIR / "data")))
DB_DIR = DATA_DIR / "db"            # one SQLite database per fetcher
STATE_DIR = DATA_DIR / "state"      # scheduler + rate-limiter bookkeeping
SCHEDULE_STATE = STATE_DIR / "schedule.json"
RATELIMIT_STATE = STATE_DIR / "ratelimit.json"


# --------------------------------------------------------------------------- #
# Tiny .env loader (no external dependency).  Real env vars always win.
# --------------------------------------------------------------------------- #
def _load_dotenv(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --------------------------------------------------------------------------- #
# Google Health API target + auth
# --------------------------------------------------------------------------- #
BASE_URL = _env("GOOGLE_HEALTH_BASE_URL", "https://health.googleapis.com")
API_USER = _env("GOOGLE_HEALTH_USER", "me")            # 'me' = the token's user
# We assume the access token is present (the user wires real OAuth in later).
ACCESS_TOKEN = os.environ.get("GOOGLE_HEALTH_ACCESS_TOKEN", "")
PAGE_SIZE = int(_env("GOOGLE_HEALTH_PAGE_SIZE", "100"))
REQUEST_TIMEOUT = int(_env("GOOGLE_HEALTH_TIMEOUT", "30"))

# --------------------------------------------------------------------------- #
# Rate limiting.  Google Health rejects with 429 once quota is exceeded; the
# observed default is ~120 requests/minute/user.  We stay comfortably under it
# and additionally honour any Retry-After the API sends back (see client.py).
# --------------------------------------------------------------------------- #
RATE_LIMIT_PER_MINUTE = int(_env("GOOGLE_HEALTH_RATE_LIMIT_PER_MIN", "100"))
RATE_LIMIT_WINDOW_SECONDS = 60

# Orchestrator tick resolution (seconds).  This is how often the scheduler wakes
# up to see which fetchers are due.
TICK_SECONDS = int(_env("FITLIT_TICK_SECONDS", "10"))

# When the FastAPI server boots, should it also start the scheduler in a
# background thread?  True for the single-container "web app runs everything"
# model; set false to run the API and the orchestrator as separate processes.
RUN_SCHEDULER = _env("FITLIT_RUN_SCHEDULER", "true").lower() in ("1", "true", "yes")

# HTTP bind address. PORT follows the Azure convention (Container Apps / App
# Service inject it); default 8000 for local use.
HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "8000"))

# SQLite journal mode. WAL is best on a local disk. Over a network filesystem
# (Azure Files / SMB) WAL's shared-memory locking is unsupported — set
# FITLIT_SQLITE_JOURNAL=DELETE there.
SQLITE_JOURNAL_MODE = _env("FITLIT_SQLITE_JOURNAL", "WAL")


# --------------------------------------------------------------------------- #
# Fetcher definitions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Fetcher:
    name: str
    interval_seconds: int          # cadence — how often the orchestrator runs it
    scope: str                     # the Google Health OAuth scope it needs
    data_types: list[str] = field(default_factory=list)


# Cadence intent:
#   * 60s    near-real-time streams (steps, heart rate, calories…)
#   * 300s   event-driven medical signals (ECG, AFib) + location/route
#   * 1800s  occasional logs (body measurements, food/water)
#   * 3600s  once-an-hour daily roll-ups + sleep
_ACTIVITY = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
_METRICS = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
_SLEEP = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
_NUTRITION = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
_LOCATION = "https://www.googleapis.com/auth/googlehealth.location.readonly"

FETCHERS: dict[str, Fetcher] = {
    "live_activity": Fetcher("live_activity", 60, _ACTIVITY, [
        "steps", "distance", "totalCalories", "activeMinutes", "activeZoneMinutes",
        "floors", "activityLevel", "sedentaryPeriod", "altitude",
        "caloriesInHeartRateZone", "timeInHeartRateZone",
    ]),
    "heart": Fetcher("heart", 60, _METRICS, [
        "heartRate",
    ]),
    "daily_summaries": Fetcher("daily_summaries", 3600, _ACTIVITY, [
        "dailyHeartRateZones", "dailyVo2Max", "runVo2Max", "vo2Max", "goals",
        "exercise", "dailyRestingHeartRate", "heartRateVariability",
        "dailyHeartRateVariability", "oxygenSaturation", "dailyOxygenSaturation",
        "respiratoryRate", "dailyRespiratoryRate", "respiratoryRateSleepSummary",
        "dailySleepTemperatureDerivations",
    ]),
    "body": Fetcher("body", 1800, _METRICS, [
        "bodyFat", "weight", "height", "bodyTemperature", "coreBodyTemperature",
        "bloodGlucose",
    ]),
    "sleep": Fetcher("sleep", 3600, _SLEEP, [
        "sleep", "sleepSummary",
    ]),
    "nutrition": Fetcher("nutrition", 1800, _NUTRITION, [
        "nutritionLog", "hydrationLog",
    ]),
    "cardiac": Fetcher("cardiac", 300, _METRICS, [
        "ecgMeasurement", "ecgRhythmClassification", "afibAnalysisWindow",
    ]),
    "location": Fetcher("location", 300, _LOCATION, [
        "location",
    ]),
}
