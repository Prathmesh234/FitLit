"""Shared run-loop for every fetcher script.

A fetcher does one thing: walk its configured data types and pull whatever the
Google Health API has for each.  The rate limiter (shared, cross-process) and
persistence live in the client, so this stays a simple loop.
"""
from __future__ import annotations

import logging
import sys

from fitlit import config
from fitlit.client import GoogleHealthClient, MissingTokenError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("fitlit.fetcher")


def fetch_once(name: str) -> dict:
    """Run the named fetcher once: fetch every data type it owns.

    Returns a result summary. Raises ``KeyError`` for an unknown fetcher and
    ``MissingTokenError`` if no access token is configured — callers (CLI,
    HTTP API) decide how to surface those.
    """
    fetcher = config.FETCHERS.get(name)
    if fetcher is None:
        raise KeyError(name)

    log.info("[%s] start — %d data types @ every %ds",
             name, len(fetcher.data_types), fetcher.interval_seconds)

    client = GoogleHealthClient(name)
    fetched = 0
    for data_type in fetcher.data_types:
        if client.list_data_points(data_type) is not None:
            fetched += 1

    total = len(fetcher.data_types)
    log.info("[%s] done — %d/%d data types fetched", name, fetched, total)
    return {"fetcher": name, "fetched": fetched, "total": total}


def run_fetcher(name: str) -> int:
    """CLI wrapper around :func:`fetch_once` returning a process exit code."""
    try:
        fetch_once(name)
    except KeyError:
        log.error("unknown fetcher %r (known: %s)", name, ", ".join(config.FETCHERS))
        return 1
    except MissingTokenError as exc:
        log.error("[%s] %s", name, exc)
        return 1
    return 0


def main(name: str) -> None:
    sys.exit(run_fetcher(name))
