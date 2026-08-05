# Private Telegram bot

FitLit can answer grounded health questions through a private Telegram bot
using Telegram's official Bot API. The daemon performs outbound HTTPS long
polling, so it needs no public webhook, listening port, domain, or TLS
certificate.

## Privacy boundary

- Pairing requires a random one-time command shown only in the terminal. After
  pairing, FitLit accepts messages only from that exact numeric Telegram user
  ID in a one-to-one private chat. Other updates are silently discarded.
- Every trusted user/assistant turn is appended to owner-only
  `data/state/telegram-conversations.sqlite3`. Conversations are indexed and
  never deleted by bot commands. `/new` archives the active thread and creates
  the next one; `/reset` is disabled.
- The selected provider receives the complete active conversation plus
  FitLit's grounded local summaries. The request explicitly labels
  `**LATEST QUERY**` as authoritative. No earlier turn is silently truncated;
  if the complete request exceeds the configured provider input bound, FitLit
  asks the user to start `/new` while preserving the archived conversation.
- The provider writes natural conversational text for greetings, follow-ups,
  clarifications, and health questions. Health claims include selected scalar
  evidence paths; FitLit appends their exact local values. It may also draft a
  balanced, attribute-free semantic HTML fragment from a strict structural-tag
  allowlist. FitLit validates that fragment, applies the fixed responsive
  production theme, and renders all artifact titles, labels, filenames, and
  bytes locally. Links, images, scripts, styles, forms, comments, attributes,
  embedded data, and remote resources are rejected.
- Telegram overrides the shared Gmail model and uses GPT-5.6 Terra at high
  reasoning effort by default. Provider request files, sessions, logs, and
  generated artifacts remain temporary.
- Replies are plain text. Requested evidence-only XLSX, DOCX, safe HTML, and
  locally rendered PNG screenshot artifacts are uploaded while their private
  temporary directory exists, then deleted.
- Outbound chunks and files are staged in the owner-only transcript database
  until Telegram confirms each part. Confirmed parts are skipped on retry. If
  a transport failure makes one part delivery-uncertain, FitLit records that
  outcome and does not blindly resend it, avoiding duplicate health messages.
  SQLite secure deletion clears the temporary staged payload after completion.
- Telegram bot chats are transported through and stored by Telegram's cloud;
  they are **not Secret Chats and are not end-to-end encrypted**. Do not use
  this channel if that boundary is unacceptable for your health questions.
- The bot token grants control of the bot. Keep it only in owner-readable
  `.env`, never paste it into issue reports, logs, commits, or command
  arguments.

## Create and pair

1. Open Telegram's verified **@BotFather** account.
2. Send `/newbot`, choose a name and username, and copy the generated token.
3. Put the token in private `.env` and confirm its permissions:

```ini
FITLIT_TELEGRAM_BOT_TOKEN=your-private-botfather-token
FITLIT_TELEGRAM_COPILOT_MODEL=gpt-5.6-terra
FITLIT_TELEGRAM_REASONING_EFFORT=high
```

```bash
chmod 600 .env
uv run python -m fitlit.telegram_service pair
```

The pairing command displays a random `/pair ...` message and the bot username.
Open that bot, press **Start**, and send the exact command within five minutes.
FitLit then writes `FITLIT_TELEGRAM_TRUSTED_USER_ID` and
`FITLIT_TELEGRAM_ENABLED=true` to private `.env`. The numeric ID and token are
not printed.

## Install and use

```bash
uv run python scripts/preflight.py
sudo uv run python scripts/install_services.py --install --start
systemctl is-active fitlit-telegram.service
```

Send an ordinary text message to the bot—no prefix is needed. `/reset` clears
no data and instead directs the user to `/new`. `/new` archives the current
indexed conversation and starts a fresh thread with the current system prompt.
`/help` reports that the channel is ready. Incoming media and documents are
not downloaded. The bot refreshes Telegram's `typing` action while the
headless provider is working, then sends the durable reply plan.

HTML is designed for phones first: one vertical column, compact sections,
short headings, and tables capped at three columns. The runtime uses the exact
same responsive dark navy, deep teal, mint, and cyan FitLit presentation shell
as the email service, including narrow-screen spacing and horizontally safe
evidence tables.

The configured five-second `getUpdates` timeout is an idle long-poll hold, not
a five-second scan interval: Telegram returns the request immediately when a
message arrives. Reducing it to one second increases idle HTTPS traffic without
improving message arrival latency. Telegram's Bot API does not offer a
WebSocket transport for bots. A webhook is the future scaling option for
multiple workers, but requires a public HTTPS ingress and coordinated update
and delivery ownership; the outbound long-polling daemon is the simpler,
smaller private deployment for one paired user.

## Operations and revocation

```bash
uv run python -m fitlit.telegram_service status
journalctl -u fitlit-telegram.service -f
systemctl restart fitlit-telegram.service
systemctl disable --now fitlit-telegram.service
```

Logs contain fixed operational events, never message bodies, bot tokens, user
IDs, usernames, or Telegram API URLs. To revoke access, use **@BotFather** to
revoke the token, stop the service, and remove the three
`FITLIT_TELEGRAM_*` identity/enabled values from `.env`.

When upgrading from the removed experimental WhatsApp bridge, also remove the
old FitLit entry from **WhatsApp → Settings → Linked Devices**. The service
installer deletes any remaining local `data/state/whatsapp-auth/` credentials,
but only the phone can revoke the remote linked-device entry.
