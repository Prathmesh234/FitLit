# FitLit

A cron-job based **Fitbit / Google Health API** data fetcher.

The goal of this project is to periodically (via a scheduled cron job) pull a
user's wearable data from Fitbit and persist it for later analysis. Before
writing any fetching code, we are in a **research stage**: cataloguing every
single API endpoint that is available so we can decide what to fetch, how
often, and under which scopes.

## Project status

| Stage | Status |
|-------|--------|
| 1. `uv` project initialization | ✅ done |
| 2. Research **every** Fitbit / Google Health API endpoint | ✅ done — see [`docs/fitbit-api-research.md`](docs/fitbit-api-research.md) |
| 3. Design the cron fetcher | ⬜ not started |
| 4. Implement OAuth + fetching | ⬜ not started |

## ⚠️ Important: two APIs exist right now (June 2026)

Fitbit is owned by Google, and the platform is **mid-migration**:

- **Legacy Fitbit Web API** (`https://api.fitbit.com`) — the long-standing API.
  **Deprecated; being turned down in September 2026.**
- **Google Health API** (`https://health.googleapis.com`, `v4`) — the new
  "Google's latest Fitbit API", launched at Google I/O **May 2026**. This is
  the future-proof target for any new integration.

Both run side by side from May → September 2026. **New builds should target the
Google Health API.** Full details, endpoint-by-endpoint, are in the research
doc.

## Research deliverables

- **[`docs/fitbit-api-research.md`](docs/fitbit-api-research.md)** — the full,
  human-readable catalogue of every endpoint, grouped by domain, for both APIs.
- **[`data/fitbit_endpoints.yaml`](data/fitbit_endpoints.yaml)** — the same
  catalogue in machine-readable form, ready to drive the future cron fetcher.

## Development

```bash
uv sync          # create the virtual environment
uv run main.py   # run the placeholder entrypoint
```
