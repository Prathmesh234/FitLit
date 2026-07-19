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


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


def _env_optional_float(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


# --------------------------------------------------------------------------- #
# Google Health API target + auth
# --------------------------------------------------------------------------- #
BASE_URL = _env("GOOGLE_HEALTH_BASE_URL", "https://health.googleapis.com")
API_USER = _env("GOOGLE_HEALTH_USER", "me")            # 'me' = the token's user
# A pre-minted access token. Optional once OAuth refresh is configured (below):
# auth.py mints its own from the refresh token. Still honoured as a fallback /
# for quick one-off testing.
ACCESS_TOKEN = os.environ.get("GOOGLE_HEALTH_ACCESS_TOKEN", "")
PAGE_SIZE = int(_env("GOOGLE_HEALTH_PAGE_SIZE", "100"))
REQUEST_TIMEOUT = int(_env("GOOGLE_HEALTH_TIMEOUT", "30"))

# dataPoints.list supports up to 10,000 rows/page (sleep + exercise cap at 25).
# Use the large page for bounded polling so high-frequency heart data does not
# consume the entire per-minute quota just walking pagination.
LIST_PAGE_SIZE = max(1, min(10_000, int(_env("GOOGLE_HEALTH_LIST_PAGE_SIZE", "10000"))))

# Poll overlapping recent windows rather than replaying the user's full history
# every cycle. Upserts make the overlap safe and capture delayed/edited points.
STREAM_LOOKBACK_HOURS = int(_env("FITLIT_STREAM_LOOKBACK_HOURS", "48"))
SUMMARY_LOOKBACK_DAYS = int(_env("FITLIT_SUMMARY_LOOKBACK_DAYS", "14"))

# --------------------------------------------------------------------------- #
# OAuth 2.0 (Google) — refresh-token flow.  Access tokens live ~1h; the
# long-lived refresh token mints fresh ones unattended.  See fitlit/auth.py and
# docs/DEPLOYMENT.md §2/§4.  CLIENT_ID/SECRET come from the Google Cloud OAuth
# client; REFRESH_TOKEN is captured once via `python -m fitlit.auth login`.
# --------------------------------------------------------------------------- #
OAUTH_CLIENT_ID = os.environ.get("GOOGLE_HEALTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_HEALTH_CLIENT_SECRET", "")
OAUTH_REFRESH_TOKEN = os.environ.get("GOOGLE_HEALTH_REFRESH_TOKEN", "")
OAUTH_AUTH_URI = _env("GOOGLE_OAUTH_AUTH_URI", "https://accounts.google.com/o/oauth2/v2/auth")
OAUTH_TOKEN_URI = _env("GOOGLE_OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token")
# Redirect URI for the one-time `login` consent exchange (must match a URI
# registered on the OAuth client when it's a Web-application type).
OAUTH_REDIRECT_URI = _env("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8765/callback")
# Cached access token + expiry. Lives in STATE_DIR (gitignored, persisted on the
# data volume) so every fetcher process shares one refresh.
TOKEN_STATE = STATE_DIR / "token.json"
# Refresh this many seconds before the real expiry to avoid edge-of-expiry 401s.
OAUTH_REFRESH_LEEWAY_SECONDS = int(_env("GOOGLE_OAUTH_REFRESH_LEEWAY", "60"))

# --------------------------------------------------------------------------- #
# Gmail notification service. It reuses the OAuth client identity above, but
# has its own least-privilege gmail.send refresh token and access-token cache.
# Health metrics are read from local SQLite; the Gmail token cannot read them.
# --------------------------------------------------------------------------- #
GMAIL_API_BASE = _env("FITLIT_GMAIL_API_BASE", "https://gmail.googleapis.com/gmail/v1")
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_TO = os.environ.get("FITLIT_GMAIL_TO", "")
GMAIL_FROM_NAME = _env("FITLIT_GMAIL_FROM_NAME", "FitLit")
GMAIL_TOKEN_STATE = STATE_DIR / "gmail-token.json"
GMAIL_NOTIFICATION_DB = STATE_DIR / "gmail-notifications.db"
GMAIL_SERVICE_LOCK = STATE_DIR / "gmail-service.lock"
GMAIL_DAILY_MIN = 2
GMAIL_DAILY_MAX = 5
GMAIL_MORNING_FALLBACK_HOUR = int(_env("FITLIT_GMAIL_MORNING_FALLBACK_HOUR", "12"))
GMAIL_EVENING_FILL_HOUR = int(_env("FITLIT_GMAIL_EVENING_FILL_HOUR", "20"))
GMAIL_WEEKLY_REPORT_HOUR = int(_env("FITLIT_GMAIL_WEEKLY_REPORT_HOUR", "20"))
GMAIL_WEEKLY_RETRY_UNTIL_HOUR = int(
    _env("FITLIT_GMAIL_WEEKLY_RETRY_UNTIL_HOUR", "12")
)

# Optional provider-neutral AI enrichment. Detection, caps, deduplication, and
# delivery stay deterministic; this layer can only add validated observations.
AI_ENABLED = _env_bool("FITLIT_AI_ENABLED")
AI_PROVIDER = _env("FITLIT_AI_PROVIDER", "auto").lower()
AI_PROVIDER_ORDER = tuple(
    provider.strip().lower()
    for provider in _env("FITLIT_AI_PROVIDER_ORDER", "copilot,codex,claude").split(",")
    if provider.strip()
)
AI_TIMEOUT_SECONDS = max(5, int(_env("FITLIT_AI_TIMEOUT_SECONDS", "45")))
AI_MAX_OUTPUT_CHARS = max(500, int(_env("FITLIT_AI_MAX_OUTPUT_CHARS", "6000")))
AI_COPILOT_MODEL = _env("FITLIT_AI_COPILOT_MODEL", "")
AI_CODEX_MODEL = _env("FITLIT_AI_CODEX_MODEL", "")
AI_CLAUDE_MODEL = _env("FITLIT_AI_CLAUDE_MODEL", "")
AI_CLAUDE_MAX_BUDGET_USD = _env("FITLIT_AI_CLAUDE_MAX_BUDGET_USD", "0.05")

# Optional private coaching profile. Keep real values in .env, never source.
BODY_FAT_ESTIMATE_PCT = _env_optional_float("FITLIT_BODY_FAT_ESTIMATE_PCT")
TARGET_BODY_FAT_PCT = _env_optional_float("FITLIT_TARGET_BODY_FAT_PCT")
HEIGHT_M = _env_optional_float("FITLIT_HEIGHT_M")
GOAL_LABEL = _env("FITLIT_GOAL_LABEL", "personal health goal")
TRAINING_PRIORITIES = tuple(
    item.strip()
    for item in _env("FITLIT_TRAINING_PRIORITIES", "").split(",")
    if item.strip()
)

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

# Some Google Health data types reject `dataPoints.list` with HTTP 400 and only
# support aggregation endpoints. We fetch these via `dataPoints:dailyRollUp`
# instead (see fitlit/client.py). The rollup needs a date range; we re-request a
# trailing window each cycle (the API caps it at 14 days for these types).
DAILY_ROLLUP_TYPES: frozenset[str] = frozenset({
    "totalCalories", "floors", "caloriesInHeartRateZone",
})
ROLLUP_LOOKBACK_DAYS = int(_env("FITLIT_ROLLUP_LOOKBACK_DAYS", "7"))


# --------------------------------------------------------------------------- #
# Fetcher definitions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Fetcher:
    name: str
    interval_seconds: int          # cadence — how often the orchestrator runs it
    scopes: tuple[str, ...]        # the Google Health OAuth scope(s) it needs
    data_types: list[str] = field(default_factory=list)


# Cadence intent:
#   * 60s    near-real-time streams (steps, heart rate, calories…)
#   * 300s   event-driven medical signals (ECG, AFib)
#   * 1800s  occasional logs (body measurements, food/water)
#   * 3600s  once-an-hour daily roll-ups + sleep
_ACTIVITY = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
_METRICS = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
_SLEEP = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
_NUTRITION = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
# ECG + irregular-rhythm-notification each sit behind their own sensitive scope.
_ECG = "https://www.googleapis.com/auth/googlehealth.ecg.readonly"
_IRN = "https://www.googleapis.com/auth/googlehealth.irn.readonly"

FETCHERS: dict[str, Fetcher] = {
    "live_activity": Fetcher("live_activity", 60, (_ACTIVITY,), [
        "steps", "distance", "totalCalories", "activeMinutes", "activeZoneMinutes",
        "floors", "activityLevel", "sedentaryPeriod", "altitude",
        "caloriesInHeartRateZone", "timeInHeartRateZone",
    ]),
    "heart": Fetcher("heart", 60, (_METRICS,), [
        "heartRate",
    ]),
    "daily_summaries": Fetcher("daily_summaries", 3600, (_ACTIVITY,), [
        "dailyHeartRateZones", "dailyVo2Max", "runVo2Max", "vo2Max",
        "exercise", "dailyRestingHeartRate", "heartRateVariability",
        "dailyHeartRateVariability", "oxygenSaturation", "dailyOxygenSaturation",
        "dailyRespiratoryRate", "respiratoryRateSleepSummary",
        "dailySleepTemperatureDerivations",
    ]),
    "body": Fetcher("body", 1800, (_METRICS,), [
        "bodyFat", "weight", "height", "coreBodyTemperature",
        "bloodGlucose",
    ]),
    "sleep": Fetcher("sleep", 3600, (_SLEEP,), [
        "sleep",
    ]),
    "nutrition": Fetcher("nutrition", 1800, (_NUTRITION,), [
        "nutritionLog", "hydrationLog",
    ]),
    "cardiac": Fetcher("cardiac", 300, (_ECG, _IRN), [
        "electrocardiogram", "irregularRhythmNotification",
    ]),
}

# The full read-only scope set FitLit needs — every distinct scope across the
# fetchers above.  `fitlit/auth.py login` requests exactly these during consent.
SCOPES: list[str] = sorted({scope for f in FETCHERS.values() for scope in f.scopes})
