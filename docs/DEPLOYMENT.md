# FitLit — VM deployment and operations

This guide covers a durable Linux VM deployment, OAuth onboarding, systemd
services, and operational security. Examples are intentionally independent of
one cloud, username, home directory, or clone location.

---

## 0. Status at a glance

| Piece | State |
|---|---|
| Endpoint catalogue (both APIs) | ✅ done |
| Fetchers + 10s orchestrator | ✅ done |
| Rate limiting | ✅ done |
| Pydantic models + SQLite storage | ✅ done |
| FastAPI server + Dockerfile | ✅ done |
| Get a Google OAuth token (consent flow) | ✅ done — refresh token captured, live data flowing |
| OAuth refresh handling in code | ✅ done — [`fitlit/auth.py`](../fitlit/auth.py) |
| Portable systemd installer | ✅ done — [`scripts/install_services.py`](../scripts/install_services.py) |

---

## 1. Prepare the VM (one-time)

Assumes Ubuntu 22.04+/Debian. SSH in first.

```bash
# system deps
sudo apt-get update && sudo apt-get install -y git sqlite3 curl

# uv (Python toolchain) — installs to ~/.local/bin
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc        # or: export PATH="$HOME/.local/bin:$PATH"

# clone + install
git clone <your-repo-url> fitlit && cd fitlit
uv sync                 # creates .venv from uv.lock

# smoke test (no token yet — should print a clean "no token" message)
uv run python -m fitlit.fetchers.heart
```

**Networking:** if you want to reach the FastAPI server from outside the VM,
open the port in the Azure **Network Security Group** (inbound rule for the
chosen `PORT`, e.g. 8000). For a private/cron-only setup you don't need this.

---

## 2. OAuth — getting a token  ⬅ the main missing piece

FitLit targets the **Google Health API**, which uses **Google OAuth 2.0**.
Access tokens are **short-lived (~1 hour)**; a refresh token is long-lived and
is what we exchange for new access tokens. This is a **one-time** manual setup to
get the first refresh token, then the code (section 4) keeps it fresh forever —
you do **not** re-consent on every run.

**Why OAuth and not a service account / API key?** Fitbit/Google Health
*personal* data belongs to the end user's Google account, so reading it legally
requires that user's OAuth consent. Service accounts (incl. domain-wide
delegation, which is Workspace-only) and API keys **cannot** access a consumer
`@gmail.com` account's health data. OAuth-with-refresh is the only viable path —
and it's a "set it once, runs 24/7" credential, which is exactly what we want.

> ### ⚠️ Two things that decide whether 24/7 actually lasts
>
> 1. **Publish the OAuth app to "Production".** While the OAuth consent screen is
>    in **"Testing"** publishing status, Google **expires refresh tokens after 7
>    days** — the service would silently die after a week. In Cloud Console →
>    **OAuth consent screen → Publishing status → "PUBLISH APP"**. For a personal
>    app with just yourself as the user you can publish without full verification
>    (you may see an "unverified app" warning at consent — click through as the
>    owner). Do this **before** minting the refresh token below.
> 2. **The refresh token is portable.** It isn't tied to a machine. Mint it
>    wherever a browser is easy (your laptop — see 2c) and paste it into the VM's
>    `.env`. A Production-mode refresh token then lives indefinitely (only dies on
>    revoke, 6-month inactivity, or password change).

### 2a. Google Cloud Console (one-time, in a browser)

1. Create / pick a Google Cloud project.
2. **Enable the Google Health API** for the project.
3. **OAuth consent screen:** configure it and add the scopes FitLit needs. The
   exact scope strings live in [`data/fitbit_endpoints.yaml`](../data/fitbit_endpoints.yaml)
   under `google_health_api.scopes` — the read-only set:
   - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
   - `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`
   - `https://www.googleapis.com/auth/googlehealth.location.readonly`
4. Add yourself as a **test user** (while the app is unverified).
5. **Create an OAuth client ID** (type *Web application* or *Desktop app*).
   Note the **Client ID** and **Client secret**. If Web, add the redirect URI
   `http://localhost:8765/callback` (must match exactly — Google rejects raw
   public IPs; only `http://localhost`/`127.0.0.1` may be plain `http`).
6. **Publish the app to Production** (Publishing status → "PUBLISH APP") so the
   refresh token doesn't expire in 7 days — see the ⚠️ note above.

### 2b. Authorize once and capture the refresh token

Hit the consent URL with **`access_type=offline`** and **`prompt=consent`** —
those two are what make Google return a `refresh_token`:

```
https://accounts.google.com/o/oauth2/v2/auth
  ?client_id=<CLIENT_ID>
  &redirect_uri=<REDIRECT_URI>
  &response_type=code
  &access_type=offline
  &prompt=consent
  &scope=<space-separated scopes from 2a>
```

Approve in the browser, copy the `code` from the redirect, then exchange it:

```bash
curl -s https://oauth2.googleapis.com/token \
  -d client_id=<CLIENT_ID> \
  -d client_secret=<CLIENT_SECRET> \
  -d code=<CODE_FROM_REDIRECT> \
  -d grant_type=authorization_code \
  -d redirect_uri=<REDIRECT_URI>
```

The JSON response contains `access_token` (use now) and **`refresh_token`**
(save this — it's the durable credential).

> **Easier:** instead of curling by hand, once `GOOGLE_HEALTH_CLIENT_ID` and
> `GOOGLE_HEALTH_CLIENT_SECRET` are in `.env`, run
> `uv run python -m fitlit.auth login`. It prints the consent URL, takes the
> `code` from the redirect, exchanges it, caches the access token, and prints
> the `GOOGLE_HEALTH_REFRESH_TOKEN=` line to paste into `.env`. This is the
> built helper described in section 4.

### 2c. Recommended for a headless VM: mint the token on your laptop

The consent redirect goes to `http://localhost:8765/...`, which only resolves on
the machine running the browser. On a **headless VM there is no browser**, and
pointing Google at the VM's public IP doesn't work (Google forbids non-localhost
`http` and raw-IP redirect URIs). Two ways to bridge that — the second is what we
landed on as simplest:

- **SSH tunnel.** Run a local listener on the VM and forward your laptop's port
  to it: `ssh -N -L 8765:localhost:8765 <vm>`, then approve in the laptop browser
  — the redirect tunnels back to the VM and the code is captured there.
- **Mint on the laptop, paste the token (simplest).** Because the refresh token
  is **portable**, just do the whole consent flow on your laptop (browser +
  `localhost:8765` are the same machine, so the redirect resolves instantly — no
  tunnel, no hanging consent page). Then copy the resulting
  `GOOGLE_HEALTH_REFRESH_TOKEN=...` value and paste it into the **VM's** `.env`.
  Clone the repo on the laptop and run `uv run python -m fitlit.auth login` (or
  the auto-capturing `scripts/oauth_capture.py`), grab the printed token, done.

Either way the VM only ever needs the three values in its `.env`
(`GOOGLE_HEALTH_CLIENT_ID`, `GOOGLE_HEALTH_CLIENT_SECRET`,
`GOOGLE_HEALTH_REFRESH_TOKEN`); it never has to run a browser.

---

## 3. Set the OAuth env vars on the VM

Create `.env` in the project root (it's gitignored). Start from the template:

```bash
cp .env.example .env
nano .env
```

Fill in the **OAuth refresh** set (the recommended, unattended-safe path — the
service mints access tokens itself):

```ini
GOOGLE_HEALTH_CLIENT_ID=<client id>
GOOGLE_HEALTH_CLIENT_SECRET=<client secret>
GOOGLE_HEALTH_REFRESH_TOKEN=<refresh_token from step 2b / `auth login`>
GOOGLE_HEALTH_USER=me

# Persistence + server (see .env.example for the full list)
FITLIT_DATA_DIR=./data
# PORT=8000
```

(For a quick one-off you can instead set just `GOOGLE_HEALTH_ACCESS_TOKEN=<token>`
— but it expires in ~1h and does **not** self-renew, so it's not for a service.)

Verify a real call is attempted and the token refreshes automatically:

```bash
uv run python -m fitlit.auth token       # prints a freshly-minted access token
uv run python -m fitlit.fetchers.heart   # makes a real request, refreshing as needed
uv run python scripts/seed_dummy_data.py # or seed dummy rows to eyeball the DB
sqlite3 data/db/heart.db "SELECT COUNT(*) FROM heartRate;"
```

The minted access token is cached at `data/state/token.json` (mode `600`) and
shared across all fetcher processes; it's refreshed ~60s before expiry.

---

## 4. OAuth refresh in the code  ✅ done

Built in [`fitlit/auth.py`](../fitlit/auth.py). How it works:

- **`auth.get_access_token()`** returns a currently-valid token. It serves a
  cached token from `data/state/token.json` while fresh, and otherwise refreshes
  via `POST https://oauth2.googleapis.com/token` (`grant_type=refresh_token`),
  ~60s before expiry (`GOOGLE_OAUTH_REFRESH_LEEWAY`). The refresh runs under an
  `fcntl` exclusive lock with a cache re-check, so the eight fetcher processes
  share one refresh instead of stampeding Google's endpoint. The cache file is
  written `0600` (it holds a live credential) and lives under `data/state/`
  (gitignored, and on the persistent data volume).
- **`fitlit/client.py`** fetches a token per request (cheap — cached) and on a
  `401` forces exactly one refresh + retry before giving up.
- **`auth.is_configured()`** backs `GET /ready` (and `/status`'s
  `token_configured`): ready once either OAuth refresh creds *or* a static token
  is present.
- **`python -m fitlit.auth login`** runs the section-2b consent exchange end to
  end and prints the `GOOGLE_HEALTH_REFRESH_TOKEN=` line to paste into `.env`.
  **`python -m fitlit.auth token`** prints a freshly-minted access token (debug).

Fallback: if no refresh creds are set but `GOOGLE_HEALTH_ACCESS_TOKEN` is, that
static token is used as-is (can't self-renew). Configure refresh for any
unattended run.

---

## 5. Run it 24/7 on a VM (systemd)

Render units from the current clone, current user, and discovered `uv` binary,
then install and start them:

```bash
uv run python scripts/install_services.py
sudo uv run python scripts/install_services.py --install --start
```

The installer renders `fitlit.service`, `fitlit-gc.service`, and
`fitlit-gmail.service` into `data/state/systemd/`, installs them with the timer,
and never hardcodes a machine identity in the repository.

Day-to-day management:

```bash
sudo systemctl status fitlit        # is it up?
sudo systemctl restart fitlit       # after a git pull / .env change
journalctl -u fitlit -f             # follow logs
curl http://localhost:8000/status   # scheduler state + rate-limit budget
curl http://localhost:8000/stats    # stored row counts per type per DB
```

> The API is bound to **`127.0.0.1:8000`** (localhost only) — it has no auth, so
> it must not be exposed. Reach it from your laptop with an SSH tunnel:
> `ssh -L 8000:localhost:8000 fitlit`, then open `localhost:8000` locally. See
> §8 before ever changing this. 

Alternative (cron-only, no API): a `@reboot` entry running
`uv run python -m fitlit.orchestrator` — see [`crontab.example`](../crontab.example).

---

## 6. Quick reference — env vars

| Var | Used now? | Purpose |
|---|---|---|
| `GOOGLE_HEALTH_ACCESS_TOKEN` | optional | Static bearer token (fallback / testing; can't self-renew) |
| `GOOGLE_HEALTH_USER` | ✅ | `me` or explicit user id |
| `GOOGLE_HEALTH_CLIENT_ID` | ✅ | OAuth client id, for refresh |
| `GOOGLE_HEALTH_CLIENT_SECRET` | ✅ | OAuth client secret, for refresh |
| `GOOGLE_HEALTH_REFRESH_TOKEN` | ✅ | Long-lived token to mint access tokens |
| `FITLIT_DATA_DIR` | ✅ | Where SQLite DBs + state live |
| `FITLIT_SQLITE_JOURNAL` | ✅ | `WAL` (local disk) / `DELETE` (network share) |
| `PORT` / `HOST` | ✅ | Server bind |
| `FITLIT_RUN_SCHEDULER` | ✅ | Run the scheduler in-process (default true) |
| `FITLIT_TICK_SECONDS` | ✅ | Orchestrator tick (default 10) |
| `FITLIT_AI_ENABLED` | optional | Enable validated AI observations |
| `FITLIT_AI_PROVIDER` | optional | `auto`, `copilot`, `codex`, or `claude` |

---

## 7. Known issues & scaling roadmap

Captured from the first live run on the VM (2026-06-20). None of these block
data capture today — they're the next workstreams.

### 7a. Some data types returned `400 Bad Request` — fixed 2026-06-20

The first live run logged 11 data types that the API rejected with `400`. Root
cause was **three** distinct issues (not the camelCase→kebab mapping), now fixed:

1. **List-unsupported (rollup-only) — 3 types.** `totalCalories`, `floors`,
   `caloriesInHeartRateZone` reject `dataPoints.list` and only support
   aggregation. They're now fetched via `dataPoints:dailyRollUp`
   ([`client.py`](../fitlit/client.py) `daily_rollup_data_points`), keyed per
   day so re-fetching a still-changing day upserts instead of duplicating.
2. **Mis-identified medical types — 3 → 2.** The catalogue invented
   `ecgMeasurement`, `ecgRhythmClassification`, `afibAnalysisWindow`. The real v4
   dataTypes are **`electrocardiogram`** (rhythm classification is a field on it)
   and **`irregular-rhythm-notification`**. These need their own scopes —
   `googlehealth.ecg.readonly` and `googlehealth.irn.readonly` — now added to
   `config.SCOPES`. **They return `403` until you re-consent** (see below).
3. **Phantom types — 5 removed.** `goals`, `location`, `respiratoryRate`,
   `bodyTemperature`, `sleepSummary` have no v4 dataType collection; the data
   lives in types we already capture (`dailyRespiratoryRate` /
   `respiratoryRateSleepSummary`, `coreBodyTemperature`, the `sleep` session's
   nested summary). The standalone `location` fetcher was dropped with them.

After the fix the catalogue holds **35** Google Health dataTypes across **7**
fetchers; `catalog.validate_coverage()` enforces it on import.

**Re-consent for ECG/IRN (one-time).** The new scopes aren't in the existing
refresh token, so ECG/IRN stay `403` until you re-run consent:

```bash
uv run python scripts/oauth_capture.py     # or: uv run python -m fitlit.auth login
sudo systemctl restart fitlit              # pick up the new refresh token
```

Until then those two types are logged at `WARNING` and skipped (non-fatal), and
all 33 other types capture normally.

Also note: `heartRate` (and other **Sample**-shaped types) carry their timestamp
in `data.sampleTime.physicalTime`, not a `startTime`/`endTime` envelope — so the
typed `start_time` column is empty for them (the value is still fully captured in
`data_json`/`raw_json`). Mapping `sampleTime → start_time` would make the
`start_time` index useful for those types too.

### 7b. Keeping SQLite from getting overloaded

> **Update 2026-06-25.** Point 3 (archive cold data) is now implemented as a
> lossless GC daemon — see **[§7c](#7c-garbage-collector-daemon)**. Point 1
> (incremental fetch) remains the highest-leverage open item: with full-history
> re-fetching, `live_activity.db` grew to ~25 GB in 6 days and filled a 31 GB
> disk. Until it lands, run the GC with a short `--older-than` (e.g. 1 day) to
> keep the footprint bounded.

The DB is small today (~3 MB) with plenty of disk, but two design facts drive
unbounded growth/cost over a lifetime of polling. Levers, highest leverage first:

1. **Incremental fetch windows (do first).** Every fetcher currently pulls the
   *entire* available history each cycle (`list_data_points` sends no time
   filter), then dedups via `INSERT … ON CONFLICT(name)`. That's why the first
   `heart` run took ~2.5 min and exhausted the 100/min rate budget. Persist each
   fetcher's last-success time and pass a `startTime`/`endTime` (or "since")
   filter so each run pulls only *new* points. Cuts runtime, API quota, and write
   churn dramatically. (Doesn't shrink final size — same unique points — but
   stops the wasteful re-scan.)
2. **Drop redundant `data_json`.** Each row stores both `data_json` (the type
   sub-object) and `raw_json` (the whole point, a superset). Keeping only
   `raw_json` roughly halves text storage with no loss.
3. **Partition + archive for lifetime scale.** Respect the "never drop a field"
   guarantee without unbounded single files: roll a DB per month
   (`heart_2026_06.db`), and/or archive cold partitions to compressed
   JSONL/Parquet on disk or blob storage; keep recent hot in SQLite. Run periodic
   `PRAGMA wal_checkpoint` + `VACUUM` to reclaim space.
4. **Graduate the store if volume demands it.** At sustained high-frequency
   capture (heartRate can be tens of thousands of points/day), a time-series
   engine (TimescaleDB/Postgres, DuckDB, or partitioned Parquet) scales better
   than SQLite for analytics. SQLite is fine for now given per-fetcher DBs +
   indexes; the risks to watch are disk fill, WAL bloat, and query latency as
   tables reach millions of rows.

### 7c. Garbage-collector daemon

[`fitlit/gc.py`](../fitlit/gc.py) + [`fitlit/gc_daemon.py`](../fitlit/gc_daemon.py)
implement a **lossless** archive-and-prune GC, deployed via
[`deploy/fitlit-gc.service`](../deploy/fitlit-gc.service). For data older than a
threshold it: (1) archives every row — all columns incl. `raw_json` — to
`data/archive/<fetcher>/<data_type>.jsonl.gz`; (2) verifies the archived line
count equals the rows selected before deleting anything; (3) consolidates each
archived `(data_type, UTC-day)` into one `_gc_summary` row; (4) prunes the
archived rows and `VACUUM`s to return space to the OS. `gc.restore_archive()` is
the exact inverse, so the operation is fully reversible.

```bash
uv run python -m fitlit.gc_daemon --dry-run            # report, change nothing
uv run python -m fitlit.gc_daemon --once --older-than 1  # one sweep, keep 1 day hot
sudo systemctl enable --now fitlit-gc.service          # run it as a daemon
```

**Caveat on a full disk.** `VACUUM` and lossless archiving both need free space;
if the disk is already full, free a little first (or migrate to a larger disk),
then run the GC. Because the data here is still <7 days old, the *immediate*
bloat is intra-day re-fetch duplication, not aged data — so pair the GC with the
§7b point 1 incremental-fetch fix for a permanent solution.

---

## 8. Security posture

Recommended controls:

| Control | State |
|---|---|
| `.env` (holds refresh token) | `chmod 600`, gitignored — never `git add -f` it |
| `data/state/token.json` (cached access token) | `chmod 600` (auth.py writes it `0600`) |
| `~/.ssh`, `authorized_keys` | `700` / `600` |
| SSH password auth | disabled (`PasswordAuthentication no`) — key-only |
| SSH root login | disabled (`PermitRootLogin no`, drop-in `99-fitlit-hardening.conf`) |
| FastAPI bind | `127.0.0.1:8000` — **the API has no auth**; reach it via SSH tunnel |

Operating rules:

- **The OAuth refresh token is a master key** to the health data — readable from
  anywhere, no device needed. Never commit it, never paste it into chat/email; if
  it leaks, revoke at <https://myaccount.google.com/permissions> and re-mint.
- Treat any account with passwordless sudo as root-equivalent. Never
  share/commit a private key; prefer a passphrase on client keys.
- **Don't expose port 8000.** It has no authentication (`/fetchers/{name}/run`,
  `/stats`, … are open). Keep it bound to localhost + closed in the NSG. Only
  publish it behind auth + HTTPS (e.g. a reverse proxy) if ever needed.
- Optional defense-in-depth: a host firewall (`ufw allow 22/tcp` **first**, then
  `ufw enable`) — low marginal value now that 8000 is localhost-bound and the NSG
  only opens 22, and it carries lock-out risk over SSH, so it's left off.
