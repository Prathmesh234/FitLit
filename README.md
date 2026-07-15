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
| 3. Design + build the cron fetcher | ✅ done — see [The fetcher](#the-fetcher) below |
| 4. FastAPI server for 24/7 / container | ✅ done — see [The server](#the-server-247) below |
| 5. Pydantic models + SQLite persistence | ✅ done — see [Storage](#storage-pydantic--sqlite) below |
| 6. Deploy + OAuth on a VM | ✅ done — portable systemd installer; see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| 7. Implement OAuth token refresh | ✅ done — [`fitlit/auth.py`](fitlit/auth.py), [`docs/DEPLOYMENT.md` §4](docs/DEPLOYMENT.md) |

## The fetcher

Targets the **Google Health API (v4)** — the future-proof API. The design is a
small orchestrator that owns the schedule, plus one thin script per data domain.

```
fitlit/
  config.py        settings + the fetcher → data-types → cadence map
  catalog.py       reads data/fitbit_endpoints.yaml (single source of truth)
  ratelimit.py     cross-process fixed-window rate limiter (≤100 req/min)
  client.py        Google Health client: auth, rate-limit, 429 backoff, persist
  fetchers/        8 runnable scripts, each owning a set of data types + cadence
  orchestrator.py  ticks every 10s and dispatches the fetchers that are due
```

**How it runs.** The orchestrator wakes every **10 seconds**, checks each
fetcher's cadence, and launches the due ones as subprocesses
(`python -m fitlit.fetchers.<name>`). Each fetcher loops over its data types and
calls `dataPoints.list` for each. crontab can't tick faster than once a minute,
so the orchestrator *is* the scheduler — cron just keeps it alive
(`@reboot`, see [`crontab.example`](crontab.example)).

| Fetcher | Cadence | Pulls |
|---|---|---|
| `live_activity` | 60s | steps, distance, calories, active/zone minutes, floors… |
| `heart` | 60s | live heart rate |
| `cardiac` | 5 min | electrocardiogram (ECG), irregular-rhythm-notification (AFib) |
| `body` | 30 min | weight, body fat, height, temperature, glucose |
| `nutrition` | 30 min | food + hydration logs |
| `sleep` | 60 min | sleep sessions |
| `daily_summaries` | 60 min | resting HR, VO2 max, HRV, SpO2, respiratory rate… |

All 35 Google Health data types from the catalogue are covered exactly once;
`catalog.validate_coverage()` enforces this on import. A few types
(`totalCalories`, `floors`, `caloriesInHeartRateZone`) reject `dataPoints.list`
and are fetched via `dataPoints:dailyRollUp` instead; ECG + irregular-rhythm
types sit behind their own OAuth scopes.

**Rate limiting (kept simple).** Google Health rejects with `429` past ~120
requests/min/user. A shared file-based fixed-window limiter
(`data/state/ratelimit.json`, file-locked) caps us at 100/min across *all*
fetcher processes; the client additionally honours any `Retry-After` on a 429.

### Run it

```bash
uv sync
cp .env.example .env          # paste a Google OAuth access token

# run one fetcher on demand
uv run python -m fitlit.fetchers.heart

# run the scheduler (10s loop)
uv run python -m fitlit.orchestrator          # daemon
uv run python -m fitlit.orchestrator --once   # single dispatch tick (testing)
```

Each fetcher upserts an overlapping recent window into **its own SQLite
database** (see [Storage](#storage-pydantic--sqlite)); scheduler + rate-limit
state live in `data/state/` (both gitignored). High-frequency interval/heart
streams re-read the trailing 48 hours, while daily/session metrics re-read 14
days. This catches delayed or edited Fitbit points without replaying the user's
entire history every cycle. `dataPoints.list` uses its 10,000-row page limit
(`sleep` and `exercise` are capped by Google at 25).

## Storage (Pydantic + SQLite)

Every data point the Google Health API returns shares one envelope —
`name` (a globally-unique id), a `dataSource`, and a type-specific `data` object
that is one of four shapes (Interval / Sample / Daily / Session). The storage
layer is built around that:

```
fitlit/models.py    Pydantic v2 models — the schema's single source of truth
fitlit/storage.py   SQLite engine: db-per-fetcher, table-per-type, upsert
```

**Several databases — one per fetcher** (`data/db/<fetcher>.db`). Because each
fetcher runs as its own process, separate files mean the eight cron scripts
never contend on SQLite's single-writer lock. **One table per data type** inside
each, with columns generated from the Pydantic model.

Every row stores:

* a **typed envelope** — `name` (PK), `start_time`/`end_time` + UTC offsets,
  `recording_method`, `platform`, `device_name`, `update_time`, `fetched_at`;
* **typed value columns** for the well-documented types (e.g. `steps.count`,
  `heartRate.beats_per_minute`, `weight.weight_kg`, `exercise.*`) — int64 values
  that the API sends as JSON strings are coerced automatically; and
* **`data_json` + `raw_json`** — the full type object and the entire untouched
  data point.

That last pair is the **lifetime guarantee**: *no field is ever dropped*, even
for data types we don't model with typed columns yet, or fields Google adds
later. Typed columns are a convenience projection on top of complete raw capture.

**Scale / dedup.** Writes are `INSERT … ON CONFLICT(name) DO UPDATE`, so polling
the same window every 60s never duplicates — and an edited point (new
`updateTime`) overwrites its row. Tables are indexed on `start_time` and
`fetched_at` for time-range queries; databases use WAL mode. The fetcher follows
`nextPageToken`, so a full window is captured, not just the first page.

Inspect what's stored at `GET /stats` (row counts per type per database), or
directly:

```bash
sqlite3 data/db/heart.db 'SELECT start_time, beats_per_minute FROM heartRate ORDER BY start_time DESC LIMIT 5;'
```

## The server (24/7)

For running 24/7 in a container, [`fitlit/server.py`](fitlit/server.py) is a
**FastAPI** app that is the single long-lived process: it runs the orchestrator
(the 10s scheduler) in a background thread *and* serves HTTP for health checks,
observability, and manual triggers.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness — 200 while the process is up |
| GET | `/ready` | Readiness — 200 only once a token is configured (else 503) |
| GET | `/` | Service summary |
| GET | `/fetchers` | List fetchers: cadence, scope, data types |
| GET | `/status` | Scheduler state (per-fetcher next-due) + rate-limit budget |
| GET | `/stats` | Stored row counts per data type per fetcher database |
| POST | `/fetchers/{name}/run` | Fetch one fetcher right now, return a summary |

```bash
uv run uvicorn fitlit.server:app --host 0.0.0.0 --port 8000
# interactive docs at http://localhost:8000/docs
```

`FITLIT_RUN_SCHEDULER=false` runs the API without the background scheduler (e.g.
if you prefer to run the orchestrator as a separate process/replica).

### Gmail health notifications

The independent Gmail service checks local FitLit data every 15 minutes and
sends concise sleep, workout, milestone, and fallback daily reports. It enforces
an at-most-once ledger and a hard **2–5 messages per Pacific day** policy. Gmail
uses a separate send-only OAuth token; it cannot read mail or health data.
Setup, policy, and operating commands are in
[`docs/GMAIL_SERVICE.md`](docs/GMAIL_SERVICE.md).

Optional GitHub Copilot CLI, OpenAI Codex CLI, or Claude Code enrichment adds a
small validated observation block after deterministic reservation. AI never
decides whether to send, receives only allowlisted metrics, and fails back to
the original report. See the Gmail service document and [`AGENT_START.md`](AGENT_START.md).

### Container

```bash
docker build -t fitlit .
docker run -p 8000:8000 -e GOOGLE_HEALTH_ACCESS_TOKEN=... \
  -v "$PWD/data:/app/data" fitlit
```

The image runs as a non-root user, respects `PORT` (default 8000), and its
`HEALTHCHECK` hits `/health`. Runtime data (SQLite DBs + scheduler state) lives
under `FITLIT_DATA_DIR` (default `/app/data`) — **mount a volume there to keep it
across restarts**, because the container filesystem is ephemeral. The endpoint
catalogue ships inside the image and is independent of that path.

### Deploy to Azure Container Registry + Container Apps

Azure runs `linux/amd64`, so build for that platform (required on an
Apple-Silicon Mac):

```bash
# 1. Build for amd64 and push straight to ACR (ACR Tasks build remotely):
az acr login --name <registry>
az acr build --registry <registry> --image fitlit:latest --platform linux/amd64 .

#    …or build locally and push:
docker buildx build --platform linux/amd64 -t <registry>.azurecr.io/fitlit:latest --push .
```

Then deploy. **SQLite needs persistent storage** — back `/app/data` with an
Azure Files share so a lifetime of data survives restarts/redeploys:

```bash
# 2. Create the Container Apps environment + an Azure Files storage mount
az containerapp env create -g <rg> -n fitlit-env -l <region>
az containerapp env storage set -g <rg> -n fitlit-env \
  --storage-name fitlitdata --azure-file-account-name <acct> \
  --azure-file-account-key <key> --azure-file-share-name fitlit --access-mode ReadWrite

# 3. Create the app (single replica — it owns the schedule), token as a secret
az containerapp create -g <rg> -n fitlit \
  --environment fitlit-env \
  --image <registry>.azurecr.io/fitlit:latest \
  --registry-server <registry>.azurecr.io \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --secrets ghtoken=<google-health-access-token> \
  --env-vars GOOGLE_HEALTH_ACCESS_TOKEN=secretref:ghtoken FITLIT_DATA_DIR=/app/data
# then attach the storage as a volume mounted at /app/data (via `az containerapp update --yaml`).
```

Two things that matter for correctness:

* **Pin to a single replica** (`--min-replicas 1 --max-replicas 1`). The
  scheduler runs in-process and owns the cadence; multiple replicas would
  double-fetch. To scale the API horizontally later, run extra replicas with
  `FITLIT_RUN_SCHEDULER=false` and keep exactly one scheduler.
* **Use one Azure Files share** for the data volume so every fetcher's `.db`
  file persists in the same place. If that share is **SMB**, also set
  `FITLIT_SQLITE_JOURNAL=DELETE` — SQLite's default WAL mode needs shared-memory
  locking that SMB doesn't support. (A premium **NFS** share, or a single
  replica on local/ephemeral disk if you don't need persistence, can keep WAL.)

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
