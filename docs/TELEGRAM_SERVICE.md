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
  the next one; `/reset` is disabled. SQLite FTS5 indexes active and archived
  turns for the read-only `search_transcript_memory` MCP tool.
- The selected provider receives the active conversation plus one compact
  `citable_evidence` map of exact `path: value` pairs selected from FitLit's
  grounded local summaries for the newest question. The request explicitly
  labels `**LATEST QUERY**` as authoritative and never repeats it inside the
  context messages. The complete transcript always stays in the local database;
  only the provider view is compacted. If the composed request exceeds the
  configured byte budget, FitLit balances evidence detail with at least the six
  most recent context turns, then omits older turns only as needed. The omitted
  count and evidence tier are reported to the provider. Only when even the
  newest question cannot fit does FitLit ask the user to start `/new` while
  preserving the archived conversation.
- The provider writes natural conversational text for greetings, follow-ups,
  clarifications, and health questions. Health analyses lead with the bottom
  line and normally add two to four concrete insights instead of stopping at a
  metric list. Exercise evidence includes normalized Fitbit pace, speed,
  cadence, calorie rate, heart-rate-zone time, GPS status, and splits when the
  source record contains them. Health claims cite keys copied verbatim from
  `citable_evidence`; FitLit appends up to ten exact values in a compact
  `Ground truth (Fitbit)` list with human labels and units, while internal path
  names stay hidden from the chat. Provider hard-wraps inside sentences are
  collapsed while real paragraphs and list items remain intact. Telegram
  replies are plain text, so the semantic HTML fragment is optional: when it is
  missing, malformed, or unsafe the runtime renders escaped semantic paragraphs
  from the validated text instead of discarding the answer. A valid fragment is
  still
  used for a requested HTML artifact, is checked against a strict structural-tag
  allowlist, and is wrapped in the fixed responsive production theme. All
  artifact titles, labels, filenames, and bytes are rendered locally. Links,
  images, scripts, styles, forms, comments, processing instructions,
  attributes, embedded data, and remote resources are rejected.
- `HARNESS` selects Claude, Codex, Copilot, or OpenCode for Telegram and every
  other model-backed workflow, and defaults to `claude`. Telegram keeps its own
  model key, so it can override the Gmail model; both default to Sonnet 5 at
  high reasoning effort.
  Harness request files, sessions, logs, MCP configuration, and generated
  artifacts remain temporary.
- The harness prompt advertises read-only transcript memory and native
  subagents. Memory is used only when the owner refers to an earlier chat or
  missing historical context; stored text is always untrusted data. For a
  genuinely complex request, at most two first-level subagents may perform
  narrow independent analysis before the parent synthesizes one reply.
- Replies are plain text, chunked against Telegram's 4096 limit measured in
  **UTF-16 code units**, so astral emoji cost two units each and a chunk can
  never overflow. Paragraph, line, and word boundaries are preserved.
- Requested evidence-only XLSX, DOCX, safe HTML, and locally rendered PNG
  screenshot artifacts are uploaded while their private temporary directory
  exists, then deleted. **Every artifact, including PNG, is sent with
  `sendDocument`**: document uploads keep the exact rendered bytes and the
  larger 50 MB semantics, while photo uploads impose dimension and
  recompression constraints. FitLit's own artifact byte cap
  (`FITLIT_EMAIL_AGENT_MAX_ATTACHMENT_BYTES`) is unchanged and still applies.
  Each artifact materializes independently; a local rendering or size failure
  drops only that file and adds a concise notice without suppressing the valid
  text answer.
- Outbound chunks and files are staged in the owner-only transcript database
  until Telegram confirms each part. Confirmed parts are skipped on retry.
  Error code `None`, `429`, and `5xx` are treated as retryable and the part
  returns to `pending`; other API rejections are terminal. A terminal artifact
  rejection lets the rest of the plan continue, while a terminal text rejection
  fails the remaining parts. If a transport failure makes one part
  delivery-uncertain, FitLit records that outcome and does not blindly resend
  it, avoiding duplicate health messages. SQLite secure deletion clears the
  temporary staged payload after completion.
- The official Bot API has no outbound idempotency key and no way to reconcile
  what a chat already received, so an uncertain send stays unsent and is
  instead **visibly disclosed**: after the durable plan completes, FitLit
  best-effort sends one short delivery notice outside the plan. A failed notice
  can never wedge the update or cause the notice to repeat. When no part at all
  reached Telegram, the stored assistant turn is prefixed `[NOT DELIVERED]` so
  later model context does not assume the owner read it.
- Only one FitLit Telegram process may run. `run` and `pair` both take a
  non-blocking kernel `flock` on owner-only `data/state/telegram-service.lock`
  (mode 0600), which the kernel releases even if the process is killed.
  Pairing therefore refuses while the listener is live and also refuses when a
  trusted owner is already configured. Initial pairing clears unauthenticated
  queued updates before waiting for the one-time code; token rotation does not
  require re-pairing.
- A deterministic update that keeps failing locally is quarantined after three
  attempts, recorded in an `update_failures` table keyed by `update_id` with a
  sanitized error class only. FitLit sends one short owner notice, discards any
  staged reply, marks the update `quarantined`, and moves on, so a single
  poison update can never block every later message. Transport failures, HTTP
  `429`/`5xx`/unknown API codes, `sqlite3.OperationalError`, and state-file
  `OSError` are transient and never consume that budget.
- Telegram documents that a bot idle for at least a week receives a randomly
  chosen next `update_id`. FitLit accepts a lower identifier only after seven
  idle days and keeps ignoring recent stale lower identifiers, so a normal
  replay can never rewind the offset.
- Provider-failure fallback notices and runtime delivery/evidence annotations
  are withheld from later provider context. Nothing is deleted or rewritten:
  SQLite still stores every byte, and only the model-visible view is filtered.
  For a partially delivered reply, the model sees only confirmed text chunks
  plus a compact partial-delivery marker.
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
HARNESS=claude
FITLIT_TELEGRAM_BOT_TOKEN=your-private-botfather-token
FITLIT_TELEGRAM_CLAUDE_MODEL=claude-sonnet-5
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
headless harness is working, then sends the durable reply plan.

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

Logs contain fixed operational events plus the numeric `update_id`, outcome,
and elapsed seconds — never message bodies, bot tokens, user IDs, usernames, or
Telegram API URLs. `status` reports `last_update_id`, `last_outcome`,
`last_update_at` and `seconds_since_last_update` in Pacific time,
`listener_running` (a lock probe), `pending_outbound_parts`,
`quarantined_updates`, and `provider_installed` / `model_valid` /
`effort_valid`.

The listener validates the headless harness, the Telegram model format, and
the reasoning effort **before** polling starts, and exits `78` when any of them
is invalid, so `RestartPreventExitStatus=78` stops a pointless restart loop. A
`401` from `getUpdates` is the only status treated as a rejected token and also
exits `78`; `403` is never assumed to mean a bad token, and `409` keeps its
conflict warning and exponential backoff. Run
`uv run python scripts/preflight.py` after changing
`HARNESS`, its `FITLIT_TELEGRAM_<HARNESS>_MODEL`, or
`FITLIT_TELEGRAM_REASONING_EFFORT`; it reports harness installation,
transcript-memory indexing, `model_valid`, and `effort_valid`. See
[`HEADLESS_HARNESSES.md`](HEADLESS_HARNESSES.md).

To revoke access, use **@BotFather** to revoke the token, stop the service, and
remove the three `FITLIT_TELEGRAM_*` identity/enabled values from `.env`.

When upgrading from the removed experimental WhatsApp bridge, also remove the
old FitLit entry from **WhatsApp → Settings → Linked Devices**. The service
installer deletes any remaining local `data/state/whatsapp-auth/` credentials,
but only the phone can revoke the remote linked-device entry.
