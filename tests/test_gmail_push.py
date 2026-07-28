from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fitlit import gmail_push
from scripts import install_services


class GmailWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "gmail-watch.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_watch_due_uses_expiration_and_topic(self) -> None:
        self.state.write_text(json.dumps({
            "expiration_ms": 100_000_000,
            "topic": "projects/project/topics/fitlit",
            "renewed_at": "1970-01-01T00:16:40+00:00",
        }))
        with (
            patch("fitlit.config.GMAIL_WATCH_STATE", self.state),
            patch(
                "fitlit.config.GMAIL_PUBSUB_TOPIC",
                "projects/project/topics/fitlit",
            ),
            patch("fitlit.config.GMAIL_WATCH_RENEW_HOURS", 24),
        ):
            self.assertFalse(gmail_push._watch_due(now=2_000))
            self.assertTrue(gmail_push._watch_due(now=90_000))

    def test_ensure_watch_filters_sent_and_persists_response(self) -> None:
        response = {"historyId": "123", "expiration": "2000000"}
        with (
            patch("fitlit.config.GMAIL_WATCH_STATE", self.state),
            patch("fitlit.config.STATE_DIR", Path(self.temp.name)),
            patch(
                "fitlit.config.GMAIL_PUBSUB_TOPIC",
                "projects/project/topics/fitlit",
            ),
            patch("fitlit.gmail_push.gmail_inbox._api_json", return_value=response) as api,
        ):
            state = gmail_push.ensure_watch(force=True)
        api.assert_called_once_with(
            "watch",
            method="POST",
            body={
                "topicName": "projects/project/topics/fitlit",
                "labelIds": ["SENT"],
                "labelFilterBehavior": "INCLUDE",
            },
        )
        self.assertEqual("123", state["history_id"])
        self.assertEqual(0o600, self.state.stat().st_mode & 0o777)

    def test_invalid_watch_response_is_rejected(self) -> None:
        with (
            patch("fitlit.config.GMAIL_WATCH_STATE", self.state),
            patch("fitlit.config.STATE_DIR", Path(self.temp.name)),
        ):
            with self.assertRaises(gmail_push.GmailPushError):
                gmail_push._write_watch_state({"historyId": "not-a-number"})


class GmailPushMessageTests(unittest.TestCase):
    def message(self, payload: dict) -> MagicMock:
        message = MagicMock()
        message.data = json.dumps(payload).encode()
        return message

    def test_matching_notification_processes_and_acknowledges(self) -> None:
        message = self.message({
            "emailAddress": "person@example.com",
            "historyId": "123",
        })
        with (
            patch("fitlit.config.GMAIL_TO", "person@example.com"),
            patch(
                "fitlit.gmail_push.gmail_inbox.process",
                return_value={"status": "ok", "sent": [{"reply_id": "reply"}]},
            ) as process,
        ):
            gmail_push.handle_message(message)
        process.assert_called_once_with()
        message.ack.assert_called_once_with()
        message.nack.assert_not_called()

    def test_transient_reconciliation_error_is_retried(self) -> None:
        message = self.message({
            "emailAddress": "person@example.com",
            "historyId": "124",
        })
        with (
            patch("fitlit.config.GMAIL_TO", "person@example.com"),
            patch(
                "fitlit.gmail_push.gmail_inbox.process",
                return_value={"status": "auth-or-api-error", "sent": []},
            ),
        ):
            gmail_push.handle_message(message)
        message.nack.assert_called_once_with()
        message.ack.assert_not_called()

    def test_partial_transient_failure_is_retried(self) -> None:
        message = self.message({
            "emailAddress": "person@example.com",
            "historyId": "126",
        })
        with (
            patch("fitlit.config.GMAIL_TO", "person@example.com"),
            patch(
                "fitlit.gmail_push.gmail_inbox.process",
                return_value={
                    "status": "ok",
                    "sent": [],
                    "failed": [{"error": "temporary"}],
                    "transient_failure": True,
                },
            ),
        ):
            gmail_push.handle_message(message)
        message.nack.assert_called_once_with()
        message.ack.assert_not_called()

    def test_invalid_or_wrong_account_notification_is_discarded(self) -> None:
        malformed = MagicMock()
        malformed.data = b"not-json"
        wrong = self.message({
            "emailAddress": "other@example.com",
            "historyId": "125",
        })
        with patch("fitlit.config.GMAIL_TO", "person@example.com"):
            gmail_push.handle_message(malformed)
            gmail_push.handle_message(wrong)
        malformed.ack.assert_called_once_with()
        wrong.ack.assert_called_once_with()

    def test_decode_requires_numeric_history_id(self) -> None:
        with self.assertRaises(gmail_push.GmailPushError):
            gmail_push._decode_notification(
                b'{"emailAddress":"person@example.com","historyId":"bad"}'
            )


class GmailPushConfigurationTests(unittest.TestCase):
    def test_runtime_requires_both_inbox_and_pubsub_configuration(self) -> None:
        with (
            patch("fitlit.config.GMAIL_PUSH_ENABLED", True),
            patch("fitlit.config.GMAIL_INBOX_ENABLED", False),
            patch("fitlit.config.GMAIL_PUBSUB_TOPIC", ""),
            patch("fitlit.config.GMAIL_PUBSUB_SUBSCRIPTION", ""),
            patch("fitlit.gmail_push.gmail_auth.is_inbox_configured", return_value=False),
        ):
            with self.assertRaises(gmail_push.GmailPushError) as raised:
                gmail_push._validate_runtime()
        self.assertIn("FITLIT_GMAIL_INBOX_ENABLED=true", str(raised.exception))
        self.assertIn("FITLIT_GMAIL_PUBSUB_TOPIC", str(raised.exception))

    def test_installer_stops_listener_when_push_is_disabled(self) -> None:
        with (
            patch("scripts.install_services.os.geteuid", return_value=0),
            patch("scripts.install_services._push_enabled", return_value=False),
            patch("scripts.install_services.subprocess.run") as run,
        ):
            install_services.install([], start=True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            ["systemctl", "disable", "--now", "fitlit-gmail-push.service"],
            commands,
        )


if __name__ == "__main__":
    unittest.main()
