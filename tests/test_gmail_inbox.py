from __future__ import annotations

import base64
import email
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack, contextmanager
from datetime import datetime
from email import policy
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fitlit import email_agent, gmail_auth, gmail_client, gmail_inbox

PACIFIC = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 7, 27, 20, 0, tzinfo=PACIFIC)


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def message(
    *,
    message_id: str = "gmail-1",
    subject: str = "FitLit Ask: How did I sleep?",
    sender: str = "person@example.com",
    recipient: str = "person@example.com",
    body: str = "Please give me the useful details.",
    auto_submitted: str | None = None,
    thread_id: str = "thread-1",
    internal_date_ms: int = 1,
    generated: bool = False,
) -> dict:
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": recipient},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{message_id}@localhost>"},
    ]
    if auto_submitted:
        headers.append({"name": "Auto-Submitted", "value": auto_submitted})
    if generated:
        headers.append({
            "name": "X-FitLit-Notification",
            "value": "email-assistant",
        })
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": str(internal_date_ms),
        "labelIds": ["SENT", "INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": encoded(body)},
                },
                {
                    "mimeType": "text/html",
                    "filename": "",
                    "body": {"data": encoded(f"<b>{body}</b>")},
                },
            ],
        },
    }


class GmailInboxParsingTests(unittest.TestCase):
    def enter_config(self, stack: ExitStack) -> None:
        stack.enter_context(patch("fitlit.config.GMAIL_TO", "person@example.com"))
        stack.enter_context(
            patch("fitlit.config.GMAIL_INBOX_SUBJECT_PREFIX", "FitLit Ask:")
        )

    def test_accepts_exact_self_addressed_prefix_and_plain_text(self) -> None:
        with ExitStack() as stack:
            self.enter_config(stack)
            command, reason = gmail_inbox._parse_message(message())
        self.assertIsNone(reason)
        self.assertEqual("gmail-1", command.message_id)
        self.assertIn("How did I sleep?", command.question)
        self.assertIn("useful details", command.question)

    def test_accepts_bare_command_subject_with_question_in_body(self) -> None:
        payload = message(
            subject="FitLit Ask",
            body="How was my workout today?",
        )
        with ExitStack() as stack:
            self.enter_config(stack)
            command, reason = gmail_inbox._parse_message(payload)
        self.assertIsNone(reason)
        self.assertEqual("How was my workout today?", command.question)

    def test_rejects_wrong_sender_near_prefix_and_automated_mail(self) -> None:
        cases = [
            message(sender="you@example.com"),
            message(subject="Re: FitLit Ask: How did I sleep?"),
            message(auto_submitted="auto-generated"),
            {**message(), "labelIds": ["INBOX"]},
        ]
        with ExitStack() as stack:
            self.enter_config(stack)
            results = [gmail_inbox._parse_message(value) for value in cases]
        self.assertTrue(all(command is None for command, _ in results))

    def test_ignores_html_attachments_quotes_and_bounds_body(self) -> None:
        payload = message(body="keep this\n> quoted private text")
        payload["payload"]["parts"].append({
            "mimeType": "text/plain",
            "filename": "notes.txt",
            "body": {"data": encoded("attachment content")},
        })
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch("fitlit.config.GMAIL_INBOX_BODY_MAX_CHARS", 20)
            )
            command, _ = gmail_inbox._parse_message(payload)
        self.assertIn("keep this", command.question)
        self.assertNotIn("quoted", command.question)
        self.assertNotIn("attachment", command.question)
        self.assertNotIn("<b>", command.question)

    def test_does_not_recurse_into_attached_messages(self) -> None:
        payload = message(body="safe question")
        payload["payload"]["parts"].append({
            "mimeType": "message/rfc822",
            "filename": "",
            "headers": [],
            "parts": [{
                "mimeType": "text/plain",
                "filename": "",
                "body": {"data": encoded("nested attachment secret")},
            }],
        })
        with ExitStack() as stack:
            self.enter_config(stack)
            command, _ = gmail_inbox._parse_message(payload)
        self.assertNotIn("nested attachment", command.question)

    def test_standard_unprefixed_reply_headers_are_not_retained(self) -> None:
        payload = message(
            subject="Re: FitLit Ask",
            body=(
                "What about my workout?\n\n"
                "From: FitLit <person@example.com>\n"
                "Sent: Tuesday, July 28, 2026\n"
                "To: person@example.com\n"
                "Subject: Re: FitLit Ask\n\n"
                "Your prior sleep answer"
            ),
        )
        with ExitStack() as stack:
            self.enter_config(stack)
            command, reason = gmail_inbox._parse_message(
                payload,
                allow_followup=True,
            )
        self.assertIsNone(reason)
        self.assertEqual("What about my workout?", command.question)

    def test_generated_reply_is_rejected_even_when_prefix_matches(self) -> None:
        payload = message(
            subject="Re: previous command",
            generated=True,
        )
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch("fitlit.config.GMAIL_INBOX_SUBJECT_PREFIX", "Re:")
            )
            command, reason = gmail_inbox._parse_message(payload)
        self.assertIsNone(command)
        self.assertEqual("FitLit-generated message rejected", reason)

    def test_followup_requires_an_established_thread_scope(self) -> None:
        payload = message(
            subject="Re: FitLit Ask",
            body="What about today?",
        )
        with ExitStack() as stack:
            self.enter_config(stack)
            rejected, _ = gmail_inbox._parse_message(payload)
            accepted, reason = gmail_inbox._parse_message(
                payload,
                allow_followup=True,
            )
        self.assertIsNone(rejected)
        self.assertIsNone(reason)
        self.assertTrue(accepted.followup)
        self.assertEqual("What about today?", accepted.question)

    def test_malformed_api_json_becomes_inbox_error(self) -> None:
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with (
            patch("fitlit.gmail_auth.get_inbox_access_token", return_value="token"),
            patch("fitlit.gmail_inbox.urllib.request.urlopen", return_value=response),
        ):
            with self.assertRaises(gmail_inbox.GmailInboxError):
                gmail_inbox._api_json("messages")

    def test_rejects_internal_date_outside_sqlite_range(self) -> None:
        payload = message()
        payload["internalDate"] = str(2**63)
        with ExitStack() as stack:
            self.enter_config(stack)
            command, reason = gmail_inbox._parse_message(payload)
        self.assertIsNone(command)
        self.assertEqual("invalid Gmail internal date", reason)


class GmailInboxAuthTests(unittest.TestCase):
    def test_readonly_consent_is_separate_from_send_scope(self) -> None:
        with (
            patch("fitlit.config.OAUTH_CLIENT_ID", "client"),
            patch("fitlit.config.GMAIL_READ_SCOPE", "gmail-readonly"),
        ):
            url = gmail_auth.build_consent_url(
                "state",
                scope="gmail-readonly",
            )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(["gmail-readonly"], query["scope"])
        self.assertEqual(["state"], query["state"])

    def test_inbox_token_uses_its_own_refresh_token_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "inbox-token.json"
            with (
                patch("fitlit.config.OAUTH_CLIENT_ID", "client"),
                patch("fitlit.config.OAUTH_CLIENT_SECRET", "secret"),
                patch(
                    "fitlit.config.GMAIL_INBOX_REFRESH_TOKEN",
                    "readonly-refresh",
                ),
                patch("fitlit.config.GMAIL_INBOX_TOKEN_STATE", cache),
                patch("fitlit.gmail_auth._read_cache", return_value={}),
                patch("fitlit.gmail_auth._write_cache") as write,
                patch(
                    "fitlit.gmail_auth.auth._post_token",
                    return_value={
                        "access_token": "readonly-access",
                        "expires_in": 3600,
                    },
                ) as post,
            ):
                token = gmail_auth.get_inbox_access_token()
        self.assertEqual("readonly-access", token)
        self.assertEqual(
            "readonly-refresh",
            post.call_args.args[0]["refresh_token"],
        )
        self.assertEqual(cache, write.call_args.args[2])

    def test_reply_message_contains_thread_headers(self) -> None:
        with patch("fitlit.config.GMAIL_TO", "person@example.com"):
            raw = gmail_client._raw_message(
                "Re: FitLit Ask",
                "answer",
                "<p>answer</p>",
                in_reply_to="<source@example>",
                references="<older@example> <source@example>",
                category="email-assistant",
                source_message_id="gmail-source",
            )
        parsed = email.message_from_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
            policy=policy.default,
        )
        self.assertEqual("<source@example>", parsed["In-Reply-To"])
        self.assertEqual("<older@example> <source@example>", parsed["References"])
        self.assertEqual("email-assistant", parsed["X-FitLit-Notification"])
        self.assertEqual(
            "gmail-source",
            parsed["X-FitLit-Source-Message-ID"],
        )

    def test_reply_message_attaches_generated_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.xlsx"
            path.write_bytes(b"spreadsheet")
            attachment = gmail_client.EmailAttachment(
                path=path,
                filename="report.xlsx",
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
            with patch("fitlit.config.GMAIL_TO", "person@example.com"):
                raw = gmail_client._raw_message(
                    "Re: FitLit Ask",
                    "answer",
                    "<p>answer</p>",
                    attachments=(attachment,),
                )
        parsed = email.message_from_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
            policy=policy.default,
        )
        attachments = list(parsed.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual("report.xlsx", attachments[0].get_filename())

    def test_network_and_server_send_failures_are_delivery_uncertain(
        self,
    ) -> None:
        failures = [
            TimeoutError("timed out"),
            urllib.error.HTTPError(
                "https://gmail.googleapis.test",
                503,
                "Unavailable",
                {},
                io.BytesIO(b'{"error":"temporary"}'),
            ),
        ]
        for failure in failures:
            with (
                self.subTest(failure=type(failure).__name__),
                patch("fitlit.config.GMAIL_TO", "person@example.com"),
                patch(
                    "fitlit.gmail_auth.get_access_token",
                    return_value="token",
                ),
                patch(
                    "fitlit.gmail_client.urllib.request.urlopen",
                    side_effect=failure,
                ),
            ):
                with self.assertRaises(gmail_client.GmailSendError) as raised:
                    gmail_client.send("subject", "text", "<p>html</p>")
            self.assertTrue(raised.exception.delivery_uncertain)
            self.assertFalse(raised.exception.retryable)

    def test_unusable_success_response_is_delivery_uncertain(self) -> None:
        for response_body in (b"{}", b"[]", b"not-json"):
            with (
                self.subTest(response_body=response_body),
                patch("fitlit.config.GMAIL_TO", "person@example.com"),
                patch(
                    "fitlit.gmail_auth.get_access_token",
                    return_value="token",
                ),
                patch(
                    "fitlit.gmail_client.urllib.request.urlopen",
                    return_value=io.BytesIO(response_body),
                ),
            ):
                with self.assertRaises(gmail_client.GmailSendError) as raised:
                    gmail_client.send("subject", "text", "<p>html</p>")
            self.assertTrue(raised.exception.delivery_uncertain)
            self.assertFalse(raised.exception.retryable)


class InboxStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = gmail_inbox.InboxStore(Path(self.temp.name) / "inbox.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command(
        self,
        value: str,
        *,
        thread_id: str = "thread",
        internal_date_ms: int = 1,
    ) -> gmail_inbox.InboundCommand:
        return gmail_inbox.InboundCommand(
            message_id=value,
            thread_id=thread_id,
            rfc_message_id=f"<{value}@example>",
            references=None,
            subject="FitLit Ask: status",
            question="status",
            internal_date_ms=internal_date_ms,
        )

    def test_reservation_deduplicates_and_enforces_independent_cap(self) -> None:
        with patch("fitlit.config.GMAIL_INBOX_DAILY_MAX", 2):
            self.assertTrue(self.store.reserve(self.command("one"), NOW, intent="agent"))
            self.store.finish("one", reply_id="reply-one", intent="daily")
            self.assertFalse(self.store.reserve(self.command("one"), NOW, intent="agent"))
            self.assertTrue(self.store.reserve(self.command("two"), NOW, intent="agent"))
            self.store.finish("two", reply_id="reply-two", intent="sleep")
            self.assertFalse(self.store.reserve(self.command("three"), NOW, intent="agent"))
        self.assertEqual(2, self.store.attempted_today("2026-07-27"))

    def test_retry_release_allows_next_attempt(self) -> None:
        command = self.command("retry")
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        self.store.retry(command.message_id, "temporary")
        self.assertEqual(1, self.store.attempted_today("2026-07-27"))
        self.assertTrue(self.store.has(command.message_id))
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE inbound_messages SET retry_after=? WHERE message_id=?",
                ("2000-01-01T00:00:00+00:00", command.message_id),
            )
            connection.commit()
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        self.assertEqual(2, self.store.attempted_today("2026-07-27"))

    def test_primary_thread_is_first_successfully_sent_chain(self) -> None:
        first = self.command("first", thread_id="first-thread")
        second = self.command("second", thread_id="second-thread")
        self.assertTrue(self.store.reserve(first, NOW, intent="agent"))
        self.store.finish(first.message_id, reply_id="one", intent="daily")
        self.assertTrue(self.store.reserve(second, NOW, intent="agent"))
        self.store.finish(second.message_id, reply_id="two", intent="sleep")
        self.assertEqual("first-thread", self.store.primary_thread_id())

    def test_pending_thread_ids_are_metadata_only_and_deduplicated(self) -> None:
        first = self.command("first", thread_id="first-thread")
        followup = self.command("followup", thread_id="first-thread")
        second = self.command("second", thread_id="second-thread")
        self.assertTrue(self.store.reserve(first, NOW, intent="agent"))
        self.assertTrue(self.store.reserve(followup, NOW, intent="agent"))
        self.assertTrue(self.store.reserve(second, NOW, intent="agent"))
        self.assertEqual(
            ["first-thread", "second-thread"],
            self.store.pending_thread_ids(),
        )

    def test_retry_backoff_pins_the_first_thread_before_primary(self) -> None:
        command = self.command("first", thread_id="first-thread")
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        self.store.retry(command.message_id, "temporary")
        payload = message(
            message_id="first",
            thread_id="first-thread",
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch("fitlit.config.GMAIL_INBOX_ENABLED", True)
            )
            stack.enter_context(
                patch("fitlit.config.GMAIL_TO", "person@example.com")
            )
            stack.enter_context(
                patch(
                    "fitlit.config.GMAIL_INBOX_SUBJECT_PREFIX",
                    "FitLit Ask:",
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_auth.is_inbox_configured",
                    return_value=True,
                )
            )
            thread_payloads = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[payload],
                )
            )
            discover = stack.enter_context(
                patch("fitlit.gmail_inbox._discover_thread")
            )
            send = stack.enter_context(
                patch("fitlit.gmail_inbox.gmail_client.send")
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual(["first"], result["skipped"])
        thread_payloads.assert_called_once_with("first-thread")
        discover.assert_not_called()
        send.assert_not_called()

    def test_stale_unreconciled_send_is_released_for_retry(self) -> None:
        command = self.command("stale", thread_id="first-thread")
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE inbound_attempts SET attempted_at=? "
                "WHERE message_id='stale'",
                ("2000-01-01T00:00:00+00:00",),
            )
            connection.commit()
        payload = message(
            message_id="stale",
            thread_id="first-thread",
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch("fitlit.config.GMAIL_INBOX_ENABLED", True)
            )
            stack.enter_context(
                patch("fitlit.config.GMAIL_TO", "person@example.com")
            )
            stack.enter_context(
                patch(
                    "fitlit.config.GMAIL_INBOX_SUBJECT_PREFIX",
                    "FitLit Ask:",
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_auth.is_inbox_configured",
                    return_value=True,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._get_thread_metadata",
                    return_value={"messages": []},
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[payload],
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertTrue(result["transient_failure"])
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status,error FROM inbound_messages "
                "WHERE message_id='stale'"
            ).fetchone()
        self.assertEqual("retryable", row["status"])
        self.assertEqual(
            "interrupted delivery attempt was not found in Gmail",
            row["error"],
        )

    def test_ledger_stores_metadata_without_email_content(self) -> None:
        command = self.command("context")
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        self.store.finish("context", reply_id="reply", intent="sleep")
        with self.store._connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(inbound_messages)"
                )
            }
        self.assertNotIn("question", columns)
        self.assertNotIn("body", columns)
        self.assertNotIn("content", columns)
        self.assertIn("rfc_message_id", columns)
        self.assertIn("gmail_internal_date", columns)

    def test_existing_ledger_is_migrated_without_losing_primary_thread(
        self,
    ) -> None:
        path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(path) as connection:
            connection.executescript("""
                CREATE TABLE inbound_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    pacific_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    reply_id TEXT,
                    error TEXT
                );
                CREATE TABLE inbound_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    pacific_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    finished_at TEXT,
                    reply_id TEXT,
                    error TEXT
                );
                INSERT INTO inbound_messages(
                    message_id,thread_id,pacific_date,status,created_at,reply_id
                ) VALUES(
                    'legacy-message','legacy-thread','2026-07-27','sent',
                    '2026-07-28T03:00:00+00:00','legacy-reply'
                );
            """)
        migrated = gmail_inbox.InboxStore(path)
        self.assertEqual("legacy-thread", migrated.primary_thread_id())
        with migrated._connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(inbound_messages)"
                )
            }
        self.assertTrue({
            "retry_after",
            "intent",
            "rfc_message_id",
            "gmail_internal_date",
        }.issubset(columns))

    def test_supersede_prevents_an_older_retry_from_running(self) -> None:
        command = self.command("old")
        self.assertTrue(self.store.reserve(command, NOW, intent="agent"))
        self.store.retry(command.message_id, "temporary")
        self.store.supersede(command.message_id, command.thread_id, NOW)
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status,retry_after FROM inbound_messages WHERE message_id=?",
                (command.message_id,),
            ).fetchone()
        self.assertEqual("ignored", row["status"])
        self.assertIsNone(row["retry_after"])
        self.assertTrue(self.store.has(command.message_id))

    def test_positional_followup_argument_remains_compatible(self) -> None:
        command = gmail_inbox.InboundCommand(
            "message",
            "thread",
            "<message@example>",
            None,
            "Re: FitLit Ask",
            "What about today?",
            True,
        )
        self.assertTrue(command.followup)
        self.assertEqual(0, command.internal_date_ms)


class InboxProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = gmail_inbox.InboxStore(Path(self.temp.name) / "inbox.db")
        self.answer = email_agent.AgentReply(
            text="Grounded answer",
            html="<p>Grounded answer</p>",
            topic="daily",
            provider="copilot",
            evidence_paths=("daily.activity.steps",),
            attachments=(),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def enter_config(self, stack: ExitStack) -> None:
        stack.enter_context(patch("fitlit.config.GMAIL_INBOX_ENABLED", True))
        stack.enter_context(patch("fitlit.config.GMAIL_TO", "person@example.com"))
        stack.enter_context(
            patch("fitlit.config.GMAIL_INBOX_SUBJECT_PREFIX", "FitLit Ask:")
        )
        stack.enter_context(
            patch("fitlit.gmail_auth.is_inbox_configured", return_value=True)
        )

    @contextmanager
    def drafted(self, turns, *, now=None):
        yield self.answer

    def test_dry_run_previews_without_reserving_or_sending(self) -> None:
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            draft = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=self.drafted,
                )
            )
            send = stack.enter_context(patch("fitlit.gmail_inbox.gmail_client.send"))
            result = gmail_inbox.process(NOW, dry_run=True, store=self.store)
        self.assertEqual("daily", result["preview"][0]["topic"])
        self.assertEqual("copilot", result["preview"][0]["provider"])
        self.assertEqual(1, result["preview"][0]["context_messages"])
        self.assertFalse(self.store.has("gmail-1"))
        self.assertEqual("user", draft.call_args.args[0][-1].role)
        send.assert_not_called()

    def test_sends_threaded_reply_once(self) -> None:
        @contextmanager
        def draft_after_reservation(*args, **kwargs):
            self.assertTrue(self.store.has("gmail-1"))
            yield self.answer

        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[message()],
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=draft_after_reservation,
                )
            )
            send = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    return_value="reply-1",
                )
            )
            first = gmail_inbox.process(NOW, store=self.store)
            second = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual("reply-1", first["sent"][0]["reply_id"])
        self.assertEqual([], second["sent"])
        self.assertEqual("thread-1", send.call_args.kwargs["thread_id"])
        self.assertEqual("<gmail-1@localhost>", send.call_args.kwargs["in_reply_to"])
        self.assertEqual(
            "gmail-1",
            send.call_args.kwargs["source_message_id"],
        )
        self.assertEqual("Re: FitLit Ask: How did I sleep?", send.call_args.args[0])
        send.assert_called_once()

    def test_retryable_send_failure_schedules_backoff(self) -> None:
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=self.drafted,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    side_effect=gmail_client.GmailSendError(
                        "retry",
                        retryable=True,
                    ),
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual(1, len(result["failed"]))
        self.assertTrue(self.store.has("gmail-1"))
        self.assertEqual(1, self.store.attempted_today("2026-07-27"))

    def test_uncertain_send_failure_stays_sending_for_reconciliation(
        self,
    ) -> None:
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=self.drafted,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    side_effect=gmail_client.GmailSendError(
                        "timeout",
                        delivery_uncertain=True,
                    ),
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertTrue(result["transient_failure"])
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status FROM inbound_messages "
                "WHERE message_id='gmail-1'"
            ).fetchone()
        self.assertEqual("sending", row["status"])

    def test_provider_failure_schedules_backoff(self) -> None:
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=email_agent.EmailAgentError(
                        "copilot timed out"
                    ),
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertTrue(result["transient_failure"])
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status,error FROM inbound_messages "
                "WHERE message_id='gmail-1'"
            ).fetchone()
        self.assertEqual("retryable", row["status"])
        self.assertEqual("copilot timed out", row["error"])

    def test_send_failure_deletes_generated_attachment(self) -> None:
        attachment_path: Path | None = None

        def adapter(_root: Path, **kwargs) -> str:
            return json.dumps({
                "text": "Here is your grounded FitLit response.",
                "html": (
                    "<section><h1>FitLit insight</h1>"
                    "<p>Here is your grounded FitLit response.</p></section>"
                ),
                "evidence_paths": ["daily.steps"],
                "artifacts": [{
                    "kind": "xlsx",
                    "evidence_paths": ["daily.steps"],
                }],
            })

        def fail_send(*args, **kwargs):
            nonlocal attachment_path
            attachment_path = kwargs["attachments"][0].path
            self.assertTrue(attachment_path.exists())
            raise gmail_client.GmailSendError("retry", retryable=True)

        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [message()]),
                )
            )
            stack.enter_context(
                patch("fitlit.config.EMAIL_AGENT_PROVIDER", "copilot")
            )
            stack.enter_context(
                patch(
                    "fitlit.email_agent.shutil.which",
                    return_value="/bin/copilot",
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.email_agent.build_grounding",
                    return_value={"daily": {"steps": 10000}},
                )
            )
            stack.enter_context(
                patch.dict(
                    email_agent._ADAPTERS,
                    {"copilot": adapter},
                    clear=False,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    side_effect=fail_send,
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertTrue(result["transient_failure"])
        self.assertIsNotNone(attachment_path)
        self.assertFalse(attachment_path.exists())

    def test_established_chain_never_runs_the_mailbox_search(self) -> None:
        original = gmail_inbox.InboundCommand(
            message_id="original",
            thread_id="thread-1",
            rfc_message_id="<original@localhost>",
            references=None,
            subject="FitLit Ask",
            question="How did I sleep?",
        )
        self.assertTrue(self.store.reserve(original, NOW, intent="agent"))
        self.store.finish("original", reply_id="first-reply", intent="sleep")
        followup = message(
            message_id="followup",
            subject="Re: FitLit Ask",
            body="What about today?",
            internal_date_ms=2,
        )
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[followup],
                )
            )
            mailbox_search = stack.enter_context(
                patch("fitlit.gmail_inbox._list_message_ids")
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=self.drafted,
                )
            )
            send = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    return_value="second-reply",
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual("second-reply", result["sent"][0]["reply_id"])
        self.assertEqual("Re: FitLit Ask", send.call_args.args[0])
        mailbox_search.assert_not_called()

    def test_only_latest_user_message_is_answered(self) -> None:
        older = message(
            message_id="older",
            subject="FitLit Ask",
            body="First question",
            internal_date_ms=1,
        )
        latest = message(
            message_id="latest",
            subject="Re: FitLit Ask",
            body="Latest question",
            internal_date_ms=2,
        )

        @contextmanager
        def capture(turns, *, now=None):
            self.assertEqual("Latest question", turns[-1].content)
            self.assertLessEqual(len(turns), 5)
            yield self.answer

        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                    return_value=("thread-1", [older, latest]),
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=capture,
                )
            )
            send = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    return_value="latest-reply",
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual("latest", result["sent"][0]["message_id"])
        send.assert_called_once()
        with self.store._connect() as connection:
            older_row = connection.execute(
                "SELECT status FROM inbound_messages WHERE message_id='older'"
            ).fetchone()
        self.assertEqual("ignored", older_row["status"])

    def test_latest_five_thread_messages_include_generated_replies(self) -> None:
        original = gmail_inbox.InboundCommand(
            message_id="original",
            thread_id="thread-1",
            rfc_message_id="<original@localhost>",
            references=None,
            subject="FitLit Ask",
            question="How did I sleep?",
        )
        self.assertTrue(self.store.reserve(original, NOW, intent="agent"))
        self.store.finish("original", reply_id="first-reply", intent="sleep")
        generated = message(
            message_id="generated",
            subject="Re: FitLit Ask",
            body="Prior grounded answer",
            internal_date_ms=2,
            generated=True,
        )
        latest = message(
            message_id="latest",
            subject="Re: FitLit Ask",
            body="Newest request",
            internal_date_ms=3,
        )

        @contextmanager
        def capture(turns, *, now=None):
            self.assertEqual(["assistant", "user"], [turn.role for turn in turns])
            self.assertEqual("Newest request", turns[-1].content)
            yield self.answer

        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[generated, latest],
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=capture,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    return_value="latest-reply",
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual(2, result["sent"][0]["context_messages"])

    def test_followup_reconciles_reply_delivered_before_ledger_finish(self) -> None:
        original = gmail_inbox.InboundCommand(
            message_id="original",
            thread_id="thread-1",
            rfc_message_id="<original@localhost>",
            references=None,
            subject="FitLit Ask",
            question="How did I sleep?",
        )
        self.assertTrue(self.store.reserve(original, NOW, intent="agent"))
        followup = message(
            message_id="followup",
            subject="Re: FitLit Ask",
            body="What about today?",
            internal_date_ms=2,
        )
        delivered_reply = {
            "id": "delivered-reply",
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {
                        "name": "X-FitLit-Notification",
                        "value": "email-assistant",
                    },
                    {
                        "name": "In-Reply-To",
                        "value": "<original@localhost>",
                    },
                ],
            },
        }
        with ExitStack() as stack:
            self.enter_config(stack)
            discover = stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._discover_thread",
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[followup],
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._get_thread_metadata",
                    return_value={"messages": [delivered_reply]},
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.email_agent.draft",
                    side_effect=self.drafted,
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox.gmail_client.send",
                    return_value="followup-reply",
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual("followup-reply", result["sent"][0]["reply_id"])
        with self.store._connect() as connection:
            original_row = connection.execute(
                "SELECT status,reply_id FROM inbound_messages WHERE message_id='original'"
            ).fetchone()
        self.assertEqual("sent", original_row["status"])
        self.assertEqual("delivered-reply", original_row["reply_id"])
        discover.assert_not_called()

    def test_reconciliation_uses_immutable_source_id_without_message_id(
        self,
    ) -> None:
        original = gmail_inbox.InboundCommand(
            message_id="original",
            thread_id="thread-1",
            rfc_message_id=None,
            references=None,
            subject="FitLit Ask",
            question="How did I sleep?",
        )
        self.assertTrue(self.store.reserve(original, NOW, intent="agent"))
        delivered_reply = {
            "id": "delivered-reply",
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {
                        "name": "X-FitLit-Notification",
                        "value": "email-assistant",
                    },
                    {
                        "name": "X-FitLit-Source-Message-ID",
                        "value": "original",
                    },
                ],
            },
        }
        with ExitStack() as stack:
            self.enter_config(stack)
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._get_thread_metadata",
                    return_value={"messages": [delivered_reply]},
                )
            )
            stack.enter_context(
                patch(
                    "fitlit.gmail_inbox._thread_payloads",
                    return_value=[],
                )
            )
            result = gmail_inbox.process(NOW, store=self.store)
        self.assertEqual([], result["sent"])
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status,reply_id FROM inbound_messages "
                "WHERE message_id='original'"
            ).fetchone()
        self.assertEqual("sent", row["status"])
        self.assertEqual("delivered-reply", row["reply_id"])

    def test_thread_fetch_downloads_only_latest_five_messages(self) -> None:
        ids = [f"message-{index}" for index in range(8)]

        def payload(message_id: str) -> dict:
            index = int(message_id.rsplit("-", 1)[-1])
            return message(
                message_id=message_id,
                internal_date_ms=index,
            )

        with (
            patch(
                "fitlit.gmail_inbox._get_thread_message_ids",
                return_value=ids,
            ),
            patch(
                "fitlit.gmail_inbox._get_message",
                side_effect=payload,
            ) as get_message,
            patch("fitlit.config.EMAIL_AGENT_CONTEXT_MESSAGES", 5),
        ):
            values = gmail_inbox._thread_payloads("thread-1")
        self.assertEqual(ids[-5:], [value["id"] for value in values])
        self.assertEqual(
            ids[-5:],
            [call.args[0] for call in get_message.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
