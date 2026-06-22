"""Minimal Google Health API (v4) client.

One generic method — ``list_data_points`` — works for every data type thanks to
the unified ``users.dataTypes.dataPoints`` design:

    GET /v4/users/{user}/dataTypes/{dataType}/dataPoints

Responsibilities kept here so the fetchers stay trivial:

    * attach a valid OAuth Bearer token (minted/refreshed by ``fitlit.auth``),
    * pass through the shared rate limiter before every call,
    * honour 429 / Retry-After with a bounded retry/backoff,
    * refresh the token once and retry on a 401 (expired access token),
    * follow pagination so a whole window is captured, and
    * upsert every data point into the fetcher's SQLite database.

Uses only the standard library for HTTP (urllib) plus our Pydantic/SQLite
storage layer.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fitlit import auth, config, ratelimit, storage

log = logging.getLogger("fitlit.client")

_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 5  # seconds, used when a 429 carries no Retry-After


class MissingTokenError(RuntimeError):
    """Raised when no access token can be obtained (none configured / no refresh)."""


def _camel_to_kebab(name: str) -> str:
    """heartRate -> heart-rate, activeZoneMinutes -> active-zone-minutes.

    Google Health uses kebab-case for the dataType path segment (snake_case is
    only for filters).  The catalogue stores the camelCase identifiers.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _civil_date_str(civil: dict | None) -> str | None:
    """A dailyRollUp ``CivilDateTime`` ({"date":{"year","month","day"}}) → 'YYYY-MM-DD'.

    Daily roll-up windows are calendar days, so a plain ISO date is the right
    grain (it sorts lexically like every other stored timestamp).
    """
    date = (civil or {}).get("date") or {}
    year, month, day = date.get("year"), date.get("month"), date.get("day")
    if not (year and month and day):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _rfc3339(dt: datetime) -> str:
    """A datetime → RFC-3339 UTC string (e.g. '2026-06-21T19:00:00Z').

    The ``rollUp`` range uses Timestamp-shaped bounds (unlike ``dailyRollUp``,
    which uses civil dates). Naive datetimes are assumed to be UTC.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _api_error_message(exc: urllib.error.HTTPError) -> str:
    """Pull the human-readable reason out of a Google API error response.

    Google returns ``{"error": {"code", "message", "status", ...}}`` in the
    body — far more useful than the bare status line (e.g. *"List is not
    supported for data type total-calories"*).  Falls back to the reason phrase
    if the body is missing or unparseable.  Reading the body never raises here.
    """
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        return payload.get("error", {}).get("message") or exc.reason
    except Exception:  # noqa: BLE001 - diagnostics must never mask the original error
        return exc.reason


class GoogleHealthClient:
    def __init__(self, fetcher_name: str) -> None:
        self.fetcher_name = fetcher_name
        self.user = config.API_USER

    # ------------------------------------------------------------------ #
    def fetch(self, data_type: str) -> int | None:
        """Fetch + persist one data type, choosing the right API method.

        Most types use ``dataPoints.list``. A few (``DAILY_ROLLUP_TYPES``) reject
        list with HTTP 400 and must be aggregated via ``dataPoints:dailyRollUp``.
        """
        if data_type in config.DAILY_ROLLUP_TYPES:
            return self.daily_rollup_data_points(data_type)
        return self.list_data_points(data_type)

    # ------------------------------------------------------------------ #
    def list_data_points(self, data_type: str) -> int | None:
        """Fetch + persist every available data point for one data type.

        Follows ``nextPageToken`` so a whole window is captured, upserting each
        page into the fetcher's SQLite database. Returns the number of points
        stored, or ``None`` if the very first request failed (logged, never
        raised, so one bad type can't sink the sweep).
        """
        try:
            auth.get_access_token()  # fail fast + clear message if unconfigured
        except auth.AuthError as exc:
            raise MissingTokenError(str(exc)) from exc

        path = f"/v4/users/{urllib.parse.quote(self.user)}/dataTypes/{_camel_to_kebab(data_type)}/dataPoints"
        base = f"{config.BASE_URL}{path}"
        fetched_at = datetime.now(timezone.utc)

        stored = 0
        page_token: str | None = None
        first = True
        while True:
            params = {"pageSize": config.PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            body = self._get_with_retry(f"{base}?{urllib.parse.urlencode(params)}", data_type)
            if body is None:
                return None if first else stored
            first = False

            points = body.get("dataPoints") or []
            stored += storage.store(self.fetcher_name, data_type, points, fetched_at)

            page_token = body.get("nextPageToken")
            if not page_token:
                break

        log.info("stored %-28s %d points", data_type, stored)
        return stored

    # ------------------------------------------------------------------ #
    def roll_up(
        self,
        data_type: str,
        start: datetime,
        end: datetime,
        window_seconds: int = 3600,
    ) -> list[dict] | None:
        """On-demand aggregation via ``dataPoints:rollUp`` (does not persist).

        Groups a data type into fixed ``window_seconds`` windows over a
        ``[start, end)`` range and returns the aggregated windows — e.g. hourly
        step sums for a day, or 5-minute heart-rate averages for a workout. This
        is the read-side counterpart to the fetchers: it answers analytics
        questions the raw per-point tables make expensive, straight from the API.

        Returns a list of ``{"start_time", "end_time", <value>}`` dicts, or
        ``None`` if the request failed (logged, never raised). The API caps the
        range at 14 days for high-frequency types (heart-rate, steps,
        active-minutes, total-calories) and 90 days otherwise.
        """
        try:
            auth.get_access_token()
        except auth.AuthError as exc:
            raise MissingTokenError(str(exc)) from exc

        path = f"/v4/users/{urllib.parse.quote(self.user)}/dataTypes/{_camel_to_kebab(data_type)}/dataPoints:rollUp"
        url = f"{config.BASE_URL}{path}"
        request = {
            "range": {"startTime": _rfc3339(start), "endTime": _rfc3339(end)},
            "windowSize": f"{int(window_seconds)}s",
            "pageSize": config.PAGE_SIZE,
        }

        windows: list[dict] = []
        page_token: str | None = None
        first = True
        while True:
            if page_token:
                request["pageToken"] = page_token
            body = self._request_with_retry(url, data_type, json_body=request)
            if body is None:
                return None if first else windows
            first = False

            windows.extend(body.get("rollupDataPoints") or [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break

        log.info("rolled up %-26s %d windows (%ss)", data_type, len(windows), window_seconds)
        return windows

    # ------------------------------------------------------------------ #
    def daily_rollup_data_points(self, data_type: str) -> int | None:
        """Fetch + persist a data type via ``dataPoints:dailyRollUp``.

        Used for types that reject ``list`` (e.g. ``totalCalories``, ``floors``,
        ``caloriesInHeartRateZone``). Requests a trailing window of daily windows,
        normalises each rollup window into the standard data-point envelope, and
        upserts it. Returns the number stored, or ``None`` if the first request
        failed (so the sweep keeps going, exactly like ``list_data_points``).
        """
        try:
            auth.get_access_token()
        except auth.AuthError as exc:
            raise MissingTokenError(str(exc)) from exc

        path = f"/v4/users/{urllib.parse.quote(self.user)}/dataTypes/{_camel_to_kebab(data_type)}/dataPoints:dailyRollUp"
        url = f"{config.BASE_URL}{path}"
        fetched_at = datetime.now(timezone.utc)

        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=config.ROLLUP_LOOKBACK_DAYS)
        end = today + timedelta(days=1)  # closed-open range → include today
        # The API caps windowSizeDays * pageSize at the type's max duration (14
        # days for total-calories / calories-in-heart-rate-zone). With 1-day
        # windows, pageSize must therefore be the window count, not PAGE_SIZE.
        window_count = (end - start).days
        request = {
            "range": {
                "start": {"date": {"year": start.year, "month": start.month, "day": start.day}},
                "end": {"date": {"year": end.year, "month": end.month, "day": end.day}},
            },
            "windowSizeDays": 1,
            "pageSize": window_count,
        }

        stored = 0
        page_token: str | None = None
        first = True
        while True:
            if page_token:
                request["pageToken"] = page_token
            body = self._request_with_retry(url, data_type, json_body=request)
            if body is None:
                return None if first else stored
            first = False

            windows = body.get("rollupDataPoints") or []
            points = [self._normalize_rollup_point(data_type, w) for w in windows]
            stored += storage.store(self.fetcher_name, data_type, points, fetched_at)

            page_token = body.get("nextPageToken")
            if not page_token:
                break

        log.info("stored %-28s %d rollup windows", data_type, stored)
        return stored

    def _normalize_rollup_point(self, data_type: str, window: dict) -> dict:
        """Shape a dailyRollUp window like a normal data point so the existing
        models/storage path handles it unchanged.

        Rollup windows carry ``civilStartTime``/``civilEndTime`` (a calendar day)
        and a value object keyed by the data type — but no ``name``, ``interval``
        or ``dataSource``. We synthesise a stable ``name`` (per day, so re-fetching
        a still-changing day upserts rather than duplicates) and an ``interval`` so
        ``start_time``/``end_time`` populate and the time index stays useful.
        """
        start = _civil_date_str(window.get("civilStartTime"))
        end = _civil_date_str(window.get("civilEndTime"))
        value = dict(window.get(data_type) or {})
        value.setdefault("interval", {"startTime": start, "endTime": end})
        kebab = _camel_to_kebab(data_type)
        return {
            "name": f"users/{self.user}/dataTypes/{kebab}/dailyRollup/{start}",
            "dataSource": {"recordingMethod": "DERIVED", "platform": "GOOGLE_HEALTH_ROLLUP"},
            data_type: value,
            # keep the untouched rollup fields too, for raw_json fidelity
            "civilStartTime": window.get("civilStartTime"),
            "civilEndTime": window.get("civilEndTime"),
        }

    # ------------------------------------------------------------------ #
    def _get_with_retry(self, url: str, data_type: str) -> dict | None:
        return self._request_with_retry(url, data_type)

    def _request_with_retry(self, url: str, data_type: str, *, json_body: dict | None = None) -> dict | None:
        refreshed = False  # only force a token refresh once per request
        for attempt in range(1, _MAX_RETRIES + 1):
            ratelimit.acquire()  # shared budget — blocks if we're at the per-minute cap
            try:
                token = auth.get_access_token(force_refresh=refreshed)
            except auth.AuthError as exc:
                log.error("auth %-28s %s", data_type, exc)
                return None
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            if json_body is None:
                req = urllib.request.Request(url, method="GET", headers=headers)
            else:
                headers["Content-Type"] = "application/json"
                req = urllib.request.Request(
                    url,
                    method="POST",
                    data=json.dumps(json_body).encode("utf-8"),
                    headers=headers,
                )
            try:
                with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    wait = int(exc.headers.get("Retry-After") or _DEFAULT_BACKOFF)
                    log.warning("429 %-28s retry in %ss (attempt %d)", data_type, wait, attempt)
                    time.sleep(wait)
                    continue
                # 401 = expired/invalid access token. Force one refresh + retry.
                if exc.code == 401 and not refreshed and attempt < _MAX_RETRIES:
                    log.warning("401 %-28s refreshing token + retrying", data_type)
                    refreshed = True
                    continue
                # 4xx (except the 401/429 handled above) are permanent for this
                # type: wrong dataType ID, list-unsupported (rollup-only), or a
                # missing OAuth scope. Surface the API's own message — and log at
                # WARNING, not ERROR, since these are config issues, not outages,
                # and they recur every cycle until the type/scope is fixed.
                msg = _api_error_message(exc)
                if 400 <= exc.code < 500:
                    log.warning("http %-28s %s %s", data_type, exc.code, msg)
                else:
                    log.error("http %-28s %s %s", data_type, exc.code, msg)
                return None
            except (urllib.error.URLError, TimeoutError) as exc:
                log.error("net %-28s %s", data_type, exc)
                return None
        return None
