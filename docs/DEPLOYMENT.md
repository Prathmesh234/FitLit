# FitLit — Azure VM deployment & remaining work

This is the running checklist for getting FitLit live on an Azure VM and the
work still outstanding (chiefly **OAuth**). Today the code assumes a Google
access token is simply present in the environment; this doc covers how to
obtain it, how to set the env vars, and what we still need to build to keep it
fresh for a lifetime of polling.

---

## 0. Status at a glance

| Piece | State |
|---|---|
| Endpoint catalogue (both APIs) | ✅ done |
| Fetchers + 10s orchestrator | ✅ done |
| Rate limiting | ✅ done |
| Pydantic models + SQLite storage | ✅ done |
| FastAPI server + Dockerfile | ✅ done |
| **Get a Google OAuth token (consent flow)** | ⬜ **TODO — manual, one-time** |
| **OAuth refresh handling in code** | ⬜ **TODO — must build** |
| Run on the VM (systemd) | ⬜ TODO |

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
is what we exchange for new access tokens. This is a one-time manual setup to
get the first refresh token, then the code (section 4) keeps it fresh.

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
   Note the **Client ID** and **Client secret**. If Web, add a redirect URI
   (e.g. `http://localhost:8765/callback` for the local exchange).

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

> We'll add a tiny helper (`uv run python -m fitlit.auth login`) to automate
> 2b so you don't curl by hand — see section 4.

---

## 3. Set the OAuth env vars on the VM

Create `.env` in the project root (it's gitignored). Start from the template:

```bash
cp .env.example .env
nano .env
```

Fill in:

```ini
# Works today (the access token the client sends as the Bearer):
GOOGLE_HEALTH_ACCESS_TOKEN=<access_token from step 2b>
GOOGLE_HEALTH_USER=me

# Needed for refresh (section 4 will consume these):
GOOGLE_HEALTH_CLIENT_ID=<client id>
GOOGLE_HEALTH_CLIENT_SECRET=<client secret>
GOOGLE_HEALTH_REFRESH_TOKEN=<refresh_token from step 2b>

# Persistence + server (see .env.example for the full list)
FITLIT_DATA_DIR=./data
# PORT=8000
```

Verify the token is seen and a real call is attempted:

```bash
uv run python -m fitlit.fetchers.heart   # should make a real request now
uv run python scripts/seed_dummy_data.py # or seed dummy rows to eyeball the DB
sqlite3 data/db/heart.db "SELECT COUNT(*) FROM heartRate;"
```

⚠️ With **only** `GOOGLE_HEALTH_ACCESS_TOKEN` set, it works until the token
expires (~1h), then every call 401s. That's why section 4 is required for a
service that runs unattended.

---

## 4. TODO — build OAuth refresh into the code

This is the one real code task left. Plan:

1. **`fitlit/auth.py`** — a small token manager that:
   - reads `GOOGLE_HEALTH_CLIENT_ID`, `GOOGLE_HEALTH_CLIENT_SECRET`,
     `GOOGLE_HEALTH_REFRESH_TOKEN`;
   - caches the current access token + its expiry under
     `data/state/token.json`;
   - `get_access_token()` returns a valid token, transparently refreshing via
     `POST https://oauth2.googleapis.com/token` with
     `grant_type=refresh_token` when it's within ~60s of expiry.
   - a `login` CLI subcommand that runs the section-2b consent exchange and
     writes the refresh token, so onboarding is one command.
2. **`fitlit/client.py`** — replace the static `config.ACCESS_TOKEN` read with
   `auth.get_access_token()` per request (cheap; it's cached), and on a `401`
   force one refresh + retry before giving up.
3. **`config.py`** — add the three new env vars (client id/secret/refresh).
4. The token cache (`data/state/token.json`) holds a live credential → keep it
   gitignored (the `data/state/` rule already covers it) and `chmod 600`.

Until this lands, a stopgap is a cron job that refreshes the access token and
rewrites `.env` hourly — but building `auth.py` is the right fix.

---

## 5. Run it 24/7 on the VM (systemd)

Once a valid token is in place, run the all-in-one server (API + background
scheduler) under systemd so it restarts on crash/reboot.

Create `/etc/systemd/system/fitlit.service` (adjust `User` and paths):

```ini
[Unit]
Description=FitLit — Google Health fetcher + API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/home/azureuser/fitlit
# uv reads .env via the app; EnvironmentFile is optional/redundant.
ExecStart=/home/azureuser/.local/bin/uv run uvicorn fitlit.server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fitlit
sudo systemctl status fitlit
journalctl -u fitlit -f          # follow logs
curl http://localhost:8000/status
curl http://localhost:8000/stats
```

Alternative (cron-only, no API): a `@reboot` entry running
`uv run python -m fitlit.orchestrator` — see [`crontab.example`](../crontab.example).

---

## 6. Quick reference — env vars

| Var | Used now? | Purpose |
|---|---|---|
| `GOOGLE_HEALTH_ACCESS_TOKEN` | ✅ | Bearer token the client sends |
| `GOOGLE_HEALTH_USER` | ✅ | `me` or explicit user id |
| `GOOGLE_HEALTH_CLIENT_ID` | ⬜ (section 4) | OAuth client id, for refresh |
| `GOOGLE_HEALTH_CLIENT_SECRET` | ⬜ (section 4) | OAuth client secret, for refresh |
| `GOOGLE_HEALTH_REFRESH_TOKEN` | ⬜ (section 4) | Long-lived token to mint access tokens |
| `FITLIT_DATA_DIR` | ✅ | Where SQLite DBs + state live |
| `FITLIT_SQLITE_JOURNAL` | ✅ | `WAL` (local disk) / `DELETE` (network share) |
| `PORT` / `HOST` | ✅ | Server bind |
| `FITLIT_RUN_SCHEDULER` | ✅ | Run the scheduler in-process (default true) |
| `FITLIT_TICK_SECONDS` | ✅ | Orchestrator tick (default 10) |
