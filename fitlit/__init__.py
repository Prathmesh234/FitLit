"""FitLit — cron-orchestrated fetcher for the Google Health API (v4).

The package is intentionally small and flat:

    config.py        settings + the fetcher -> data-types -> cadence map
    catalog.py       reads data/fitbit_endpoints.yaml (single source of truth)
    ratelimit.py     simple cross-process fixed-window rate limiter
    client.py        Google Health API client (auth, rate-limit, persist)
    fetchers/        one thin runnable script per data domain / cadence
    orchestrator.py  ticks every 10s and dispatches the due fetchers
"""

__all__ = ["config", "catalog", "ratelimit", "client"]
