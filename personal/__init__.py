"""FitLit's personal section — the non-health half of the assistant.

`fitlit/` owns wearable ingestion, health analysis, and the Gmail/Telegram
channels. `personal/` owns everything else the assistant does for its owner:
scheduled personal tasks, their durable system of record, and the personal
skills that describe each domain.

The two halves share one identity. A personal task reuses `fitlit.config` for
paths and dotenv loading, `fitlit.gmail_client` for delivery, and the same
headless harness, but keeps its own ledger (`data/state/personal.db`) so a
personal job can never consume the health notification budget.
"""
