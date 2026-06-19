"""Minimal Google Health API (v4) client.

One generic method — ``list_data_points`` — works for every data type thanks to
the unified ``users.dataTypes.dataPoints`` design:

    GET /v4/users/{user}/dataTypes/{dataType}/dataPoints

Responsibilities kept here so the fetchers stay trivial:

    * attach the OAuth Bearer token (assumed present, read from .env),
    * pass through the shared rate limiter before every call,
    * honour 429 / Retry-After with a bounded retry/backoff,
    * persist each raw response under data/raw/<fetcher>/<dataType>/.

Uses only the standard library (urllib) so it runs with no extra installs.
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

from fitlit import config, ratelimit

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
    def list_data_points(self, data_type: str) -> dict | None:
        """Fetch the available data points for a single data type.

        Returns the parsed JSON body, or ``None`` if the call ultimately
        failed (logged, never raised, so one bad type can't sink the sweep).
        """
        if not self.token:
            raise MissingTokenError(
                "GOOGLE_HEALTH_ACCESS_TOKEN is not set — copy .env.example to .env "
                "and fill it in."
            )

        path = f"/v4/users/{urllib.parse.quote(self.user)}/dataTypes/{_camel_to_kebab(data_type)}/dataPoints"
        query = urllib.parse.urlencode({"pageSize": config.PAGE_SIZE})
        url = f"{config.BASE_URL}{path}?{query}"

        body = self._get_with_retry(url, data_type)
        if body is not None:
            self._persist(data_type, body)
        return body

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
                    payload = json.loads(resp.read().decode("utf-8"))
                    log.info("ok  %-28s %s", data_type, resp.status)
                    return payload
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

    # ------------------------------------------------------------------ #
    def _persist(self, data_type: str, body: dict) -> None:
        out_dir = config.RAW_DIR / self.fetcher_name / data_type
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (out_dir / f"{stamp}.json").write_text(json.dumps(body, indent=2))
