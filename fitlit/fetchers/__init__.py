"""One thin, runnable script per data domain / cadence.

Each module is a few lines: it names itself and calls ``base.run_fetcher``.
The actual data-type list and cadence live in ``fitlit.config.FETCHERS`` so the
orchestrator and the scripts share one source of truth.

Run any fetcher on its own:    uv run python -m fitlit.fetchers.heart
"""
