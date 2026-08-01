from __future__ import annotations

import base64
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fitlit import email_agent, whatsapp_agent
from fitlit.gmail_client import EmailAttachment

PACIFIC = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 7, 31, 17, 0, tzinfo=PACIFIC)


def request() -> dict:
    return {
        "turns": [{
            "role": "user",
            "content": "How did I sleep?",
            "internal_date_ms": int(NOW.timestamp() * 1000),
        }],
        "now_ms": int(NOW.timestamp() * 1000),
    }


class WhatsAppAgentTests(unittest.TestCase):
    def test_turns_are_bounded_and_latest_must_be_user(self) -> None:
        with patch("fitlit.config.WHATSAPP_CONTEXT_MESSAGES", 1):
            turns = whatsapp_agent._turns(request()["turns"])
            self.assertEqual("user", turns[-1].role)

            invalid = request()["turns"] + [{
                "role": "assistant",
                "content": "answer",
                "internal_date_ms": int(NOW.timestamp() * 1000),
            }]
            with self.assertRaises(whatsapp_agent.WhatsAppAgentError):
                whatsapp_agent._turns(invalid)

    def test_draft_returns_text_and_in_memory_document_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fitlit-daily.xlsx"
            path.write_bytes(b"private spreadsheet")
            attachment = EmailAttachment(
                path=path,
                filename=path.name,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
            reply = email_agent.AgentReply(
                text="Grounded reply",
                html="<p>Grounded reply</p>",
                topic="daily",
                provider="copilot",
                evidence_paths=("daily.steps",),
                attachments=(attachment,),
            )

            @contextmanager
            def drafted(*args, **kwargs):
                yield reply

            with patch(
                "fitlit.whatsapp_agent.email_agent.draft",
                side_effect=drafted,
            ):
                value = whatsapp_agent.draft_payload(request())

        self.assertTrue(value["ok"])
        self.assertEqual("Grounded reply", value["text"])
        self.assertEqual(1, len(value["documents"]))
        document = value["documents"][0]
        self.assertEqual(
            b"private spreadsheet",
            base64.b64decode(document["content_base64"]),
        )
        self.assertEqual(len(b"private spreadsheet"), document["size"])

    def test_request_rejects_body_content_outside_bounds(self) -> None:
        value = request()
        value["turns"][0]["content"] = "\x00"
        with self.assertRaises(whatsapp_agent.WhatsAppAgentError):
            whatsapp_agent.draft_payload(value)


if __name__ == "__main__":
    unittest.main()
