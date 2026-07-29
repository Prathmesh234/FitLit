"""Near-real-time Gmail command polling without Google Cloud dependencies."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import threading
from datetime import datetime

from fitlit import config, email_agent, gmail_auth, gmail_inbox
from fitlit.journal import PACIFIC

log = logging.getLogger("fitlit.gmail_poll")


class GmailPollError(RuntimeError):
    """Raised when the Gmail-only listener cannot start."""


def _validate_runtime() -> None:
    missing = []
    if not config.GMAIL_INBOX_ENABLED:
        missing.append("FITLIT_GMAIL_INBOX_ENABLED=true")
    if not gmail_auth.is_inbox_configured():
        missing.append("Gmail inbox and send OAuth credentials")
    if (
        config.EMAIL_AGENT_PROVIDER not in email_agent.PROVIDERS
        or not shutil.which(config.EMAIL_AGENT_PROVIDER)
    ):
        missing.append(
            f"headless email provider {config.EMAIL_AGENT_PROVIDER!r}"
        )
    if missing:
        raise GmailPollError("missing configuration: " + ", ".join(missing))


def run_once() -> dict:
    """Reconcile the constrained command inbox once."""
    result = gmail_inbox.process(datetime.now(PACIFIC))
    if result.get("status") in {"auth-or-api-error", "ledger-error"}:
        log.error("Gmail reconciliation returned %s", result["status"])
    elif result.get("transient_failure"):
        log.warning("Gmail command retry scheduled with durable backoff")
    elif result.get("sent"):
        log.info("replied to %d Gmail command(s)", len(result["sent"]))
    return result


def run() -> None:
    """Poll Gmail until terminated, using interruptible waits between checks."""
    _validate_runtime()
    stopped = threading.Event()

    def stop(_signum, _frame) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info(
        "Gmail-only command listener started with a %d-second interval "
        "using %s model %s at %s effort with %d-message context",
        config.GMAIL_INBOX_POLL_SECONDS,
        config.EMAIL_AGENT_PROVIDER,
        email_agent.selected_model() or "provider-default",
        config.EMAIL_AGENT_REASONING_EFFORT,
        config.EMAIL_AGENT_CONTEXT_MESSAGES,
    )
    while not stopped.is_set():
        run_once()
        stopped.wait(config.GMAIL_INBOX_POLL_SECONDS)


def status() -> dict:
    return {
        "enabled": config.GMAIL_INBOX_ENABLED,
        "configured": gmail_auth.is_inbox_configured(),
        "poll_seconds": config.GMAIL_INBOX_POLL_SECONDS,
        "provider": config.EMAIL_AGENT_PROVIDER,
        "model": email_agent.selected_model() or None,
        "reasoning_effort": config.EMAIL_AGENT_REASONING_EFFORT,
        "context_messages": config.EMAIL_AGENT_CONTEXT_MESSAGES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run the Gmail-only command listener")
    subparsers.add_parser("once", help="reconcile commands once")
    subparsers.add_parser("status", help="show Gmail-only listener configuration")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run()
            return 0
        if args.command == "once":
            print(json.dumps(run_once(), indent=2))
            return 0
        print(json.dumps(status(), indent=2))
        return 0
    except GmailPollError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    raise SystemExit(main())
