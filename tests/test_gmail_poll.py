from __future__ import annotations

import unittest
from unittest.mock import patch

from fitlit import gmail_poll


class GmailPollTests(unittest.TestCase):
    def test_runtime_requires_inbox_and_oauth(self) -> None:
        with (
            patch("fitlit.config.GMAIL_INBOX_ENABLED", False),
            patch("fitlit.gmail_poll.gmail_auth.is_inbox_configured", return_value=False),
        ):
            with self.assertRaises(gmail_poll.GmailPollError) as raised:
                gmail_poll._validate_runtime()
        self.assertIn("FITLIT_GMAIL_INBOX_ENABLED=true", str(raised.exception))
        self.assertIn("OAuth credentials", str(raised.exception))

    def test_run_once_reconciles_existing_inbox_processor(self) -> None:
        expected = {
            "status": "ok",
            "sent": [{"reply_id": "reply"}],
            "transient_failure": False,
        }
        with patch(
            "fitlit.gmail_poll.gmail_inbox.process",
            return_value=expected,
        ) as process:
            result = gmail_poll.run_once()
        self.assertEqual(expected, result)
        process.assert_called_once()

    def test_status_reports_bounded_interval(self) -> None:
        with (
            patch("fitlit.config.GMAIL_INBOX_ENABLED", True),
            patch("fitlit.config.GMAIL_INBOX_POLL_SECONDS", 5),
            patch("fitlit.gmail_poll.gmail_auth.is_inbox_configured", return_value=True),
        ):
            self.assertEqual({
                "enabled": True,
                "configured": True,
                "poll_seconds": 5,
            }, gmail_poll.status())


if __name__ == "__main__":
    unittest.main()
