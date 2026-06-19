"""Minimal Google Health API (v4) client.

One generic method — ``list_data_points`` — works for every data type thanks to
the unified ``users.dataTypes.dataPoints`` design:

    GET /v4/users/{user}/dataTypes/{dataType}/dataPoints

Responsibilities kept here so the fetchers stay trivial:

    * attach the OAuth Bearer token (assumed present, read from .env),
    * pass through the shared rate limiter before every call,
    * honour 429 / Retry-After with a bounded retry/backoff,
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
from datetime import datetime, timezone

from fitlit import config, ratelimit, storage

log = logging.getLogger("fitlit.client")

_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 5  # seconds, used when a 429 carries no Retry-After


class MissingTokenError(RuntimeError):
    """Raised when no access token is configured."""


def _camel_to_kebab(name: str) -> str:
    """heartRate -> heart-rate, activeZoneMinutes -> active-zone-minutes.

    Google Health uses kebab-case for the dataType path segment (snake_case is
    only for filters).  The catalogue stores the camelCase identifiers.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


class GoogleHealthClient:
    def __init__(self, fetcher_name: str) -> None:
        self.fetcher_name = fetcher_name
        self.token = config.ACCESS_TOKEN
        self.user = config.API_USER

    # ------------------------------------------------------------------ #
    def list_data_points(self, data_type: str) -> int | None:
        """Fetch + persist every available data point for one data type.

        Follows ``nextPageToken`` so a whole window is captured, upserting each
        page into the fetcher's SQLite database. Returns the number of points
        stored, or ``None`` if the very first request failed (logged, never
        raised, so one bad type can't sink the sweep).
        """
        if not self.token:
            raise MissingTokenError(
                "GOOGLE_HEALTH_ACCESS_TOKEN is not set — copy .env.example to .env "
                "and fill it in."
            )

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
    def _get_with_retry(self, url: str, data_type: str) -> dict | None:
        for attempt in range(1, _MAX_RETRIES + 1):
            ratelimit.acquire()  # shared budget — blocks if we're at the per-minute cap
            req = urllib.request.Request(
                url,
                method="GET",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
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
                log.error("http %-28s %s %s", data_type, exc.code, exc.reason)
                return None
            except (urllib.error.URLError, TimeoutError) as exc:
                log.error("net %-28s %s", data_type, exc)
                return None
        return None
