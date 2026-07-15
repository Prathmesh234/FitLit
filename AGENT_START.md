# FitLit agent start guide

This is the public, provider-neutral runbook for GitHub Copilot CLI, OpenAI
Codex CLI, Claude Code, or a human operator bootstrapping a fresh Linux clone.
It intentionally contains no recipient, token, account identity, host name,
health value, diet, location, or private coaching context.

## Canonical agent instruction

> Read `AGENT_START.md`. Run the preflight, install only the repository's
> declared dependencies, create `.env` from `.env.example` without printing
> secrets, guide the operator through Google Health and optional Gmail OAuth,
> render/install the systemd units, verify every daemon and local endpoint, and
> run the privacy scan. Never expose port 8000 publicly, inspect or commit local
> health databases, print credentials, or add ignored coaching context.

## 1. Safety rules

1. Keep `.env`, OAuth caches, `data/db/`, `data/state/`, `AGENTS.md`, and
   `data/Body-Comp-HandOff/` private and untracked.
2. Do not print token values. Report only whether required variable names exist.
3. Keep the API on `127.0.0.1:8000`; it has no application authentication.
4. Do not send raw database rows or free-form personal notes to an AI provider.
5. Do not enable Gmail delivery until the recipient and daily policy are
   understood. Preview first.

## 2. Prepare the clone

Prerequisites: Linux with systemd, Git, curl, Python 3.11+, and `uv`.

```bash
sudo apt-get update
sudo apt-get install -y git curl sqlite3
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv sync
cp .env.example .env
chmod 600 .env
uv run python scripts/preflight.py
```

The preflight emits booleans and executable paths, never secret values. A
nonzero exit means Python, `uv`, `.env`, or its permissions need attention.

## 3. Configure Google Health OAuth

Follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) to create a Google OAuth
client, enable Google Health API, configure the documented read-only scopes,
publish the consent screen appropriately, and capture a refresh token.

For a remote machine, forward the callback:

```bash
ssh -N -L 8765:localhost:8765 <user>@<vm-host>
uv run python scripts/oauth_capture.py
```

The capture script verifies OAuth `state`, exchanges the code locally, stores
the refresh token in mode-`0600` `.env`, and does not commit it.

## 4. Configure optional Gmail delivery

Enable Gmail API in the same Google Cloud project, then mint a separate
least-privilege `gmail.send` refresh token:

```bash
uv run python scripts/oauth_capture.py --gmail
```

Set `FITLIT_GMAIL_TO` only in `.env`. Preview without sending:

```bash
uv run python -m fitlit.gmail_service run --dry-run
uv run python -m fitlit.gmail_service status
```

The deterministic policy sends at least two successful reports when delivery
and data are available, attempts at most five per Pacific day, suppresses
duplicates, and reserves the final slot for mandatory reporting.

## 5. Configure optional headless AI

AI enrichment is off by default. Install and authenticate at least one supported
CLI using its official instructions:

- GitHub Copilot CLI: <https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli>
- OpenAI Codex CLI: <https://learn.chatgpt.com/docs/codex-cli>
- Claude Code: <https://docs.anthropic.com/en/docs/claude-code/setup>

Authentication choices:

- Copilot: run `copilot login` if the CLI is not already authenticated.
- Codex: run `codex login`; on headless machines prefer
  `codex login --device-auth`. API-key automation may use `CODEX_API_KEY`.
- Claude: FitLit invokes `--bare`, which requires `ANTHROPIC_API_KEY` or
  configured Bedrock/Vertex/Foundry credentials.

Enable provider fallback in `.env`:

```ini
FITLIT_AI_ENABLED=true
FITLIT_AI_PROVIDER=auto
FITLIT_AI_PROVIDER_ORDER=copilot,codex,claude
```

The runtime invokes AI only after deterministic reservation. It uses an empty
temporary working directory, strips application secrets, disables provider
tools/instructions/session persistence where supported, enforces a timeout and
schema, and sends the original report unchanged on any failure.

## 6. Install all daemons

The installer discovers the current clone, service user, `uv`, and provider
binary directories before rendering units:

```bash
uv run python scripts/install_services.py
sudo uv run python scripts/install_services.py --install --start
```

Installed runtime:

| Unit | Role |
|---|---|
| `fitlit.service` | Local dashboard/API plus 10-second scheduler |
| `fitlit-gc.service` | Lossless archive and bounded SQLite retention |
| `fitlit-gmail.timer` | Launches the Gmail one-shot every 15 minutes |
| `fitlit-gmail.service` | Detects, reserves, optionally enriches, and sends |

## 7. Verify operation

```bash
systemctl is-active fitlit.service fitlit-gc.service fitlit-gmail.timer
systemctl is-enabled fitlit.service fitlit-gc.service fitlit-gmail.timer
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/status
uv run python -m fitlit.gmail_service status
uv run python -m unittest discover -s tests -v
```

Reach the dashboard from another machine only through a tunnel:

```bash
ssh -N -L 8000:localhost:8000 <user>@<vm-host>
```

Then open <http://localhost:8000>.

## 8. Public-release gate

```bash
uv run python scripts/privacy_scan.py
uv run python scripts/privacy_scan.py --history
git status --short
```

The tracked-tree scan must be clean. The history scan must also be clean before
making the repository public; deleting a secret only from the latest commit is
not sufficient. Review ignored/untracked files separately without adding them.
