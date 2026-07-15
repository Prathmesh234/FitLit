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
| Daily minimum | A noon recovery fallback is used only when sleep has not synced; an evening numerical summary is sent only when fewer than two messages were delivered |
| Daily maximum | Five attempted sends per Pacific day; nonmandatory messages stop at four to reserve one slot |

The SQLite ledger at `data/state/gmail-notifications.db` and a process lock make
delivery at-most-once across timer and manual runs. Immutable Google Health
point names are used for sleep and formal-exercise deduplication.

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
   sudo cp deploy/fitlit-gmail.service deploy/fitlit-gmail.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now fitlit-gmail.timer
   ```

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
