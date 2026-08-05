# FitLit Gmail notification service

FitLit's Gmail service checks the local health databases every 15 minutes and
sends compact numerical reports to one configured Gmail address. Optional
email commands can read only tightly filtered, self-addressed `FitLit Ask:`
messages through a separate Gmail read-only credential. Gmail is never used to
obtain health data.

## Notification policy

| Trigger | Delivery rule |
|---|---|
| Completed sleep | One morning report per immutable sleep record |
| Completed workout | One report per Fitbit exercise record; a high-confidence heart/movement pattern can fill gaps when Fitbit misses a lifting session |
| Interesting signal | One 10,000-step milestone and at most one unexpected low-movement heart-rate signal per day |
| Weekly catalog | Sunday at 8:00 PM Pacific; Monday morning retry if the daily cap or a transient failure blocks Sunday delivery |
| Morning recovery | One in-depth sleep report per immutable sleep record; a noon recovery fallback is used only when sleep has not synced |
| Evening review | One 8:00 PM Pacific day-in-review whenever a daily slot remains |
| Daily maximum | Five attempted sends per Pacific day; nonmandatory messages stop at four to reserve one slot |

The SQLite ledger at `data/state/gmail-notifications.db` and a process lock make
delivery at-most-once across timer and manual runs. Immutable Google Health
point names are used for sleep and formal-exercise deduplication.

## Email-to-FitLit commands

When explicitly enabled, the existing 15-minute timer also checks for
self-addressed command emails. A valid command must satisfy every condition:

- the sender and recipient both exactly match `FITLIT_GMAIL_TO`;
- the subject starts exactly with `FitLit Ask:` (configurable);
- the message is not automated mail; and
- the question comes only from the subject suffix and bounded `text/plain`
  body. HTML, attachments, quoted replies, and oversized content are ignored.

Examples:

```text
FitLit Ask: How did I sleep?
FitLit Ask: How was my workout today?
FitLit Ask: How active was I today?
FitLit Ask: Give me this week's summary
FitLit Ask: Show commands
```

The command daemon is provider-centered rather than template-driven. It builds
a fresh, read-only grounded snapshot from FitLit's daily, sleep, weekly,
workout, weight, and activity summaries and gives that snapshot to the selected
headless harness. The provider writes natural conversational text, selects
scalar evidence paths for factual health claims, and chooses any requested
XLSX, DOCX, HTML, or PNG artifact type. FitLit appends exact path/value
evidence, validates a strict attribute-free semantic HTML fragment, applies
the fixed responsive FitLit theme, and owns artifact titles, labels, filenames,
and bytes.

The first successfully processed `FitLit Ask` conversation becomes the primary
thread. After that, the daemon stops searching the mailbox and polls only that
exact Gmail thread. It reads full content for no more than the latest five
messages in the chain, answers only the newest unseen user message, and treats
the older four as bounded context. Older unseen questions are marked
superseded instead of receiving stale replies. Unrelated threads never enter
the provider request.

The bounded thread fragments are supplied in-memory to the configured provider,
so the provider can understand conversational replies. They are not written to
the SQLite ledger. The ledger stores only immutable message/thread IDs,
delivery state, Gmail chronology, and a topic derived from the cited data root.
The
isolated provider request, local session state, logs, and generated artifacts
live in a mode-`0700` temporary directory and are deleted immediately after
Gmail delivery or failure.

Copilot is the default harness, using `gpt-5.6-sol` at `high` reasoning effort.
Its run has an isolated `COPILOT_HOME`, no remote export, no MCP servers, no
custom instructions, and only the `view` tool inside the temporary request
directory. Codex and Claude adapters can be selected through `.env`. Provider
output is schema-validated; unsafe or malformed HTML, non-scalar or missing
evidence paths, provider-controlled topics or filenames, excessive artifacts,
XML-unsafe values, and spreadsheet formulas are rejected. Provider HTML cannot
contain attributes, links, images, scripts, styles, forms, comments, embedded
data, or remote resources. Exact grounded values and the production CSS shell
are added by the runtime. There is no template classifier: greetings and
normal conversation can use natural text with no evidence paths, while health
answers include the selected evidence trace.
Interrupted sends are first reconciled against Gmail using the immutable source
message ID and are released for retry only after the full provider-and-delivery
window has expired without a matching reply.

Commands are read-only: they cannot execute shell commands, alter FitLit data,
control services, or access arbitrary files. Immutable Gmail message IDs are
stored in `data/state/gmail-inbox.db` without retaining the question body.
Replies have an independent default limit of 20 attempts per Pacific day and
do not consume the five proactive-notification slots.

Configuration:

```ini
GMAIL_INBOX_REFRESH_TOKEN=...
FITLIT_GMAIL_INBOX_ENABLED=true
FITLIT_GMAIL_INBOX_SUBJECT_PREFIX=FitLit Ask:
FITLIT_GMAIL_INBOX_DAILY_MAX=20
FITLIT_GMAIL_INBOX_BATCH_MAX=5
FITLIT_EMAIL_AGENT_PROVIDER=copilot
FITLIT_EMAIL_AGENT_COPILOT_MODEL=gpt-5.6-sol
FITLIT_EMAIL_AGENT_REASONING_EFFORT=high
FITLIT_EMAIL_AGENT_CONTEXT_MESSAGES=5
```

The feature uses a separate `gmail.readonly` refresh token and access-token
cache. That Google scope technically authorizes reading the mailbox; FitLit's
reader enforces the self-address and subject restrictions before extracting
content. The existing `gmail.send` token remains unable to read mail:

```bash
uv run python scripts/oauth_capture.py --gmail-inbox
uv run python -m fitlit.gmail_service status
uv run python -m fitlit.gmail_service run --dry-run
```

For responses within seconds instead of the reconciliation timer's 15-minute
interval, the simplest private setup is the Gmail-only listener:

```ini
FITLIT_GMAIL_INBOX_ENABLED=true
FITLIT_GMAIL_INBOX_POLL_SECONDS=5
FITLIT_EMAIL_AGENT_PROVIDER=copilot
FITLIT_EMAIL_AGENT_COPILOT_MODEL=gpt-5.6-sol
FITLIT_EMAIL_AGENT_REASONING_EFFORT=high
```

```bash
sudo uv run python scripts/install_services.py --install --start
systemctl status fitlit-gmail-poll.service --no-pager
```

It uses only the existing `gmail.readonly` and `gmail.send` OAuth credentials
and checks Gmail every 5 seconds. This is a long-running systemd polling loop,
not a public webhook or Google Pub/Sub integration. It requires no Pub/Sub
topic, service account, public endpoint, or additional cloud authorization.
Typical command detection is within one polling interval; headless generation,
artifact validation, and Gmail delivery add additional processing time. The
15-minute timer stays enabled as a reconciliation path.

## Daily health reports

The morning report selects the longest sleep opportunity ending on the current
Pacific date, preventing overlapping wearable records from inflating the night.
It includes the sleep window, onset latency, awake time, efficiency, stage
architecture, seven-night duration and timing comparisons, HRV, resting heart
rate, blood oxygen, respiratory rate, deterministic interpretation, and the
full calendar date.

At 8:00 PM Pacific, FitLit builds a day-in-review whenever the five-attempt cap
still has room. Unlike the old minimum-count fallback, this is a recurring daily
report rather than a message sent only on quiet days. It includes:

- steps, goal progress, energy expenditure, trusted exercise time/calories,
  active-zone load, sleep, and recovery vitals;
- an eight-block movement rhythm, complete formal-workout ledger, recent
  comparisons, and quality notes for malformed workout records;
- deterministic day-specific facts such as recent step rank, peak movement
  hour, exercise share of total energy, and the morning sleep result; and
- day-of-year, ISO week, and remaining-year context.

Missing optional oxygen or respiratory data is labeled unavailable rather than
imputed. Both reports have plain-text equivalents and remain useful when AI
enrichment is disabled or unavailable.

Build either report locally without sending or reserving it:

```bash
uv run python -m fitlit.gmail_service daily-preview sleep \
  --html data/state/sleep-preview.html
uv run python -m fitlit.gmail_service daily-preview evening \
  --html data/state/evening-preview.html
```

## Weekly performance catalog

Every Sunday at 8:00 PM Pacific, FitLit builds one immutable Monday-Sunday
catalog. The event key is the week-ending date, so the 15-minute timer can retry
without creating duplicates. If Sunday has already reached the five-message
cap, or Gmail temporarily fails, the same catalog remains eligible until noon
Monday after the daily counter resets.

The report is deliberately deeper than the event emails:

- **Training:** trusted workout count, intentional training minutes, exercise
  calories, active-zone minutes, distance, workout types, and a complete formal
  session ledger.
- **Activity:** total and average steps, total energy expenditure, daily
  movement bars, the most active day, and coverage-aware comparison with the
  prior week.
- **Sleep:** average duration, efficiency, cumulative debt against 7.5 hours,
  bedtime consistency, and week-over-week duration change.
- **Recovery:** HRV, resting heart rate, blood oxygen, respiratory rate, and
  prior-week changes when at least three days exist on both sides.
- **Strain proxy:** counts days where HRV is more than 10% below the prior-week
  baseline while resting heart rate is over 3 BPM above it. This is explicitly
  labeled as a recovery proxy, not a direct stress measurement or diagnosis.
- **Decisions:** deterministic standout observations and up to four next-week
  priorities based on movement, sleep, recovery, training load, and data
  coverage.

Wearable records with impossible or internally inconsistent timing/energy stay
visible in the workout ledger with a quality note, but are excluded from totals.
This prevents one malformed session from silently inflating weekly calories or
training time.

Configuration:

```ini
FITLIT_GMAIL_WEEKLY_REPORT_HOUR=20
FITLIT_GMAIL_WEEKLY_RETRY_UNTIL_HOUR=12
```

Build the current catalog locally without reserving or sending it:

```bash
uv run python -m fitlit.gmail_service weekly-preview
uv run python -m fitlit.gmail_service weekly-preview \
  --html data/state/weekly-preview.html
```

## Gmail API research and design

The implementation follows Google's official Gmail API workflow:

1. Build an RFC 2822 MIME message.
2. Encode it as base64url in the message resource's `raw` field.
3. Call `POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send`.

Proactive delivery requests only
`https://www.googleapis.com/auth/gmail.send`. The optional command inbox uses a
second token containing only
`https://www.googleapis.com/auth/gmail.readonly`. Google classifies readonly
mail access as a restricted scope. For a private single-user deployment, keep
the OAuth application and token under the operator's control; a public
multi-user product requires Google's applicable verification and data-handling
requirements. Both Gmail credentials remain separate from FitLit's Health
credentials, and health metrics are read from local SQLite databases.

Official references:

- <https://developers.google.com/workspace/gmail/api/guides/sending>
- <https://developers.google.com/workspace/gmail/api/guides/list-messages>
- <https://developers.google.com/workspace/gmail/api/guides/threads>
- <https://developers.google.com/workspace/gmail/api/auth/scopes>
- <https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send>
- <https://developers.google.com/identity/protocols/oauth2/web-server#offline>

## One-time setup

1. In the same Google Cloud project used by FitLit, enable **Gmail API**.
2. Keep the OAuth consent screen in **Production** so unattended refresh tokens
   are not subject to the seven-day Testing expiry described in
   [`DEPLOYMENT.md`](DEPLOYMENT.md).
3. Reuse the existing OAuth client ID and secret, but mint a separate send-only
   refresh token:

   ```bash
   # On the laptop, keep the existing callback tunnel open.
   ssh -N -L 8765:localhost:8765 fitlit

   # On the VM:
   uv run python scripts/oauth_capture.py --gmail
   ```

4. The capture script writes `GMAIL_REFRESH_TOKEN` to the ignored `.env`.
   Configure the recipient there as `FITLIT_GMAIL_TO`.
5. To enable self-addressed email commands, mint the isolated read-only token:

   ```bash
   uv run python scripts/oauth_capture.py --gmail-inbox
   ```

   This writes `GMAIL_INBOX_REFRESH_TOKEN`; then set
   `FITLIT_GMAIL_INBOX_ENABLED=true`. Install and authenticate the selected
   command provider, then configure it:

   ```ini
   FITLIT_EMAIL_AGENT_PROVIDER=copilot
   FITLIT_EMAIL_AGENT_COPILOT_MODEL=gpt-5.6-sol
   FITLIT_EMAIL_AGENT_REASONING_EFFORT=high
   FITLIT_EMAIL_AGENT_CONTEXT_MESSAGES=5
   ```

6. Install and enable the services:

   ```bash
   sudo uv run python scripts/install_services.py --install --start
   ```

## Optional proactive-report AI observations

The event detector, immutable IDs, overlap checks, daily cap, reserved slot,
and delivery decision remain deterministic. This separate enrichment path is
called only after a proactive message has successfully reserved a ledger slot,
and only for sleep, daily, workout, weekly, or high-signal heart reports. A
normal day therefore makes roughly 2–5 model calls, not 96 timer-interval calls.

The subprocess receives a shallow allowlisted object of numerical metrics and a
controlled report type. It does not receive the Gmail address, OAuth tokens,
database files, `.env`, names, local coaching documents, or raw wearable JSON.
It runs from a fresh empty temporary directory with tools/instructions disabled,
a hard timeout, a minimal environment, and no persistent session. Output must
match a strict object containing one short headline, at most three
observations, and confidence from 0–1. Invalid, failed, unavailable, or timed-out
providers are discarded and the original deterministic report is still sent.

Supported CLI providers:

| Provider | Noninteractive contract |
|---|---|
| GitHub Copilot CLI | `copilot --prompt ... --silent`, custom instructions/MCP/remote/tools disabled |
| OpenAI Codex CLI | `codex exec --ephemeral --sandbox read-only --output-schema ...` |
| Claude Code | `claude --bare --print --json-schema ... --tools "" --no-session-persistence` |

Configure in the ignored `.env`:

```ini
FITLIT_AI_ENABLED=true
FITLIT_AI_PROVIDER=auto
FITLIT_AI_PROVIDER_ORDER=copilot,codex,claude
FITLIT_AI_TIMEOUT_SECONDS=45
```

`auto` tries installed providers in order. Authenticate the chosen CLI as its
own documentation requires. Claude `--bare` requires API-key/provider
credentials rather than the normal OAuth/keychain session. Provider credentials
must stay outside source control.

References:

- <https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli>
- <https://learn.chatgpt.com/docs/non-interactive-mode>
- <https://learn.chatgpt.com/docs/auth>
- <https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless>

## Operation

```bash
# Preview due messages without sending or changing the ledger
uv run python -m fitlit.gmail_service run --dry-run

# Run one real notification check
uv run python -m fitlit.gmail_service run

# Configuration, today's counts, and recent delivery outcomes
uv run python -m fitlit.gmail_service status

# Timer state and delivery logs
systemctl status fitlit-gmail.timer
journalctl -u fitlit-gmail.service --since today
```

The timer can safely be enabled before Gmail OAuth is complete. An unconfigured
run exits successfully with `status: not-configured` and does not reserve or
send any notification.
