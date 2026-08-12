from __future__ import annotations

import io
import json
import subprocess
import sys
import signal
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fitlit import config, email_agent, telegram_service
from fitlit.gmail_client import EmailAttachment
from fitlit.journal import PACIFIC

TOKEN = "123456789:" + ("a" * 35)
USER_ID = 123456789


def update(
    text: str | None = "How did I sleep?",
    *,
    update_id: int = 10,
    user_id: int = USER_ID,
    chat_type: str = "private",
) -> dict:
    message = {
        "message_id": 20,
        "date": 1785960000,
        "from": {"id": user_id, "is_bot": False},
        "chat": {"id": user_id, "type": chat_type},
    }
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


class FakeResponse:
    def __init__(self, value: dict) -> None:
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, maximum: int = -1) -> bytes:
        return self.value[:maximum]


class FakeClient:
    def __init__(self) -> None:
        self.texts: list[tuple[int, str]] = []
        self.documents: list[tuple[int, str, bytes]] = []
        self.photos: list[tuple[int, str, bytes]] = []
        self.drop_pending: bool | None = None
        self.updates: list[dict] = []
        self.chat_actions: list[tuple[int, str]] = []
        self.typing_started = threading.Event()

    def set_retry_sleeper(self, retry_sleeper) -> None:
        self.retry_sleeper = retry_sleeper

    def get_me(self) -> dict:
        return {"is_bot": True, "username": "fitlit_test_bot"}

    def delete_webhook(self, *, drop_pending_updates: bool) -> None:
        self.drop_pending = drop_pending_updates

    def get_updates(self, **kwargs) -> list[dict]:
        values, self.updates = self.updates, []
        return values

    def send_text(self, chat_id: int, text: str, **kwargs) -> list[int]:
        self.texts.append((chat_id, text))
        return [len(self.texts)]

    def send_chat_action(
        self,
        chat_id: int,
        action: str = "typing",
    ) -> None:
        self.chat_actions.append((chat_id, action))
        self.typing_started.set()

    def send_document(
        self,
        chat_id: int,
        path: Path,
        filename: str,
        mime_type: str,
    ) -> int:
        return self.send_document_bytes(
            chat_id,
            path.read_bytes(),
            filename,
            mime_type,
        )

    def send_document_bytes(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        **kwargs,
    ) -> int:
        self.documents.append((chat_id, filename, content))
        return len(self.documents)

    def send_photo(
        self,
        chat_id: int,
        path: Path,
        filename: str,
        mime_type: str,
    ) -> int:
        return self.send_photo_bytes(
            chat_id,
            path.read_bytes(),
            filename,
            mime_type,
        )

    def send_photo_bytes(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        **kwargs,
    ) -> int:
        self.photos.append((chat_id, filename, content))
        return len(self.photos)


class TelegramParsingTests(unittest.TestCase):
    def test_accepts_only_direct_nonbot_messages(self) -> None:
        value = telegram_service.parse_inbound(update(update_id=0))
        self.assertIsNotNone(value)
        self.assertEqual(USER_ID, value.user_id)
        self.assertEqual("How did I sleep?", value.text)
        self.assertIsNone(
            telegram_service.parse_inbound(update(chat_type="group"))
        )

    def test_splits_long_plain_text_within_telegram_bounds(self) -> None:
        chunks = telegram_service.split_text(
            ("word " * 100).strip(),
            80,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(
            all(
                1 <= telegram_service.utf16_units(item) <= 80
                for item in chunks
            )
        )

    def test_budget_is_measured_in_utf16_code_units(self) -> None:
        # Telegram counts an astral emoji as two units even though Python
        # counts one character, so a naive character budget overflows.
        text = "🏋️" * 400
        chunks = telegram_service.split_text(text, 100)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(telegram_service.utf16_units(chunk), 100)
            self.assertLessEqual(
                len(chunk.encode("utf-16-le")) // 2,
                100,
            )
        self.assertEqual(text, "".join(chunks))

    def test_unicode_chunks_never_split_a_surrogate_pair(self) -> None:
        chunks = telegram_service.split_text("😀" * 51, 10)
        for chunk in chunks:
            self.assertEqual(chunk, chunk.encode("utf-16", "strict").decode(
                "utf-16"
            ))
            self.assertEqual(0, telegram_service.utf16_units(chunk) % 2)
            self.assertLessEqual(telegram_service.utf16_units(chunk), 10)
        self.assertEqual("😀" * 51, "".join(chunks))

    def test_split_prefers_paragraph_then_line_then_word_boundaries(
        self,
    ) -> None:
        paragraphs = telegram_service.split_text(
            "first paragraph text\n\nsecond paragraph text",
            30,
        )
        self.assertEqual(
            ["first paragraph text", "second paragraph text"],
            paragraphs,
        )
        lines = telegram_service.split_text(
            "first line of text\nsecond line of text",
            25,
        )
        self.assertEqual(
            ["first line of text", "second line of text"],
            lines,
        )
        words = telegram_service.split_text(
            "alpha bravo charlie delta echo foxtrot",
            20,
        )
        self.assertTrue(all(not item.startswith(" ") for item in words))
        self.assertEqual(
            "alpha bravo charlie delta echo foxtrot",
            " ".join(words),
        )

    def test_split_never_emits_an_empty_chunk(self) -> None:
        text = "alpha\n\n\n\n" + ("b" * 40) + "\n\n\n" + "  " + "c" * 40
        for budget in (2, 3, 7, 20, 41):
            chunks = telegram_service.split_text(text, budget)
            self.assertTrue(all(chunk.strip() for chunk in chunks))
            self.assertTrue(
                all(
                    telegram_service.utf16_units(chunk) <= budget
                    for chunk in chunks
                )
            )
        with self.assertRaises(telegram_service.TelegramError):
            telegram_service.split_text("   ", 100)

    def test_configured_chunk_budget_is_within_telegram_limit(self) -> None:
        self.assertLessEqual(config.TELEGRAM_MESSAGE_CHUNK_CHARS, 4096)
        chunks = telegram_service.split_text(
            "🥚" * 5000,
            config.TELEGRAM_MESSAGE_CHUNK_CHARS,
        )
        self.assertTrue(
            all(
                telegram_service.utf16_units(chunk)
                <= config.TELEGRAM_MESSAGE_CHUNK_CHARS
                for chunk in chunks
            )
        )


class TelegramStateTests(unittest.TestCase):
    def test_state_is_private_atomic_and_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "telegram-state.json"
            state = telegram_service.TelegramState(path)
            state.finish(42, "replied")
            value = json.loads(path.read_text())
            self.assertEqual(
                {"last_update_id", "status", "updated_at", "version"},
                set(value),
            )
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(43, state.offset)
            self.assertNotIn("message", path.read_text())
            self.assertNotIn("question", path.read_text())

    def test_transcript_archives_threads_without_deleting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "telegram.sqlite3"
            store = telegram_service.TelegramTranscriptStore(path)
            first = store.active(USER_ID)
            store.append(
                first,
                user_id=USER_ID,
                role="user",
                content="first question",
                sent_at_ms=1,
                source_update_id=1,
            )
            second = store.start_new(USER_ID, 2)
            store.append(
                second,
                user_id=USER_ID,
                role="user",
                content="second question",
                sent_at_ms=2,
                source_update_id=2,
            )
            self.assertNotEqual(
                first.conversation_id,
                second.conversation_id,
            )
            self.assertEqual("first question", store.history(first)[0].content)
            self.assertEqual("second question", store.history(second)[0].content)
            self.assertEqual((2, 2), store.stats(USER_ID))
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_active_conversation_adopts_current_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "telegram.sqlite3"
            )
            first = store.active(USER_ID)
            with store._connect() as connection:
                connection.execute(
                    """
                    UPDATE conversations
                    SET system_instructions = ?
                    WHERE id = ?
                    """,
                    ('["legacy evidence-only prompt"]', first.conversation_id),
                )
            refreshed = store.active(USER_ID)
            self.assertNotIn(
                "legacy evidence-only prompt",
                refreshed.system_instructions,
            )
            self.assertIn(
                "Respond naturally as a conversational FitLit assistant.",
                refreshed.system_instructions,
            )

    def test_new_conversation_update_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "telegram.sqlite3"
            )
            store.active(USER_ID)
            first = store.start_new(USER_ID, 42)
            retried = store.start_new(USER_ID, 42)
            self.assertEqual(first.conversation_id, retried.conversation_id)
            self.assertEqual((2, 0), store.stats(USER_ID))


class TelegramUpdateIdentifierTests(unittest.TestCase):
    def test_recent_lower_update_id_is_ignored_as_a_stale_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = telegram_service.TelegramState(path)
            state.finish(5000, "replied")
            state.finish(4999, "replied")
            state.finish(12, "replied")
            self.assertEqual(5000, state.value["last_update_id"])
            self.assertEqual(5001, state.offset)
            self.assertEqual(
                5000,
                json.loads(path.read_text())["last_update_id"],
            )

    def test_lower_update_id_is_accepted_after_a_week_of_silence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = telegram_service.TelegramState(path)
            state.finish(5000, "replied")
            stale = json.loads(path.read_text())
            stale["updated_at"] = int(time.time()) - (8 * 24 * 60 * 60)
            path.write_text(json.dumps(stale))
            reopened = telegram_service.TelegramState(path)
            # Telegram documents a randomly chosen next update_id after a week
            # without updates, so nothing may be confirmed blindly.
            self.assertIsNone(reopened.offset)
            reopened.finish(7, "replied")
            self.assertEqual(7, reopened.value["last_update_id"])
            self.assertEqual(8, reopened.offset)
            self.assertEqual(
                7,
                json.loads(path.read_text())["last_update_id"],
            )

    def test_idle_window_is_exactly_seven_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = telegram_service.TelegramState(path)
            state.finish(900, "replied")
            state.value["updated_at"] = int(time.time()) - (
                telegram_service.UPDATE_ID_RESET_IDLE_SECONDS - 60
            )
            state.finish(5, "replied")
            self.assertEqual(900, state.value["last_update_id"])
            self.assertEqual(901, state.offset)
            state.value["updated_at"] = int(time.time()) - (
                telegram_service.UPDATE_ID_RESET_IDLE_SECONDS
            )
            state.finish(5, "replied")
            self.assertEqual(5, state.value["last_update_id"])


class TelegramClientTests(unittest.TestCase):
    def test_unreachable_api_raises_a_transport_error(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        for failure in (
            urllib.error.URLError("no route to host"),
            TimeoutError("timed out"),
            OSError("connection reset"),
        ):
            with patch.object(client, "_open", side_effect=failure):
                with self.assertRaises(
                    telegram_service.TelegramTransportError
                ) as raised:
                    client.get_me()
            self.assertIsInstance(
                raised.exception,
                telegram_service.TelegramError,
            )
            self.assertNotIsInstance(
                raised.exception,
                telegram_service.TelegramAPIError,
            )

    def test_api_rejection_is_not_classified_as_transport(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        failure = urllib.error.HTTPError(
            "https://api.telegram.org/bot/getMe",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(json.dumps({
                "ok": False,
                "error_code": 429,
                "parameters": {"retry_after": 12},
            }).encode()),
        )
        with patch.object(client, "_open", side_effect=failure):
            with self.assertRaises(
                telegram_service.TelegramAPIError
            ) as raised:
                client.get_me()
        self.assertEqual(429, raised.exception.error_code)
        self.assertEqual(12, raised.exception.retry_after)
        self.assertNotIsInstance(
            raised.exception,
            telegram_service.TelegramTransportError,
        )

    def test_get_updates_requires_a_valid_update_identifier(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        for batch in (
            [{"message": {}}],
            [{"update_id": -1}],
            [{"update_id": "12"}],
            [{"update_id": True}],
            [{"update_id": 4}, {"update_id": None}],
        ):
            with patch.object(client, "_request", return_value=batch):
                with self.assertRaises(telegram_service.TelegramError):
                    client.get_updates(offset=None, timeout=1)
        with patch.object(
            client,
            "_request",
            return_value=[{"update_id": 0}, {"update_id": 9}],
        ):
            self.assertEqual(
                [{"update_id": 0}, {"update_id": 9}],
                client.get_updates(offset=None, timeout=1),
            )

    def test_send_text_uses_documented_link_preview_options(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        with patch.object(
            client,
            "_request",
            return_value={"message_id": 3},
        ) as request:
            client.send_text(USER_ID, "reply")
        fields = request.call_args.args[1]
        self.assertNotIn("disable_web_page_preview", fields)
        self.assertEqual(
            {"is_disabled": True},
            json.loads(fields["link_preview_options"]),
        )

    def test_png_is_uploaded_with_document_semantics(self) -> None:
        self.assertIn("image/png", telegram_service.DOCUMENT_MIME_TYPES)
        client = telegram_service.TelegramClient(TOKEN)
        with patch.object(
            client,
            "_request_with_flood_retry",
            return_value={"message_id": 5},
        ) as request:
            self.assertEqual(
                5,
                client.send_document_bytes(
                    USER_ID,
                    b"\x89PNG\r\n\x1a\nbytes",
                    "fitlit-snapshot.png",
                    "image/png",
                ),
            )
        self.assertEqual("sendDocument", request.call_args.args[0])
        self.assertIn(b'name="document"', request.call_args.kwargs["encoded"])

    def test_retryable_api_errors_are_classified_conservatively(self) -> None:
        for code in (None, 429, 500, 502, 503):
            self.assertTrue(
                telegram_service._retryable_api_error(
                    telegram_service.TelegramAPIError(code)
                )
            )
        for code in (400, 401, 403, 404, 413):
            self.assertFalse(
                telegram_service._retryable_api_error(
                    telegram_service.TelegramAPIError(code)
                )
            )

    def test_client_validates_api_envelope_without_exposing_token(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        with patch.object(
            client,
            "_open",
            return_value=FakeResponse({
                "ok": True,
                "result": {"is_bot": True, "id": 1},
            }),
        ):
            self.assertTrue(client.get_me()["is_bot"])
        with self.assertRaises(telegram_service.TelegramError) as raised:
            telegram_service.TelegramClient("not-a-token")
        self.assertNotIn("not-a-token", str(raised.exception))

    def test_send_text_requires_message_identifier(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        with patch.object(
            client,
            "_open",
            return_value=FakeResponse({"ok": True, "result": {}}),
        ):
            with self.assertRaises(telegram_service.TelegramError):
                client.send_text(USER_ID, "reply")

    def test_send_chat_action_accepts_only_confirmed_typing(self) -> None:
        client = telegram_service.TelegramClient(TOKEN)
        with patch.object(
            client,
            "_request",
            return_value=True,
        ) as request:
            client.send_chat_action(USER_ID)
        self.assertEqual(
            ("sendChatAction", {"chat_id": USER_ID, "action": "typing"}),
            request.call_args.args,
        )
        with self.assertRaises(telegram_service.TelegramError):
            client.send_chat_action(USER_ID, "upload_document")

    def test_retry_delay_honors_telegram_flood_control(self) -> None:
        error = telegram_service.TelegramAPIError(429, retry_after=75)
        self.assertEqual(75, telegram_service._retry_delay(error, 4))

    def test_send_text_retries_only_rate_limited_chunk(self) -> None:
        sleeps = []
        client = telegram_service.TelegramClient(
            TOKEN,
            retry_sleeper=lambda delay: sleeps.append(delay) or False,
        )
        with (
            patch(
                "fitlit.telegram_service.split_text",
                return_value=["first", "second"],
            ),
            patch.object(
                client,
                "_request",
                side_effect=[
                    {"message_id": 1},
                    telegram_service.TelegramAPIError(429, retry_after=7),
                    {"message_id": 2},
                ],
            ) as request,
        ):
            self.assertEqual([1, 2], client.send_text(USER_ID, "reply"))
        self.assertEqual([7], sleeps)
        self.assertEqual(
            ["first", "second", "second"],
            [call.args[1]["text"] for call in request.call_args_list],
        )

    def test_interrupted_flood_wait_is_known_not_delivered(self) -> None:
        states = []
        client = telegram_service.TelegramClient(
            TOKEN,
            retry_sleeper=lambda delay: True,
        )
        with patch.object(
            client,
            "_request",
            side_effect=telegram_service.TelegramAPIError(
                429,
                retry_after=5,
            ),
        ):
            with self.assertRaises(
                telegram_service.TelegramNotDeliveredError
            ):
                client.send_text(
                    USER_ID,
                    "reply",
                    retry_state=states.append,
                )
        self.assertEqual(["pending"], states)

    def test_send_document_retries_flood_control(self) -> None:
        sleeps = []
        client = telegram_service.TelegramClient(
            TOKEN,
            retry_sleeper=lambda delay: sleeps.append(delay) or False,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.xlsx"
            path.write_bytes(b"private evidence")
            with patch.object(
                client,
                "_request",
                side_effect=[
                    telegram_service.TelegramAPIError(429, retry_after=75),
                    {"message_id": 9},
                ],
            ) as request:
                message_id = client.send_document(
                    USER_ID,
                    path,
                    path.name,
                    (
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    ),
                )
        self.assertEqual(9, message_id)
        self.assertEqual([75], sleeps)
        self.assertEqual(2, request.call_count)

    def test_startup_retries_transient_transport_failure(self) -> None:
        client = FakeClient()
        calls = []

        def get_me():
            calls.append("get")
            if calls.count("get") == 1:
                raise telegram_service.TelegramError("temporary")
            return {"is_bot": True}

        client.get_me = get_me
        sleeps = []
        self.assertTrue(telegram_service._initialize(
            client,
            stopping=lambda: False,
            sleeper=sleeps.append,
        ))
        self.assertEqual([2], sleeps)
        self.assertFalse(client.drop_pending)


class TelegramProcessingTests(unittest.TestCase):
    def test_untrusted_updates_are_silently_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = telegram_service.TelegramState(
                Path(directory) / "state.json"
            )
            transcript = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            client = FakeClient()
            with patch(
                "fitlit.config.TELEGRAM_TRUSTED_USER_ID",
                USER_ID,
            ):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update(user_id=USER_ID + 1),
                )
            self.assertEqual([], client.texts)
            self.assertEqual("ignored", state.value["status"])

    def test_grounded_reply_uses_complete_persisted_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            conversation = transcript.active(USER_ID)
            transcript.append(
                conversation,
                user_id=USER_ID,
                role="user",
                content="earlier question",
                sent_at_ms=1,
                source_update_id=1,
            )
            transcript.append(
                conversation,
                user_id=USER_ID,
                role="assistant",
                content="earlier answer",
                sent_at_ms=2,
                source_update_id=1,
            )
            attachment_path = root / "fitlit-evidence.xlsx"
            attachment_path.write_bytes(b"private evidence")
            html_path = root / "fitlit-report.html"
            html_path.write_text("<html><body>safe report</body></html>")
            screenshot_path = root / "fitlit-snapshot.png"
            screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nprivate image")
            reply = email_agent.AgentReply(
                text="Grounded reply",
                html="<p>Grounded reply</p>",
                topic="daily",
                provider="copilot",
                evidence_paths=("daily.steps",),
                attachments=(
                    EmailAttachment(
                        path=attachment_path,
                        filename=attachment_path.name,
                        mime_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                    ),
                    EmailAttachment(
                        path=html_path,
                        filename=html_path.name,
                        mime_type="text/html",
                    ),
                    EmailAttachment(
                        path=screenshot_path,
                        filename=screenshot_path.name,
                        mime_type="image/png",
                    ),
                ),
            )

            client = FakeClient()
            drafted_turns = []

            @contextmanager
            def drafted(turns, **kwargs):
                drafted_turns.extend(turns)
                self.assertIsNone(kwargs["context_limit"])
                self.assertEqual("telegram", kwargs["channel"])
                self.assertEqual("gpt-5.6-terra", kwargs["model"])
                self.assertEqual("high", kwargs["reasoning_effort"])
                self.assertTrue(client.typing_started.wait(1))
                yield reply

            with (
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                patch(
                    "fitlit.config.TELEGRAM_COPILOT_MODEL",
                    "gpt-5.6-terra",
                ),
                patch("fitlit.config.TELEGRAM_REASONING_EFFORT", "high"),
                patch(
                    "fitlit.telegram_service.email_agent.draft",
                    side_effect=drafted,
                ),
            ):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update(),
                )

            self.assertEqual([(USER_ID, "Grounded reply")], client.texts)
            self.assertEqual([(USER_ID, "typing")], client.chat_actions)
            self.assertEqual(
                [
                    (USER_ID, "fitlit-evidence.xlsx", b"private evidence"),
                    (
                        USER_ID,
                        "fitlit-report.html",
                        b"<html><body>safe report</body></html>",
                    ),
                    (
                        USER_ID,
                        "fitlit-snapshot.png",
                        b"\x89PNG\r\n\x1a\nprivate image",
                    ),
                ],
                client.documents,
            )
            self.assertEqual([], client.photos)
            self.assertEqual(
                ["earlier question", "earlier answer", "How did I sleep?"],
                [turn.content for turn in drafted_turns],
            )
            history = transcript.history(conversation)
            self.assertEqual(
                ["user", "assistant", "user", "assistant"],
                [turn.role for turn in history],
            )
            self.assertEqual("replied", state.value["status"])

    def test_provider_failure_preserves_history_without_false_size_claim(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            client = FakeClient()
            with (
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                patch(
                    "fitlit.telegram_service.email_agent.draft",
                    side_effect=email_agent.EmailAgentError(
                        "copilot returned unsafe semantic HTML"
                    ),
                ),
            ):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update("How was my workout yesterday?"),
                )
            self.assertIn(
                "please try the question again",
                client.texts[0][1],
            )
            self.assertNotIn("/new", client.texts[0][1])
            conversation = transcript.active(USER_ID)
            with transcript._connect() as connection:
                stored = [
                    (row[0], row[1])
                    for row in connection.execute(
                        """
                        SELECT role, content FROM turns
                        WHERE conversation_id = ? ORDER BY id
                        """,
                        (conversation.conversation_id,),
                    ).fetchall()
                ]
            self.assertEqual(["user", "assistant"], [row[0] for row in stored])
            self.assertIn("please try the question again", stored[1][1])
            history = transcript.history(conversation)
            self.assertEqual(["user"], [turn.role for turn in history])

    def test_oversized_input_is_detected_by_type_not_message_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            client = FakeClient()
            with (
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                patch(
                    "fitlit.telegram_service.email_agent.draft",
                    side_effect=email_agent.EmailAgentInputTooLargeError(
                        "the provider refused this payload"
                    ),
                ),
            ):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update("summarize everything", update_id=88),
                )
            self.assertIn("/new", client.texts[0][1])
            self.assertIn("too large", client.texts[0][1])

    def test_reply_plan_construction_failure_degrades_to_safe_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            reply = email_agent.AgentReply(
                text="Grounded reply",
                html="<p>Grounded reply</p>",
                topic="daily",
                provider="copilot",
                evidence_paths=("daily.steps",),
                attachments=(),
            )

            @contextmanager
            def drafted(turns, **kwargs):
                yield reply

            for failure in (
                OSError("artifact disappeared"),
                telegram_service.TelegramError("chunking failed"),
            ):
                client = FakeClient()
                with (
                    patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                    patch(
                        "fitlit.telegram_service.email_agent.draft",
                        side_effect=drafted,
                    ),
                    patch(
                        "fitlit.telegram_service._reply_parts",
                        side_effect=failure,
                    ),
                ):
                    outcome = telegram_service.process_update(
                        client,
                        state,
                        transcript,
                        update("how did I sleep?", update_id=89),
                    )
                self.assertEqual("replied", outcome)
                self.assertIn(
                    "please try the question again",
                    client.texts[0][1],
                )
                transcript.discard_reply(USER_ID, 89)
                with transcript._connect() as connection:
                    connection.execute("DELETE FROM turns WHERE role = 'assistant'")

    def test_delivery_resume_skips_confirmed_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=77,
                assistant_text="first\nsecond",
                parts=[
                    ("text", b"first", "", "text/plain"),
                    ("text", b"second", "", "text/plain"),
                ],
            )
            first_client = FakeClient()
            calls = 0

            def fail_second(chat_id, text, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise telegram_service.TelegramAPIError(500)
                first_client.texts.append((chat_id, text))
                return [calls]

            first_client.send_text = fail_second
            with self.assertRaises(telegram_service.TelegramAPIError):
                telegram_service._deliver_reply_plan(
                    first_client,
                    store,
                    USER_ID,
                    plan,
                )
            retry_client = FakeClient()
            resumed = store.reply_plan(USER_ID, 77)
            self.assertIsNotNone(resumed)
            telegram_service._deliver_reply_plan(
                retry_client,
                store,
                USER_ID,
                resumed,
            )
            self.assertEqual([(USER_ID, "first")], first_client.texts)
            self.assertEqual([(USER_ID, "second")], retry_client.texts)
            self.assertIsNone(store.reply_plan(USER_ID, 77))

    def test_delivery_uncertain_part_is_not_resent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=78,
                assistant_text="reply",
                parts=[("text", b"reply", "", "text/plain")],
            )
            failing = FakeClient()
            failing.send_text = lambda *args, **kwargs: (_ for _ in ()).throw(
                telegram_service.TelegramError("delivery uncertain")
            )
            with self.assertRaises(telegram_service.TelegramError):
                telegram_service._deliver_reply_plan(
                    failing,
                    store,
                    USER_ID,
                    plan,
                )
            retry = FakeClient()
            resumed = store.reply_plan(USER_ID, 78)
            self.assertIsNotNone(resumed)
            telegram_service._deliver_reply_plan(
                retry,
                store,
                USER_ID,
                resumed,
            )
            self.assertEqual(1, len(retry.texts))
            self.assertNotEqual("reply", retry.texts[0][1])
            self.assertIn("did not resend it", retry.texts[0][1])
            with store._connect() as connection:
                stored = connection.execute(
                    """
                    SELECT content FROM turns
                    WHERE conversation_id = ? AND role = 'assistant'
                    """,
                    (conversation.conversation_id,),
                ).fetchone()[0]
            self.assertIn("did not resend", stored)
            self.assertTrue(stored.startswith("[NOT DELIVERED] "))
            history = store.history(conversation)
            self.assertEqual(
                ["[The previous reply was not delivered to the user.]"],
                [turn.content for turn in history],
            )

    def test_partial_delivery_exposes_only_confirmed_text_to_provider(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=79,
                assistant_text="first paragraph\nsecond paragraph",
                parts=[
                    ("text", b"first paragraph", "", "text/plain"),
                    ("text", b"second paragraph", "", "text/plain"),
                ],
            )
            store.set_part_status(plan, 0, "delivered", telegram_message_id=1)
            store.set_part_status(plan, 1, "failed")
            store.complete_reply(plan)
            with store._connect() as connection:
                row = connection.execute(
                    """
                    SELECT content,provider_content FROM turns
                    WHERE conversation_id = ? AND role = 'assistant'
                    """,
                    (conversation.conversation_id,),
                ).fetchone()
            self.assertIn("second paragraph", row[0])
            self.assertNotIn("second paragraph", row[1])
            self.assertIn("Only part", row[1])
            self.assertEqual(
                [row[1]],
                [turn.content for turn in store.history(conversation)],
            )

    def test_delivery_notice_waits_for_durable_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=80,
                assistant_text="reply",
                parts=[("text", b"reply", "", "text/plain")],
            )
            client = FakeClient()
            client.send_text = lambda *args, **kwargs: (_ for _ in ()).throw(
                telegram_service.TelegramAPIError(400)
            )
            with (
                patch.object(
                    store,
                    "complete_reply",
                    side_effect=sqlite3.OperationalError("busy"),
                ),
                self.assertRaises(sqlite3.OperationalError),
            ):
                telegram_service._deliver_reply_plan(
                    client,
                    store,
                    USER_ID,
                    plan,
                )
            self.assertEqual([], client.texts)

    def test_delivery_continues_after_uncertain_part(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=82,
                assistant_text="reply with artifact",
                parts=[
                    ("text", b"reply", "", "text/plain"),
                    (
                        "document",
                        b"artifact",
                        "fitlit-health.html",
                        "text/html",
                    ),
                ],
            )
            store.set_part_status(plan, 0, "sending")
            resumed = store.reply_plan(USER_ID, 82)
            self.assertIsNotNone(resumed)
            client = FakeClient()
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                resumed,
            )
            self.assertEqual(1, len(client.texts))
            self.assertIn("could not be confirmed", client.texts[0][1])
            self.assertNotIn("reply", client.texts[0][1])
            self.assertEqual(
                [(USER_ID, "fitlit-health.html", b"artifact")],
                client.documents,
            )
            self.assertIsNone(store.reply_plan(USER_ID, 82))

    def test_known_undelivered_part_returns_to_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=79,
                assistant_text="reply",
                parts=[("text", b"reply", "", "text/plain")],
            )
            failing = FakeClient()
            failing.send_text = lambda *args, **kwargs: (_ for _ in ()).throw(
                telegram_service.TelegramNotDeliveredError("stopped")
            )
            with self.assertRaises(
                telegram_service.TelegramNotDeliveredError
            ):
                telegram_service._deliver_reply_plan(
                    failing,
                    store,
                    USER_ID,
                    plan,
                )
            resumed = store.reply_plan(USER_ID, 79)
            self.assertIsNotNone(resumed)
            self.assertEqual("pending", resumed.parts[0].status)
            retry = FakeClient()
            telegram_service._deliver_reply_plan(
                retry,
                store,
                USER_ID,
                resumed,
            )
            self.assertEqual([(USER_ID, "reply")], retry.texts)

    def test_command_delivery_uncertainty_is_not_resent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            failing = FakeClient()
            failing.send_text = lambda *args, **kwargs: (_ for _ in ()).throw(
                telegram_service.TelegramError("delivery uncertain")
            )
            with patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID):
                with self.assertRaises(telegram_service.TelegramError):
                    telegram_service.process_update(
                        failing,
                        state,
                        transcript,
                        update("/help", update_id=80),
                    )
                retry = FakeClient()
                telegram_service.process_update(
                    retry,
                    state,
                    transcript,
                    update("/help", update_id=80),
                )
            self.assertEqual(1, len(retry.texts))
            self.assertIn("did not resend it", retry.texts[0][1])
            self.assertNotIn("FitLit is connected", retry.texts[0][1])
            self.assertEqual("command", state.value["status"])
            self.assertIsNone(transcript.reply_plan(USER_ID, 80))

    def test_confirmed_command_is_not_resent_before_offset_persistence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            inbound = telegram_service.parse_inbound(
                update("/help", update_id=81)
            )
            self.assertIsNotNone(inbound)
            first = FakeClient()
            telegram_service._deliver_fixed_text(
                first,
                transcript,
                inbound,
                "ready",
            )
            retry = FakeClient()
            telegram_service._deliver_fixed_text(
                retry,
                transcript,
                inbound,
                "ready",
            )
            self.assertEqual([(USER_ID, "ready")], first.texts)
            self.assertEqual([], retry.texts)
            self.assertTrue(transcript.fixed_reply_completed(USER_ID, 81))

    def test_new_archives_history_and_reset_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = telegram_service.TelegramState(
                Path(directory) / "state.json"
            )
            transcript = telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )
            first = transcript.active(USER_ID)
            transcript.append(
                first,
                user_id=USER_ID,
                role="user",
                content="old",
                sent_at_ms=1,
                source_update_id=1,
            )
            client = FakeClient()
            with patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update("/reset"),
                )
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update("/new", update_id=11),
                )
            self.assertEqual("old", transcript.history(first)[0].content)
            active = transcript.active(USER_ID)
            self.assertNotEqual(first.conversation_id, active.conversation_id)
            self.assertEqual([], transcript.history(active))
            self.assertIn("never deleted", client.texts[0][1])
            self.assertEqual("new-conversation", state.value["status"])

    def test_new_retry_reuses_conversation_after_delivery_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            transcript.active(USER_ID)
            failing = FakeClient()
            with (
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                patch.object(
                    failing,
                    "send_text",
                    side_effect=telegram_service.TelegramError("temporary"),
                ),
            ):
                with self.assertRaises(telegram_service.TelegramError):
                    telegram_service.process_update(
                        failing,
                        state,
                        transcript,
                        update("/new", update_id=25),
                    )
            client = FakeClient()
            with patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID):
                telegram_service.process_update(
                    client,
                    state,
                    transcript,
                    update("/new", update_id=25),
                )
            self.assertEqual((2, 0), transcript.stats(USER_ID))
            self.assertEqual("new-conversation", state.value["status"])


class TelegramDeliveryTests(unittest.TestCase):
    @contextmanager
    def _store(self):
        with tempfile.TemporaryDirectory() as directory:
            yield telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )

    def _plan(self, store, source_update_id, parts, text="reply text"):
        conversation = store.active(USER_ID)
        return conversation, store.prepare_reply(
            conversation,
            user_id=USER_ID,
            source_update_id=source_update_id,
            assistant_text=text,
            parts=parts,
        )

    @staticmethod
    def _stored_assistant(store, conversation):
        with store._connect() as connection:
            row = connection.execute(
                """
                SELECT content FROM turns
                WHERE conversation_id = ? AND role = 'assistant'
                ORDER BY id DESC LIMIT 1
                """,
                (conversation.conversation_id,),
            ).fetchone()
        return row[0] if row else None

    def test_unknown_and_rate_limited_statuses_stay_pending(self) -> None:
        for code in (None, 429, 500):
            with self._store() as store:
                conversation, plan = self._plan(
                    store,
                    900 + (code or 0),
                    [("text", b"reply text", "", "text/plain")],
                )
                client = FakeClient()
                client.send_text = lambda *a, **k: (_ for _ in ()).throw(
                    telegram_service.TelegramAPIError(code)
                )
                with self.assertRaises(telegram_service.TelegramAPIError):
                    telegram_service._deliver_reply_plan(
                        client,
                        store,
                        USER_ID,
                        plan,
                    )
                resumed = store.reply_plan(USER_ID, 900 + (code or 0))
                self.assertIsNotNone(resumed)
                self.assertEqual("pending", resumed.parts[0].status)
                self.assertIsNone(
                    self._stored_assistant(store, conversation)
                )

    def test_terminal_text_rejection_fails_remaining_parts_with_notice(
        self,
    ) -> None:
        with self._store() as store:
            conversation, plan = self._plan(
                store,
                910,
                [
                    ("text", b"first", "", "text/plain"),
                    ("text", b"second", "", "text/plain"),
                    (
                        "document",
                        b"artifact",
                        "fitlit-health.html",
                        "text/html",
                    ),
                ],
            )
            client = FakeClient()
            client.send_text = lambda *a, **k: (_ for _ in ()).throw(
                telegram_service.TelegramAPIError(400)
            )
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                plan,
            )
            self.assertEqual([], client.documents)
            self.assertIsNone(store.reply_plan(USER_ID, 910))
            stored = self._stored_assistant(store, conversation)
            self.assertTrue(stored.startswith("[NOT DELIVERED] "))
            self.assertIn("were rejected", stored)

    def test_terminal_artifact_rejection_continues_the_plan(self) -> None:
        with self._store() as store:
            conversation, plan = self._plan(
                store,
                920,
                [
                    ("text", b"answer", "", "text/plain"),
                    (
                        "document",
                        b"artifact",
                        "fitlit-health.html",
                        "text/html",
                    ),
                    (
                        "document",
                        b"screenshot",
                        "fitlit-snapshot.png",
                        "image/png",
                    ),
                ],
            )
            client = FakeClient()
            rejected = []

            def send_document_bytes(chat_id, content, filename, mime, **kw):
                if filename.endswith(".html"):
                    rejected.append(filename)
                    raise telegram_service.TelegramAPIError(400)
                client.documents.append((chat_id, filename, content))
                return len(client.documents)

            client.send_document_bytes = send_document_bytes
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                plan,
            )
            self.assertEqual(["fitlit-health.html"], rejected)
            self.assertEqual(
                [(USER_ID, "fitlit-snapshot.png", b"screenshot")],
                client.documents,
            )
            self.assertEqual(2, len(client.texts))
            self.assertEqual("answer", client.texts[0][1])
            self.assertIn("rejected part of that answer", client.texts[1][1])
            stored = self._stored_assistant(store, conversation)
            self.assertFalse(stored.startswith("[NOT DELIVERED] "))
            self.assertIn("were rejected", stored)

    def test_delivery_notice_failure_never_wedges_the_update(self) -> None:
        with self._store() as store:
            conversation, plan = self._plan(
                store,
                930,
                [("text", b"answer", "", "text/plain")],
            )
            client = FakeClient()

            def send_text(chat_id, text, **kwargs):
                raise telegram_service.TelegramAPIError(400)

            client.send_text = send_text
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                plan,
            )
            self.assertIsNone(store.reply_plan(USER_ID, 930))
            self.assertTrue(
                self._stored_assistant(store, conversation).startswith(
                    "[NOT DELIVERED] "
                )
            )

    def test_partial_delivery_is_not_marked_undelivered(self) -> None:
        with self._store() as store:
            conversation, plan = self._plan(
                store,
                940,
                [
                    ("text", b"answer", "", "text/plain"),
                    (
                        "document",
                        b"artifact",
                        "fitlit-health.html",
                        "text/html",
                    ),
                ],
            )
            client = FakeClient()
            client.send_document_bytes = (
                lambda *a, **k: (_ for _ in ()).throw(
                    telegram_service.TelegramAPIError(413)
                )
            )
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                plan,
            )
            stored = self._stored_assistant(store, conversation)
            self.assertFalse(stored.startswith("[NOT DELIVERED] "))
            self.assertIn("were rejected", stored)

    def test_reply_parts_stage_every_artifact_as_a_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            png = root / "fitlit-snapshot.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
            book = root / "fitlit-evidence.xlsx"
            book.write_bytes(b"workbook")
            reply = email_agent.AgentReply(
                text="Grounded reply",
                html="<p>Grounded reply</p>",
                topic="daily",
                provider="copilot",
                evidence_paths=("daily.steps",),
                attachments=(
                    EmailAttachment(
                        path=png,
                        filename=png.name,
                        mime_type="image/png",
                    ),
                    EmailAttachment(
                        path=book,
                        filename=book.name,
                        mime_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                    ),
                ),
            )
            parts = telegram_service._reply_parts(reply)
        self.assertEqual(
            ["text", "document", "document"],
            [part[0] for part in parts],
        )
        self.assertNotIn("photo", [part[0] for part in parts])

    def test_staged_photo_parts_still_deliver_after_the_upgrade(self) -> None:
        with self._store() as store:
            _, plan = self._plan(
                store,
                950,
                [
                    (
                        "photo",
                        b"legacy screenshot",
                        "fitlit-snapshot.png",
                        "image/png",
                    ),
                ],
            )
            client = FakeClient()
            telegram_service._deliver_reply_plan(
                client,
                store,
                USER_ID,
                plan,
            )
            self.assertEqual(
                [(USER_ID, "fitlit-snapshot.png", b"legacy screenshot")],
                client.photos,
            )


class TelegramContextTests(unittest.TestCase):
    @contextmanager
    def _store(self):
        with tempfile.TemporaryDirectory() as directory:
            yield telegram_service.TelegramTranscriptStore(
                Path(directory) / "transcript.sqlite3"
            )

    def test_history_hides_runtime_notices_without_deleting_them(
        self,
    ) -> None:
        with self._store() as store:
            conversation = store.active(USER_ID)
            contents = [
                ("user", "how did I sleep?"),
                ("assistant", telegram_service.PROVIDER_FAILURE_REPLY),
                ("user", "try again"),
                (
                    "assistant",
                    "You slept 7h12m.\n\nFitLit selected the following "
                    "grounded health data for your latest query:\n\n"
                    "- Sleep › Minutes: 432 [sleep.minutes]",
                ),
                ("user", "and yesterday?"),
                (
                    "assistant",
                    "Yesterday was 6h40m.\n\n"
                    + telegram_service.UNCERTAIN_ANNOTATION,
                ),
                ("user", "one more"),
                (
                    "assistant",
                    telegram_service.NOT_DELIVERED_PREFIX
                    + "Third answer.\n\n"
                    + telegram_service.FAILED_ANNOTATION,
                ),
                ("user", "last"),
                (
                    "assistant",
                    telegram_service.NOT_DELIVERED_PREFIX
                    + telegram_service.TOO_LARGE_REPLY,
                ),
            ]
            for index, (role, content) in enumerate(contents):
                store.append(
                    conversation,
                    user_id=USER_ID,
                    role=role,
                    content=content,
                    sent_at_ms=index + 1,
                    source_update_id=index + 1,
                )
            history = store.history(conversation)
            self.assertEqual(
                [
                    "how did I sleep?",
                    "try again",
                    "You slept 7h12m.",
                    "and yesterday?",
                    "Yesterday was 6h40m.",
                    "one more",
                    "[NOT DELIVERED] Third answer.",
                    "last",
                ],
                [turn.content for turn in history],
            )
            with store._connect() as connection:
                stored = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT content FROM turns
                        WHERE conversation_id = ? ORDER BY id
                        """,
                        (conversation.conversation_id,),
                    ).fetchall()
                ]
            self.assertEqual([item[1] for item in contents], stored)
            self.assertEqual((1, len(contents)), store.stats(USER_ID))

    def test_quarantine_table_is_added_to_an_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.sqlite3"
            store = telegram_service.TelegramTranscriptStore(path)
            conversation = store.active(USER_ID)
            store.append(
                conversation,
                user_id=USER_ID,
                role="user",
                content="legacy question",
                sent_at_ms=1,
                source_update_id=1,
            )
            with store._connect() as connection:
                connection.execute("DROP TABLE update_failures")
            reopened = telegram_service.TelegramTranscriptStore(path)
            self.assertEqual(0, reopened.quarantined_count())
            self.assertEqual(1, reopened.record_failure(1, "TelegramError"))
            self.assertEqual(
                ["legacy question"],
                [
                    turn.content
                    for turn in reopened.history(reopened.active(USER_ID))
                ],
            )
            self.assertEqual((1, 1), reopened.stats(USER_ID))

    def test_visible_text_helper_is_conservative(self) -> None:
        self.assertIsNone(
            telegram_service.model_visible_assistant_text(
                telegram_service.PROVIDER_FAILURE_REPLY
            )
        )
        self.assertIsNone(
            telegram_service.model_visible_assistant_text("   ")
        )
        self.assertEqual(
            "kept",
            telegram_service.model_visible_assistant_text("kept"),
        )
        self.assertEqual(
            "kept",
            telegram_service.model_visible_assistant_text(
                "kept\n\nFitLit selected the following grounded health "
                "data for your latest query:\n\n- A: 1 [a]"
            ),
        )
        self.assertEqual(
            "kept",
            telegram_service.model_visible_assistant_text(
                "kept\n\nGround truth (Fitbit)\n- Average pace: 5:57/km"
            ),
        )


class TelegramStatusTests(unittest.TestCase):
    def test_status_reports_operations_in_pacific_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "transcript.sqlite3"
            state = telegram_service.TelegramState(state_path)
            state.finish(4242, "replied")
            store = telegram_service.TelegramTranscriptStore(transcript_path)
            conversation = store.active(USER_ID)
            plan = store.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=1,
                assistant_text="pending answer",
                parts=[
                    ("text", b"one", "", "text/plain"),
                    ("text", b"two", "", "text/plain"),
                ],
            )
            store.set_part_status(plan, 0, "delivered")
            store.record_failure(99, "TelegramError")
            store.record_failure(99, "TelegramError")
            store.record_failure(99, "TelegramError")
            with (
                patch("fitlit.config.TELEGRAM_STATE_PATH", state_path),
                patch(
                    "fitlit.config.TELEGRAM_TRANSCRIPT_PATH",
                    transcript_path,
                ),
                patch(
                    "fitlit.config.TELEGRAM_LOCK_PATH",
                    root / "telegram-service.lock",
                ),
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                patch("sys.stdout", new=io.StringIO()) as output,
            ):
                self.assertEqual(0, telegram_service.status())
                report = json.loads(output.getvalue())
                self.assertFalse(report["listener_running"])
                with telegram_service.single_instance("the listener"):
                    with patch("sys.stdout", new=io.StringIO()) as running:
                        telegram_service.status()
                    self.assertTrue(
                        json.loads(running.getvalue())["listener_running"]
                    )
        self.assertEqual(4242, report["last_update_id"])
        self.assertEqual("replied", report["last_outcome"])
        self.assertEqual(1, report["pending_outbound_parts"])
        self.assertEqual(1, report["quarantined_updates"])
        self.assertLess(report["seconds_since_last_update"], 60)
        self.assertIn("America/Los_Angeles", report["timezone"])
        self.assertIn(
            datetime.now(PACIFIC).strftime("%Y-%m-%d"),
            report["last_update_at"],
        )
        self.assertNotIn("+00:00", report["last_update_at"])
        for key in ("provider_installed", "model_valid", "effort_valid"):
            self.assertIsInstance(report[key], bool)

    def test_status_is_safe_before_any_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "fitlit.config.TELEGRAM_STATE_PATH",
                    root / "state.json",
                ),
                patch(
                    "fitlit.config.TELEGRAM_TRANSCRIPT_PATH",
                    root / "transcript.sqlite3",
                ),
                patch(
                    "fitlit.config.TELEGRAM_LOCK_PATH",
                    root / "telegram-service.lock",
                ),
                patch("sys.stdout", new=io.StringIO()) as output,
            ):
                self.assertEqual(0, telegram_service.status())
                report = json.loads(output.getvalue())
        self.assertIsNone(report["last_update_id"])
        self.assertIsNone(report["last_update_at"])
        self.assertIsNone(report["seconds_since_last_update"])
        self.assertEqual(0, report["quarantined_updates"])
        self.assertFalse(report["listener_running"])


class TelegramRunTests(unittest.TestCase):
    @contextmanager
    def _service(self):
        original = (
            signal.getsignal(signal.SIGTERM),
            signal.getsignal(signal.SIGINT),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                with (
                    patch("fitlit.config.TELEGRAM_ENABLED", True),
                    patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
                    patch(
                        "fitlit.config.TELEGRAM_STATE_PATH",
                        root / "state.json",
                    ),
                    patch(
                        "fitlit.config.TELEGRAM_TRANSCRIPT_PATH",
                        root / "transcript.sqlite3",
                    ),
                    patch(
                        "fitlit.config.TELEGRAM_LOCK_PATH",
                        root / "telegram-service.lock",
                    ),
                    patch(
                        "fitlit.telegram_service._retry_delay",
                        return_value=0,
                    ),
                ):
                    yield root
            finally:
                signal.signal(signal.SIGTERM, original[0])
                signal.signal(signal.SIGINT, original[1])

    def test_polling_401_is_a_configuration_failure_with_exit_78(self) -> None:
        client = FakeClient()

        def get_updates(**kwargs):
            raise telegram_service.TelegramAPIError(401)

        client.get_updates = get_updates
        with self._service():
            with self.assertRaises(telegram_service.TelegramConfigError):
                telegram_service.run(client)
            with patch(
                "fitlit.telegram_service.TelegramClient",
                return_value=client,
            ):
                with patch("sys.stderr", new=io.StringIO()) as errors:
                    self.assertEqual(78, telegram_service.main(["run"]))
            self.assertIn("rejected the configured bot token", errors.getvalue())

    def test_polling_403_is_retried_rather_than_treated_as_a_bad_token(
        self,
    ) -> None:
        client = FakeClient()
        calls = []

        def get_updates(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise telegram_service.TelegramAPIError(403)
            signal.raise_signal(signal.SIGTERM)
            return []

        client.get_updates = get_updates
        with self._service():
            with self.assertLogs("fitlit.telegram", "ERROR") as logs:
                self.assertEqual(0, telegram_service.run(client))
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("status 403", "\n".join(logs.output))

    def test_polling_409_keeps_its_backoff_and_detailed_log(self) -> None:
        client = FakeClient()
        calls = []

        def get_updates(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise telegram_service.TelegramAPIError(409, retry_after=3)
            signal.raise_signal(signal.SIGTERM)
            return []

        client.get_updates = get_updates
        with self._service():
            with self.assertLogs("fitlit.telegram", "ERROR") as logs:
                self.assertEqual(0, telegram_service.run(client))
        output = "\n".join(logs.output)
        self.assertIn("409", output)
        self.assertIn("stop other bot pollers", output)
        self.assertEqual(3, telegram_service._retry_delay(
            telegram_service.TelegramAPIError(409, retry_after=3),
            2,
        ))

    def test_startup_validates_provider_model_and_effort(self) -> None:
        client = FakeClient()
        with self._service():
            with patch("fitlit.telegram_service.shutil.which", return_value=None):
                with self.assertRaises(
                    telegram_service.TelegramConfigError
                ) as raised:
                    telegram_service.run(client)
            self.assertIn("not installed", str(raised.exception))
            with patch(
                "fitlit.config.TELEGRAM_COPILOT_MODEL",
                "not a valid model!",
            ):
                with self.assertRaises(
                    telegram_service.TelegramConfigError
                ) as raised:
                    telegram_service.run(client)
            self.assertIn("invalid format", str(raised.exception))
            with patch(
                "fitlit.config.TELEGRAM_REASONING_EFFORT",
                "extreme",
            ):
                with self.assertRaises(
                    telegram_service.TelegramConfigError
                ) as raised:
                    telegram_service.run(client)
            self.assertIn("effort", str(raised.exception))
            with patch("fitlit.config.HARNESS", "gemini"):
                with self.assertRaises(
                    telegram_service.TelegramConfigError
                ):
                    telegram_service.run(client)

    def test_validated_settings_match_the_provider_module_contract(
        self,
    ) -> None:
        with patch("fitlit.config.HARNESS", "copilot"):
            with patch(
                "fitlit.telegram_service.shutil.which",
                return_value="/usr/bin/copilot",
            ):
                self.assertTrue(telegram_service.provider_installed())
            with patch(
                "fitlit.telegram_service.shutil.which",
                return_value=None,
            ):
                self.assertFalse(telegram_service.provider_installed())
            with patch(
                "fitlit.config.TELEGRAM_COPILOT_MODEL",
                "gpt-5.6-terra",
            ):
                self.assertTrue(telegram_service.model_valid())
            with patch(
                "fitlit.config.TELEGRAM_COPILOT_MODEL",
                "--dangerous flag",
            ):
                self.assertFalse(telegram_service.model_valid())
        with (
            patch("fitlit.config.HARNESS", "opencode"),
            patch(
                "fitlit.config.TELEGRAM_OPENCODE_MODEL",
                "anthropic/claude-sonnet-4-5",
            ),
            patch(
                "fitlit.telegram_service.shutil.which",
                return_value="/usr/bin/opencode",
            ),
        ):
            self.assertTrue(telegram_service.provider_installed())
            self.assertTrue(telegram_service.model_valid())
            self.assertEqual(
                "anthropic/claude-sonnet-4-5",
                telegram_service._telegram_model(),
            )
        with patch("fitlit.config.TELEGRAM_REASONING_EFFORT", "high"):
            self.assertTrue(telegram_service.effort_valid())
        with patch("fitlit.config.TELEGRAM_REASONING_EFFORT", "highest"):
            self.assertFalse(telegram_service.effort_valid())

    def test_second_listener_refuses_to_poll(self) -> None:
        with self._service():
            with telegram_service.single_instance("the listener"):
                with self.assertRaises(telegram_service.TelegramLockedError):
                    telegram_service.run(FakeClient())

    def test_unexpected_failure_never_ends_the_listener(self) -> None:
        client = FakeClient()
        calls = []

        def get_updates(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("unexpected defect")
            signal.raise_signal(signal.SIGTERM)
            return []

        client.get_updates = get_updates
        with self._service():
            with self.assertLogs("fitlit.telegram", "ERROR"):
                self.assertEqual(0, telegram_service.run(client))
        self.assertGreaterEqual(len(calls), 2)

    def test_poison_update_does_not_block_the_poll_loop(self) -> None:
        client = FakeClient()
        batches = [
            [update("poison question", update_id=700)],
            [update("poison question", update_id=700)],
            [update("poison question", update_id=700)],
            [update("/new", update_id=701)],
        ]

        def get_updates(**kwargs):
            if batches:
                return batches.pop(0)
            signal.raise_signal(signal.SIGTERM)
            return []

        client.get_updates = get_updates
        with self._service() as root:
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=telegram_service.TelegramError("local defect"),
            ):
                self.assertEqual(0, telegram_service.run(client))
            state = json.loads((root / "state.json").read_text())
        self.assertEqual(701, state["last_update_id"])
        self.assertEqual("new-conversation", state["status"])
        self.assertIn("skipped it", client.texts[0][1])
        self.assertIn("Started conversation", client.texts[-1][1])


class TelegramQuarantineTests(unittest.TestCase):
    @contextmanager
    def _runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = telegram_service.TelegramState(root / "state.json")
            transcript = telegram_service.TelegramTranscriptStore(
                root / "transcript.sqlite3"
            )
            with patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID):
                yield state, transcript

    def test_poison_update_is_quarantined_and_later_updates_proceed(
        self,
    ) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            conversation = transcript.active(USER_ID)
            transcript.prepare_reply(
                conversation,
                user_id=USER_ID,
                source_update_id=100,
                assistant_text="staged",
                parts=[("text", b"staged", "", "text/plain")],
            )
            poison = update("How did I sleep?", update_id=100)
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=telegram_service.TelegramError(
                    "Telegram outbound reply part was invalid"
                ),
            ):
                for attempt in (1, 2):
                    with self.assertRaises(telegram_service.TelegramError):
                        telegram_service._process_with_quarantine(
                            client,
                            state,
                            transcript,
                            poison,
                        )
                    self.assertEqual(
                        attempt,
                        transcript.failure_count(100),
                    )
                    self.assertEqual(-1, state.value["last_update_id"])
                outcome = telegram_service._process_with_quarantine(
                    client,
                    state,
                    transcript,
                    poison,
                )
            self.assertEqual("quarantined", outcome)
            self.assertEqual("quarantined", state.value["status"])
            self.assertEqual(100, state.value["last_update_id"])
            self.assertEqual(101, state.offset)
            self.assertEqual(1, transcript.quarantined_count())
            self.assertIsNone(transcript.reply_plan(USER_ID, 100))
            self.assertEqual(1, len(client.texts))
            self.assertIn("skipped it", client.texts[0][1])

            following = telegram_service._process_with_quarantine(
                client,
                state,
                transcript,
                update("/new", update_id=101),
            )
            self.assertEqual("new-conversation", following)
            self.assertEqual(101, state.value["last_update_id"])
            self.assertIn(
                "Earlier conversations remain archived locally.",
                client.texts[-1][1],
            )

    def test_transient_failures_never_consume_the_poison_budget(self) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            poison = update("How did I sleep?", update_id=200)
            failures = (
                telegram_service.TelegramTransportError("unreachable"),
                telegram_service.TelegramAPIError(None),
                telegram_service.TelegramAPIError(429, retry_after=5),
                telegram_service.TelegramAPIError(503),
                telegram_service.TelegramNotDeliveredError("interrupted"),
                sqlite3.OperationalError("database is locked"),
                OSError("no space left on device"),
            )
            for failure in failures:
                with patch(
                    "fitlit.telegram_service._grounded_reply",
                    side_effect=failure,
                ):
                    with self.assertRaises(type(failure)):
                        telegram_service._process_with_quarantine(
                            client,
                            state,
                            transcript,
                            poison,
                        )
                self.assertEqual(0, transcript.failure_count(200))
            self.assertEqual(0, transcript.quarantined_count())
            self.assertEqual(-1, state.value["last_update_id"])
            self.assertEqual([], client.texts)

    def test_unexpected_local_error_counts_and_stays_a_telegram_error(
        self,
    ) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            poison = update("How did I sleep?", update_id=300)
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=ValueError("deterministic local defect"),
            ):
                with self.assertRaises(telegram_service.TelegramError):
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        poison,
                    )
            self.assertEqual(1, transcript.failure_count(300))

    def test_recovered_update_clears_its_recorded_failures(self) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            message = update("How did I sleep?", update_id=400)
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=telegram_service.TelegramError("local defect"),
            ):
                with self.assertRaises(telegram_service.TelegramError):
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        message,
                    )
            self.assertEqual(1, transcript.failure_count(400))
            with patch("fitlit.telegram_service._grounded_reply"):
                self.assertEqual(
                    "replied",
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        message,
                    ),
                )
            self.assertEqual(0, transcript.failure_count(400))
            self.assertEqual(0, transcript.quarantined_count())

    def test_quarantine_completes_even_if_the_owner_notice_fails(
        self,
    ) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            client.send_text = lambda *a, **k: (_ for _ in ()).throw(
                telegram_service.TelegramTransportError("unreachable")
            )
            poison = update("How did I sleep?", update_id=550)
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=telegram_service.TelegramError("local defect"),
            ):
                for _ in range(2):
                    with self.assertRaises(telegram_service.TelegramError):
                        telegram_service._process_with_quarantine(
                            client,
                            state,
                            transcript,
                            poison,
                        )
                self.assertEqual(
                    "quarantined",
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        poison,
                    ),
                )
            self.assertEqual(550, state.value["last_update_id"])
            self.assertEqual(1, transcript.quarantined_count())

    def test_quarantine_records_no_message_content_or_identity(self) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            secret = "my private symptom question"
            poison = update(secret, update_id=500)
            with patch(
                "fitlit.telegram_service._grounded_reply",
                side_effect=telegram_service.TelegramAPIError(400),
            ):
                with self.assertLogs("fitlit.telegram", "ERROR") as logs:
                    for _ in range(2):
                        with self.assertRaises(
                            telegram_service.TelegramAPIError
                        ):
                            telegram_service._process_with_quarantine(
                                client,
                                state,
                                transcript,
                                poison,
                            )
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        poison,
                    )
            with transcript._connect() as connection:
                stored = connection.execute(
                    """
                    SELECT attempts, last_error, first_seen_ms
                    FROM update_failures WHERE update_id = ?
                    """,
                    (500,),
                ).fetchone()
            self.assertEqual(3, stored[0])
            self.assertEqual("TelegramAPIError:400", stored[1])
            self.assertGreater(stored[2], 0)
            output = "\n".join(logs.output)
            self.assertIn("500", output)
            self.assertNotIn(secret, output)
            self.assertNotIn(str(USER_ID), output)

    def test_successful_update_logs_outcome_and_elapsed_time_only(
        self,
    ) -> None:
        with self._runtime() as (state, transcript):
            client = FakeClient()
            secret = "How did I sleep last night?"
            with self.assertLogs("fitlit.telegram", "INFO") as logs:
                with patch("fitlit.telegram_service._grounded_reply"):
                    telegram_service._process_with_quarantine(
                        client,
                        state,
                        transcript,
                        update(secret, update_id=600),
                    )
            output = "\n".join(logs.output)
            self.assertIn("600", output)
            self.assertIn("replied", output)
            self.assertIn("seconds", output)
            self.assertNotIn(secret, output)
            self.assertNotIn(str(USER_ID), output)


class TelegramPairingTests(unittest.TestCase):
    def test_pair_uses_one_time_code_and_updates_private_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / ".env"
            env.write_text(f"FITLIT_TELEGRAM_BOT_TOKEN={TOKEN}\n")
            env.chmod(0o600)
            state_path = root / "state" / "telegram-state.json"
            telegram_service.TelegramState(state_path).finish(5000, "replied")
            client = FakeClient()
            client.updates = [update("/pair fixed-code", update_id=5001)]
            with (
                patch("fitlit.config.TELEGRAM_ENABLED", False),
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", None),
                patch("fitlit.config.BASE_DIR", root),
                patch(
                    "fitlit.config.TELEGRAM_STATE_PATH",
                    state_path,
                ),
                patch(
                    "fitlit.config.TELEGRAM_LOCK_PATH",
                    root / "state" / "telegram-service.lock",
                ),
                patch(
                    "fitlit.telegram_service.secrets.token_urlsafe",
                    return_value="fixed-code",
                ),
                patch("sys.stdout", new=io.StringIO()),
            ):
                self.assertEqual(0, telegram_service.pair(client))
            content = env.read_text()
            self.assertIn(
                f"FITLIT_TELEGRAM_TRUSTED_USER_ID={USER_ID}",
                content,
            )
            self.assertIn("FITLIT_TELEGRAM_ENABLED=true", content)
            self.assertEqual(0o600, env.stat().st_mode & 0o777)
            self.assertTrue(client.drop_pending)
            self.assertEqual(5001, json.loads(state_path.read_text())[
                "last_update_id"
            ])

    def test_pairing_refuses_while_the_listener_holds_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "state" / "telegram-service.lock"
            with (
                patch("fitlit.config.TELEGRAM_ENABLED", False),
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", None),
                patch("fitlit.config.TELEGRAM_LOCK_PATH", lock_path),
            ):
                with telegram_service.single_instance("the listener"):
                    self.assertTrue(telegram_service.listener_running())
                    with self.assertRaises(
                        telegram_service.TelegramLockedError
                    ):
                        telegram_service.pair(FakeClient())
                self.assertFalse(telegram_service.listener_running())
                self.assertEqual(
                    0o600,
                    lock_path.stat().st_mode & 0o777,
                )

    def test_pairing_handles_non_ascii_messages_before_the_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / ".env"
            env.write_text(f"FITLIT_TELEGRAM_BOT_TOKEN={TOKEN}\n")
            env.chmod(0o600)
            client = FakeClient()
            client.updates = [
                update("How did I sleep? 😴", update_id=20),
                update("/pair fixed-code", update_id=21),
            ]
            with (
                patch("fitlit.config.TELEGRAM_ENABLED", False),
                patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", None),
                patch("fitlit.config.BASE_DIR", root),
                patch(
                    "fitlit.config.TELEGRAM_STATE_PATH",
                    root / "state" / "telegram-state.json",
                ),
                patch(
                    "fitlit.config.TELEGRAM_LOCK_PATH",
                    root / "state" / "telegram-service.lock",
                ),
                patch(
                    "fitlit.telegram_service.secrets.token_urlsafe",
                    return_value="fixed-code",
                ),
                patch("sys.stdout", new=io.StringIO()),
            ):
                self.assertEqual(0, telegram_service.pair(client))

    def test_pairing_refuses_when_already_configured(self) -> None:
        with (
            patch("fitlit.config.TELEGRAM_ENABLED", True),
            patch("fitlit.config.TELEGRAM_TRUSTED_USER_ID", USER_ID),
        ):
            with self.assertRaises(telegram_service.TelegramConfigError):
                telegram_service.pair(FakeClient())

    def test_single_instance_lock_is_released_when_a_holder_crashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "state" / "telegram-service.lock"
            source = (
                "import sys, os, pathlib\n"
                "sys.path.insert(0, os.getcwd())\n"
                "from unittest.mock import patch\n"
                "from fitlit import telegram_service\n"
                f"path = pathlib.Path({str(lock_path)!r})\n"
                "with patch('fitlit.config.TELEGRAM_LOCK_PATH', path):\n"
                "    with telegram_service.single_instance('the listener'):\n"
                "        os.kill(os.getpid(), 9)\n"
            )
            crashed = subprocess.run(
                [sys.executable, "-c", source],
                cwd=str(Path(telegram_service.__file__).resolve().parent.parent),
                capture_output=True,
            )
            self.assertNotEqual(0, crashed.returncode)
            with patch("fitlit.config.TELEGRAM_LOCK_PATH", lock_path):
                self.assertFalse(telegram_service.listener_running())
                with telegram_service.single_instance("the listener"):
                    pass


if __name__ == "__main__":
    unittest.main()
