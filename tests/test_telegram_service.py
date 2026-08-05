from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fitlit import email_agent, telegram_service
from fitlit.gmail_client import EmailAttachment

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
        self.assertTrue(all(1 <= len(item) <= 80 for item in chunks))


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


class TelegramClientTests(unittest.TestCase):
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
                ],
                client.documents,
            )
            self.assertEqual(
                [(
                    USER_ID,
                    "fitlit-snapshot.png",
                    b"\x89PNG\r\n\x1a\nprivate image",
                )],
                client.photos,
            )
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
            self.assertEqual([], retry.texts)
            history = store.history(conversation)
            self.assertIn("did not resend", history[-1].content)

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
            self.assertEqual([], client.texts)
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
            self.assertEqual([], retry.texts)
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
            client.updates = [update("/pair fixed-code")]
            with (
                patch("fitlit.config.BASE_DIR", root),
                patch(
                    "fitlit.config.TELEGRAM_STATE_PATH",
                    state_path,
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
            self.assertEqual(10, json.loads(state_path.read_text())[
                "last_update_id"
            ])


if __name__ == "__main__":
    unittest.main()
