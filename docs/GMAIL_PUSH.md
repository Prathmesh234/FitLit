# Near-real-time Gmail commands

FitLit can receive Gmail mailbox-change notifications through Google Cloud
Pub/Sub and keep an outbound StreamingPull connection open from the VM. No
public VM port or webhook is required.

```text
self-addressed FitLit Ask email
  -> Gmail mailbox watch
  -> Google Pub/Sub topic
  -> pull subscription
  -> fitlit-gmail-push.service on the VM
  -> constrained Gmail reader
  -> threaded reply
```

Gmail normally publishes the event within seconds. The existing 15-minute timer
remains enabled as a reconciliation path because Gmail documents that push
notifications can occasionally be delayed or dropped. Immutable Gmail message
IDs make duplicate push and timer processing safe.

## 1. Create Pub/Sub resources

Use the same Google Cloud project as the OAuth client configured by
`GOOGLE_HEALTH_CLIENT_ID`. Gmail rejects a `watch` request when the topic belongs
to a different project.

From Google Cloud Shell:

```bash
PROJECT_ID="your-google-cloud-project"
TOPIC="fitlit-gmail"
SUBSCRIPTION="fitlit-gmail-vm"

gcloud services enable gmail.googleapis.com pubsub.googleapis.com \
  --project "$PROJECT_ID"
gcloud pubsub topics create "$TOPIC" --project "$PROJECT_ID"
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
  --topic "$TOPIC" \
  --ack-deadline 60 \
  --project "$PROJECT_ID"
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project "$PROJECT_ID"
```

The VM identity needs `pubsub.subscriptions.consume` on the subscription. Grant
`roles/pubsub.subscriber` only to the identity used by this VM.

## 2. Authenticate the Azure VM to Google Cloud

**Recommended:** configure
[Workload Identity Federation for an Azure VM](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds).
It exchanges the Azure managed identity for short-lived Google credentials and
does not store a Google service-account key. Point
`GOOGLE_APPLICATION_CREDENTIALS` at the generated external-account
configuration file.

For a private test deployment, a dedicated service-account JSON key also works,
but it is a long-lived secret. Restrict the service account to Pub/Sub
Subscriber, store the key outside the repository with mode `0600`, and rotate
it:

```bash
chmod 600 /path/to/fitlit-pubsub-credentials.json
```

The Pub/Sub credential is separate from both Gmail OAuth tokens:

- `GMAIL_INBOX_REFRESH_TOKEN` reads Gmail through `gmail.readonly`.
- `GMAIL_REFRESH_TOKEN` sends the threaded response through `gmail.send`.
- Application Default Credentials consume only the Pub/Sub subscription.

## 3. Enable the command inbox and push listener

Complete the separate Gmail read-only consent first:

```bash
uv run python scripts/oauth_capture.py --gmail-inbox
```

Add the following to the ignored `.env`:

```ini
FITLIT_GMAIL_INBOX_ENABLED=true
FITLIT_GMAIL_PUSH_ENABLED=true
FITLIT_GMAIL_PUBSUB_TOPIC=projects/your-google-cloud-project/topics/fitlit-gmail
FITLIT_GMAIL_PUBSUB_SUBSCRIPTION=projects/your-google-cloud-project/subscriptions/fitlit-gmail-vm
GOOGLE_APPLICATION_CREDENTIALS=/path/to/adc-or-wif-credential.json
```

Create the watch and install the service:

```bash
uv sync
uv run python -m fitlit.gmail_push watch
sudo uv run python scripts/install_services.py --install --start
systemctl status fitlit-gmail-push.service --no-pager
```

FitLit watches the `SENT` label because valid commands must be sent by the
authenticated account to itself. Each notification is also checked against
`FITLIT_GMAIL_TO` before processing.

## Operations

```bash
uv run python -m fitlit.gmail_push status
journalctl -u fitlit-gmail-push.service -n 100 --no-pager
sudo systemctl restart fitlit-gmail-push.service
```

Gmail watches expire after at most seven days. The listener checks hourly and
renews the watch when it is within 24 hours of expiration. Starting or manually
renewing a watch causes one immediate Pub/Sub notification, which is harmless
because command processing is idempotent.

Pub/Sub messages are acknowledged after successful reconciliation. Transient
Gmail authorization, API, or ledger failures are negatively acknowledged so
Pub/Sub retries them. Poison messages and notifications for another account are
acknowledged and discarded without exposing their contents.

Official references:

- <https://developers.google.com/workspace/gmail/api/guides/push>
- <https://docs.cloud.google.com/pubsub/docs/pull>
- <https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds>
