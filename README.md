# FitLit

A self-hosted **personal assistant**, built on a cron-job based
**Fitbit / Google Health API** data fetcher.

FitLit periodically pulls the owner's wearable data from Fitbit and persists it
for analysis, then answers questions about it over Gmail and a private Telegram
bot. Wearable health is its deepest domain rather than its boundary: the
[personal section](#the-personal-section) adds scheduled tasks that have nothing
to do with fitness, sharing the same assistant, the same channels, and the same
headless harness.

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
| 8. Personal (non-health) scheduled tasks | ✅ done — see [The personal section](#the-personal-section) below |

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
uv run uvicorn fitlit.server:app --host 127.0.0.1 --port 8000
# interactive docs at http://localhost:8000/docs
```

`FITLIT_RUN_SCHEDULER=false` runs the API without the background scheduler (e.g.
if you prefer to run the orchestrator as a separate process/replica).
The API contains private health data and write endpoints and has no application
login layer. Keep it loopback-only or place it behind an identity-aware proxy;
never expose port 8000 directly to the internet.

### Gmail health notifications

The independent Gmail service checks local FitLit data every 15 minutes and
sends rich morning sleep/recovery briefs, workout and milestone notices, and an
8 PM Pacific day-in-review with movement rhythm, workout ledger, recovery
signals, comparisons, and day-specific facts. It enforces an at-most-once
ledger and a hard **2–5 messages per Pacific day** policy. Gmail uses a separate
send-only OAuth token; it cannot read mail or health data.
Setup, policy, and operating commands are in
[`docs/GMAIL_SERVICE.md`](docs/GMAIL_SERVICE.md).

Every Sunday at 8 PM Pacific it also sends an in-depth weekly performance
catalog: workout ledger, trusted exercise calories and load, daily movement,
sleep debt/consistency, HRV, resting heart rate, blood oxygen, respiratory rate,
coverage-aware weekly trends, data-quality notes, and next-week priorities.

Optional enrichment for proactive reports adds a small validated observation
block after deterministic reservation. It never decides whether to send,
receives only allowlisted metrics, and fails back to the original report. This
is separate from the provider-centered conversational inbox described below.
See the Gmail service document and [`AGENT_START.md`](AGENT_START.md).

An optional read-only email command channel lets the configured user send
`FitLit Ask:` questions to the same Gmail address and receive a provider-drafted
threaded answer grounded in local health summaries. It uses a separate
`gmail.readonly` token, accepts only exact self-addressed commands, stores no
question bodies, and cannot alter FitLit data.
A Gmail-only daemon checks for those commands every 5 seconds using the
already-approved Gmail OAuth credentials. It is a simple systemd polling loop:
no public endpoint, Pub/Sub topic, service account, or additional cloud
authorization. The 15-minute timer stays enabled as a reliability fallback.
See [`docs/GMAIL_SERVICE.md`](docs/GMAIL_SERVICE.md).

The first successful command thread becomes the only chain polled afterward.
Only its latest five messages are exposed in-memory to the selected headless
harness, with the newest user turn authoritative; unrelated mail and older
thread content are excluded. The global `HARNESS` setting selects Claude,
Codex, Copilot, or OpenCode for every model-backed workflow; Claude Code is the
default, using Sonnet 5 at high reasoning effort. It receives one compact
query-filtered
`citable_evidence` map of exact `path: value` pairs, produces natural
conversational text, copies evidence keys verbatim for health claims, and
requests artifact types when needed. The runtime appends exact path/value
evidence and deterministically wraps a strictly validated semantic fragment in
the fixed responsive FitLit theme, plus evidence-only XLSX, DOCX, HTML, or PNG
artifacts.
For complex work the selected harness may use at most two first-level native
subagents. Every harness can also call a read-only SQLite FTS5 memory tool to
search archived Telegram transcripts when the owner refers to an earlier chat.
Temporary harness state and artifacts are deleted after delivery, and email
bodies are never stored in SQLite. Configuration, authentication, delegation,
and memory details are in
[`docs/HEADLESS_HARNESSES.md`](docs/HEADLESS_HARNESSES.md).

### Private Telegram bot

An optional official Telegram Bot API channel provides the same grounded agent
in a private bot chat with no public webhook. A one-time random pairing command
binds one exact numeric Telegram user ID; all other users and non-private chats
are silently ignored. The daemon persists complete owner-only indexed
conversations and supplies the active transcript with an explicit
`**LATEST QUERY**` marker to the isolated headless provider, compacting only
that provider view when the composed request would exceed the byte budget.
`/new` archives the current thread without deleting it; `/reset` is disabled.
Archived turns remain searchable through the read-only transcript-memory tool.
The globally selected harness produces the response, and
evidence-only XLSX, DOCX, safe themed HTML, and locally rendered PNG screenshot
results can be delivered. Official Bot API long polling returns immediately on
message arrival, while a typing heartbeat covers provider generation time; no
WebSocket or public webhook is required for this single-user deployment.
Telegram's model is set independently from Gmail; both default to Sonnet 5 at
high reasoning effort under `HARNESS=claude`. Plain text replies never depend on harness HTML, and
any HTML artifact uses the same mobile-first FitLit email theme.
Setup, privacy boundaries, pairing, and operating commands are in
[`docs/TELEGRAM_SERVICE.md`](docs/TELEGRAM_SERVICE.md).

## The personal section

`personal/` is the half of the assistant that is not about wearables:
scheduled personal tasks, a durable ledger at `data/state/personal.db`, and the
`personal-overview` / `personal-coffee` skills. It shares `.env`, the data
directory, the Gmail sender, and the harness with `fitlit/`, but keeps its own
ledger so a personal job can neither consume nor be throttled by the health
send budget.

The first task is a **daily coffee-shop recommendation**: every morning at 9:00
AM Pacific, one email with a Seattle coffee shop to visit that day — roughly a
15-minute drive from South Lake Union, quiet enough to sit and work in, and not
one of the shops sent recently.

What makes it trustworthy is that it refuses to answer from the model's memory.
The task runs the harness with exactly `WebSearch` and `WebFetch`, asks for
plain Google-style queries, and requires the shop's hours to be read off its own
site or Google Business listing that morning. The result is then checked against
rules a JSON schema cannot express: the harness envelope must show a search
actually happened, `verified_date` must be today in Pacific, the shop must be
open today, the sources must be at least two real URLs on two different hosts,
the drive must be inside the ceiling, and the room must not be a loud one.
Anything that fails is rejected and asked again.

Duplicates are handled by a normalized shop key, so `Victrola Coffee Roasters`
and `victrola coffee` are one shop. A repeat is retried; a repeat that survives
every attempt is sent anyway and labeled with the date it was last suggested.

The owner's verdict on a shop is recorded and honoured — `blocked` is a
permanent exclusion applied before the model sees the request, and every other
sentiment becomes standing taste guidance:

```bash
uv run python -m personal.runner run coffee --dry-run
uv run python -m personal.runner feedback "Victrola Coffee" disliked --note "too loud"
uv run python -m personal.runner history coffee
```

The conversational agent is given a read-only view of the ledger, so asking
"where am I getting coffee today?" over Telegram reports the shop that was
actually emailed rather than inventing a new one.

Delivery is a systemd timer carrying `America/Los_Angeles`, so 9:00 AM stays
9:00 AM across the PST/PDT change. Requires `HARNESS=claude`. Setup, the full
constraint list, and how to add a task are in
[`docs/PERSONAL.md`](docs/PERSONAL.md).

### Container

```bash
docker build -t fitlit .
docker run -p 127.0.0.1:8000:8000 -e GOOGLE_HEALTH_ACCESS_TOKEN=... \
  -v "$PWD/data:/app/data" fitlit
```

The image runs as a non-root user, respects `PORT` (default 8000), and its
`HEALTHCHECK` hits `/health`. Runtime data (SQLite DBs + scheduler state) lives
under `FITLIT_DATA_DIR` (default `/app/data`) — **mount a volume there to keep it
across restarts**, because the container filesystem is ephemeral. The endpoint
catalogue ships inside the image and is independent of that path.
The Docker build uses a deny-all context allowlist and explicit `COPY`
instructions, so `.env`, Gmail state, databases, archives, and local coaching
documents cannot be included in the image.

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
  --target-port 8000 --ingress internal \
  --min-replicas 1 --max-replicas 1 \
  --secrets ghtoken=<google-health-access-token> \
  --env-vars GOOGLE_HEALTH_ACCESS_TOKEN=secretref:ghtoken FITLIT_DATA_DIR=/app/data
# then attach the storage as a volume mounted at /app/data (via `az containerapp update --yaml`).
```

Keep Container Apps ingress `internal` unless an identity-aware authentication
layer is configured in front of FitLit. The API itself intentionally assumes a
private loopback or private-network deployment and must not receive anonymous
internet traffic.

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
