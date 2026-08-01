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
4. Do not send raw database rows, unrelated mailbox mail, or private coaching
   files to an AI provider. The command inbox may send only the latest five
   bounded messages from its primary FitLit thread plus grounded summaries.
5. Do not enable Gmail delivery until the recipient and daily policy are
   understood. Preview first.
6. Treat `data/state/whatsapp-auth/` as login credentials. Never print, copy,
   inspect, or commit its files or the private trusted phone number.

## 2. Prepare the clone

Prerequisites: Linux with systemd, Git, curl, Python 3.11+, and `uv`. The
optional WhatsApp bridge additionally requires Node 20+ and npm.

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
nonzero exit means a required base prerequisite—or an explicitly enabled
WhatsApp bridge prerequisite—needs attention.

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
uv run python -m fitlit.gmail_service daily-preview sleep \
  --html data/state/sleep-preview.html
uv run python -m fitlit.gmail_service daily-preview evening \
  --html data/state/evening-preview.html
```

The deterministic policy sends at least two successful reports when delivery
and data are available, attempts at most five per Pacific day, suppresses
duplicates, and reserves the final slot for mandatory reporting. The morning
sleep brief is keyed to the immutable sleep record; the 8 PM Pacific
day-in-review is keyed to the Pacific calendar date.

For the optional self-addressed command inbox, mint a separate read-only token:

```bash
uv run python scripts/oauth_capture.py --gmail-inbox
```

Set `FITLIT_GMAIL_INBOX_ENABLED=true` only after confirming that
`FITLIT_GMAIL_TO` is the operator's own address. The reader accepts only exact
`FitLit Ask:` subjects sent from and to that address, ignores HTML and
attachments, stores no question body, and exposes read-only health summaries.
The first successful command chain becomes the only thread polled. The selected
headless provider receives at most the latest five messages from that chain,
with the newest user message authoritative.
For near-real-time delivery without more cloud permissions, set
`FITLIT_GMAIL_INBOX_POLL_SECONDS=5` and enable `fitlit-gmail-poll.service`
through the installer. This Gmail-only systemd polling loop is the primary
command listener; do not provision Pub/Sub, a service account, or a public
webhook. Keep the existing 15-minute Gmail timer enabled as reconciliation.

Configure the command-reply harness in `.env`:

```ini
FITLIT_EMAIL_AGENT_PROVIDER=copilot
FITLIT_EMAIL_AGENT_COPILOT_MODEL=gpt-5.6-sol
FITLIT_EMAIL_AGENT_REASONING_EFFORT=high
FITLIT_EMAIL_AGENT_CONTEXT_MESSAGES=5
```

Copilot runs with an isolated temporary `COPILOT_HOME`, no remote export, no
MCP servers, and only read access to its temporary request. It drafts
qualitative text and safe HTML, selects scalar evidence paths, and chooses any
requested XLSX/DOCX type. The runtime appends exact path/value traces, owns
topics, filenames, columns, and data cells, and deletes request files, provider
state, logs, and artifacts after delivery.

## 5. Configure optional proactive-report AI

The conversational inbox requires its selected provider. Separate enrichment
for proactive morning/evening/weekly reports remains optional and is off by
default. Install and authenticate supported CLIs using their official
instructions:

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

## 6. Configure optional private WhatsApp

The unofficial Baileys bridge is a replaceable private prototype; read its
account-risk and privacy boundaries in
[`docs/WHATSAPP_SERVICE.md`](docs/WHATSAPP_SERVICE.md). Install pinned
dependencies, set the trusted E.164 number only in `.env`, and pair:

```bash
npm --prefix whatsapp-bridge ci
npm --prefix whatsapp-bridge run pair
```

Scan from **WhatsApp → Settings → Linked Devices → Link a device**. Pairing
credentials are written under ignored owner-only state. Enable the service only
after successful pairing:

```ini
FITLIT_WHATSAPP_ENABLED=true
FITLIT_WHATSAPP_CONTEXT_MESSAGES=5
```

The bridge accepts only the paired account's live self-chat, stores no bodies,
keeps five in-memory turns, and routes responses through the same grounded
provider harness.

## 7. Install all daemons

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
| `fitlit-gmail-poll.service` | Checks self-addressed Gmail commands every 5 seconds |
| `fitlit-gmail.timer` | Launches the Gmail one-shot every 15 minutes |
| `fitlit-gmail.service` | Detects, reserves, optionally enriches, and sends |
| `fitlit-whatsapp.service` | Handles private WhatsApp self-chat questions |

## 8. Verify operation

```bash
systemctl is-active fitlit.service fitlit-gc.service fitlit-gmail.timer
systemctl is-enabled fitlit.service fitlit-gc.service fitlit-gmail.timer
# If FITLIT_WHATSAPP_ENABLED=true:
systemctl is-active fitlit-whatsapp.service
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

## 9. Public-release gate

```bash
uv run python scripts/privacy_scan.py
uv run python scripts/privacy_scan.py --history
git status --short
```

The public working-tree scan covers tracked and nonignored untracked files and
must be clean. The history scan must also be clean before making the repository
public; deleting a secret only from the latest commit is not sufficient. Review
ignored files separately without adding them.
