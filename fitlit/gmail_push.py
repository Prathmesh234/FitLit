"""Near-real-time Gmail command trigger through a Pub/Sub pull subscription."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import pubsub_v1

from fitlit import config, gmail_auth, gmail_inbox

log = logging.getLogger("fitlit.gmail_push")


class GmailPushError(RuntimeError):
    """Raised when the Gmail watch or Pub/Sub listener cannot operate."""


def _read_watch_state() -> dict:
    try:
        return json.loads(config.GMAIL_WATCH_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_watch_state(response: dict) -> None:
    history_id = str(response.get("historyId", ""))
    expiration = str(response.get("expiration", ""))
    if not history_id.isdigit() or not expiration.isdigit():
        raise GmailPushError("Gmail watch response omitted historyId or expiration")
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = config.GMAIL_WATCH_STATE.with_suffix(".json.tmp")
    payload = {
        "history_id": history_id,
        "expiration_ms": int(expiration),
        "renewed_at": datetime.now(timezone.utc).isoformat(),
        "topic": config.GMAIL_PUBSUB_TOPIC,
    }
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, config.GMAIL_WATCH_STATE)


def _watch_due(now: float | None = None) -> bool:
    state = _read_watch_state()
    if state.get("topic") != config.GMAIL_PUBSUB_TOPIC:
        return True
    current = now or time.time()
    try:
        expiration = int(state.get("expiration_ms", 0)) / 1000
        renewed_at = datetime.fromisoformat(
            str(state.get("renewed_at", "")).replace("Z", "+00:00")
        ).timestamp()
    except (TypeError, ValueError):
        return True
    interval = config.GMAIL_WATCH_RENEW_HOURS * 3600
    return current >= renewed_at + interval or expiration <= current + interval


def ensure_watch(*, force: bool = False) -> dict:
    """Create or renew the Gmail SENT-label watch when it is nearing expiry."""
    if not force and not _watch_due():
        return _read_watch_state()
    if not config.GMAIL_PUBSUB_TOPIC:
        raise GmailPushError("FITLIT_GMAIL_PUBSUB_TOPIC is not configured")
    try:
        response = gmail_inbox._api_json(
            "watch",
            method="POST",
            body={
                "topicName": config.GMAIL_PUBSUB_TOPIC,
                "labelIds": ["SENT"],
                "labelFilterBehavior": "INCLUDE",
            },
        )
    except (gmail_inbox.GmailInboxError, gmail_auth.GmailAuthError) as exc:
        raise GmailPushError(f"could not renew Gmail watch: {exc}") from exc
    _write_watch_state(response)
    return _read_watch_state()


def _decode_notification(data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GmailPushError("Pub/Sub notification contained malformed JSON") from exc
    if not isinstance(value, dict):
        raise GmailPushError("Pub/Sub notification was not an object")
    email_address = str(value.get("emailAddress", "")).strip().lower()
    history_id = str(value.get("historyId", ""))
    if not email_address or not history_id.isdigit():
        raise GmailPushError("Pub/Sub notification omitted emailAddress or historyId")
    return {"email_address": email_address, "history_id": history_id}


def handle_message(message) -> None:
    """Validate one Gmail notification, reconcile commands, then ack or retry."""
    try:
        notification = _decode_notification(message.data)
    except GmailPushError as exc:
        log.warning("discarding invalid Gmail Pub/Sub notification: %s", exc)
        message.ack()
        return
    if notification["email_address"] != config.GMAIL_TO.strip().lower():
        log.warning("discarding Gmail notification for an unexpected account")
        message.ack()
        return
    try:
        result = gmail_inbox.process()
    except (
        OSError,
        sqlite3.Error,
        gmail_auth.GmailAuthError,
        gmail_inbox.GmailInboxError,
    ) as exc:
        log.error("Gmail command reconciliation failed: %s", exc)
        message.nack()
        return
    if (
        result.get("status") in {"auth-or-api-error", "ledger-error"}
        or result.get("transient_failure")
    ):
        log.error(
            "Gmail command reconciliation requires retry (status=%s)",
            result.get("status"),
        )
        message.nack()
        return
    message.ack()
    if result.get("sent"):
        log.info("replied to %d Gmail command(s)", len(result["sent"]))


def _validate_runtime() -> None:
    missing = []
    if not config.GMAIL_PUSH_ENABLED:
        missing.append("FITLIT_GMAIL_PUSH_ENABLED=true")
    if not config.GMAIL_INBOX_ENABLED:
        missing.append("FITLIT_GMAIL_INBOX_ENABLED=true")
    if not gmail_auth.is_inbox_configured():
        missing.append("Gmail inbox and send OAuth credentials")
    if not config.GMAIL_PUBSUB_TOPIC:
        missing.append("FITLIT_GMAIL_PUBSUB_TOPIC")
    if not config.GMAIL_PUBSUB_SUBSCRIPTION:
        missing.append("FITLIT_GMAIL_PUBSUB_SUBSCRIPTION")
    if missing:
        raise GmailPushError("missing configuration: " + ", ".join(missing))


def run() -> None:
    """Keep one outbound StreamingPull connection open and renew the watch daily."""
    _validate_runtime()
    ensure_watch()
    stopped = threading.Event()

    def stop(_signum, _frame) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        subscriber = pubsub_v1.SubscriberClient()
    except (DefaultCredentialsError, OSError) as exc:
        raise GmailPushError(f"Pub/Sub credentials are unavailable: {exc}") from exc
    future = subscriber.subscribe(
        config.GMAIL_PUBSUB_SUBSCRIPTION,
        callback=handle_message,
        flow_control=pubsub_v1.types.FlowControl(max_messages=1),
        await_callbacks_on_shutdown=True,
    )
    log.info("Gmail Pub/Sub listener started")
    try:
        while not stopped.wait(3600):
            if future.done():
                try:
                    future.result()
                except GoogleAPICallError as exc:
                    raise GmailPushError(f"Pub/Sub listener stopped: {exc}") from exc
                raise GmailPushError("Pub/Sub listener stopped unexpectedly")
            ensure_watch()
    finally:
        future.cancel()
        subscriber.close()


def status() -> dict:
    state = _read_watch_state()
    expiration_ms = state.get("expiration_ms")
    return {
        "enabled": config.GMAIL_PUSH_ENABLED,
        "topic_configured": bool(config.GMAIL_PUBSUB_TOPIC),
        "subscription_configured": bool(config.GMAIL_PUBSUB_SUBSCRIPTION),
        "watch_expiration": (
            datetime.fromtimestamp(
                int(expiration_ms) / 1000,
                tz=timezone.utc,
            ).isoformat()
            if expiration_ms else None
        ),
        "watch_due": _watch_due(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run the Pub/Sub StreamingPull listener")
    subparsers.add_parser("watch", help="create or renew the Gmail mailbox watch")
    subparsers.add_parser("status", help="show local push configuration and watch expiry")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run()
            return 0
        if args.command == "watch":
            print(json.dumps(ensure_watch(force=True), indent=2))
            return 0
        print(json.dumps(status(), indent=2))
        return 0
    except GmailPushError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    raise SystemExit(main())
