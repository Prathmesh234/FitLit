# FitLit Gmail notification service

FitLit's Gmail service checks the local health databases every 15 minutes and
sends compact numerical reports to one configured Gmail address. It never reads
mail and never uses Gmail to obtain health data.

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

Only `https://www.googleapis.com/auth/gmail.send` is requested. Google classifies
this as a sensitive scope that permits sending mail on the user's behalf. The
Gmail refresh token and access-token cache are separate from FitLit's Health
credentials; health metrics are read from the existing local SQLite databases.

Official references:

- <https://developers.google.com/workspace/gmail/api/guides/sending>
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
5. Install and enable the timer:

   ```bash
   sudo uv run python scripts/install_services.py --install --start
   ```

## Optional AI observations

The event detector, immutable IDs, overlap checks, daily cap, reserved slot,
and delivery decision remain deterministic. AI is called only after a message has successfully reserved a ledger slot, and
only for sleep, daily, workout, weekly, or high-signal heart reports. A normal day
therefore makes roughly 2–5 model calls, not 96 timer-interval calls.

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
