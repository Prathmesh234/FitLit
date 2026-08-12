"""Private Telegram Bot API channel for FitLit's grounded agent."""
from __future__ import annotations

import argparse
import fcntl
import io
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from fitlit import config, email_agent
from fitlit.journal import PACIFIC

LOG = logging.getLogger("fitlit.telegram")
EX_CONFIG = 78
TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
METHOD_PATTERN = re.compile(r"^[A-Za-z]+$")
FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
DOCUMENT_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/html": ".html",
    # PNG evidence is uploaded with document semantics: Telegram compresses and
    # dimension-checks photos, while documents keep the exact rendered bytes.
    "image/png": ".png",
}
# Retained only so an outbound part staged as a photo before this change still
# has a delivery path; new plans always stage PNG artifacts as documents.
PHOTO_MIME_TYPES = {"image/png": ".png"}
TYPING_REFRESH_SECONDS = 4.0
# Telegram assigns the next update_id randomly instead of sequentially once a
# bot has had no updates for at least a week.
UPDATE_ID_RESET_IDLE_SECONDS = 7 * 24 * 60 * 60
NOT_DELIVERED_PREFIX = "[NOT DELIVERED] "
UNCERTAIN_ANNOTATION = (
    "[Telegram delivery could not be confirmed; FitLit did not resend the "
    "uncertain part.]"
)
FAILED_ANNOTATION = "[One or more Telegram delivery parts were rejected.]"
EVIDENCE_APPENDIX_MARKERS = (
    "Ground truth (Fitbit)",
    "FitLit selected the following grounded health data",
)
TOO_LARGE_REPLY = (
    "This complete conversation is too large for the headless provider. "
    "Use /new to begin another thread; the current conversation remains "
    "archived."
)
PROVIDER_FAILURE_REPLY = (
    "FitLit's headless provider could not complete that reply. "
    "Your message and conversation history are preserved; "
    "please try the question again."
)
# Operational fallbacks are real user-visible messages, but they are FitLit
# runtime notices rather than model output, so they are withheld from later
# provider context without ever being deleted from the transcript.
PROVIDER_FALLBACK_REPLIES = frozenset({
    TOO_LARGE_REPLY,
    PROVIDER_FAILURE_REPLY,
})
QUARANTINE_NOTICE = (
    "FitLit could not process one earlier message after repeated attempts "
    "and has skipped it to keep the channel moving. Please send that "
    "question again if it still matters."
)


class TelegramError(RuntimeError):
    """Telegram transport, response, or configuration failure."""


class TelegramConfigError(TelegramError):
    """Telegram settings are missing or invalid."""


class TelegramTransportError(TelegramError):
    """Telegram could not be reached, timed out, or dropped the connection."""


class TelegramLockedError(TelegramError):
    """Another FitLit Telegram process holds the single-instance lock."""


class TelegramAPIError(TelegramError):
    """Telegram returned a valid API error response."""

    def __init__(
        self,
        error_code: int | None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__("Telegram Bot API rejected the request")
        self.error_code = error_code
        self.retry_after = retry_after


class TelegramNotDeliveredError(TelegramError):
    """Telegram explicitly confirmed that an interrupted part was not sent."""


def _blocking_retry_sleep(delay: float) -> bool:
    time.sleep(delay)
    return False


def _retryable_api_error(error: TelegramAPIError) -> bool:
    """Telegram may still accept this call later; the outcome is not final."""
    code = error.error_code
    return code is None or code == 429 or code >= 500


@dataclass(frozen=True)
class TelegramInbound:
    update_id: int
    message_id: int
    user_id: int
    chat_id: int
    sent_at_ms: int
    text: str | None


@dataclass(frozen=True)
class TelegramConversation:
    conversation_id: int
    system_instructions: tuple[str, ...]


@dataclass(frozen=True)
class TelegramOutboundPart:
    part_index: int
    kind: str
    payload: bytes
    filename: str
    mime_type: str
    status: str


@dataclass(frozen=True)
class TelegramReplyPlan:
    user_id: int
    source_update_id: int
    conversation_id: int
    assistant_text: str
    record_in_transcript: bool
    parts: tuple[TelegramOutboundPart, ...]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_inbound(update: Any) -> TelegramInbound | None:
    if not isinstance(update, dict):
        return None
    update_id = _nonnegative_int(update.get("update_id"))
    message = update.get("message")
    if update_id is None or not isinstance(message, dict):
        return None
    message_id = _positive_int(message.get("message_id"))
    user = message.get("from")
    chat = message.get("chat")
    sent_at = _positive_int(message.get("date"))
    if (
        message_id is None
        or not isinstance(user, dict)
        or not isinstance(chat, dict)
        or user.get("is_bot") is True
        or chat.get("type") != "private"
        or sent_at is None
    ):
        return None
    user_id = _positive_int(user.get("id"))
    chat_id = _positive_int(chat.get("id"))
    if user_id is None or chat_id != user_id:
        return None
    text = message.get("text")
    return TelegramInbound(
        update_id=update_id,
        message_id=message_id,
        user_id=user_id,
        chat_id=chat_id,
        sent_at_ms=sent_at * 1000,
        text=text.strip() if isinstance(text, str) and text.strip() else None,
    )


def _update_id(update: Any) -> int:
    value = update.get("update_id") if isinstance(update, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TelegramError("Telegram update had an invalid identifier")
    return value


def _command(text: str) -> str | None:
    first = text.split(maxsplit=1)[0]
    if not first.startswith("/"):
        return None
    return first.split("@", 1)[0].lower()


def model_visible_assistant_text(content: str) -> str | None:
    """Strip runtime annotations, or return None for a FitLit-only notice.

    The compact ``[NOT DELIVERED]`` marker is deliberately kept: it is the one
    delivery fact the model must know, because the owner never saw that reply.
    Verbose runtime appendices are removed, and operational fallbacks that
    FitLit itself wrote are withheld entirely.
    """
    text = content
    undelivered = text.startswith(NOT_DELIVERED_PREFIX)
    if undelivered:
        text = text[len(NOT_DELIVERED_PREFIX):]
    for marker in (
        *EVIDENCE_APPENDIX_MARKERS,
        UNCERTAIN_ANNOTATION,
        FAILED_ANNOTATION,
    ):
        index = text.find(marker)
        if index != -1:
            text = text[:index]
    text = text.strip()
    if not text or text in PROVIDER_FALLBACK_REPLIES:
        return None
    return NOT_DELIVERED_PREFIX + text if undelivered else text


def utf16_units(text: str) -> int:
    """Return Telegram's own length unit: UTF-16 code units."""
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _utf16_prefix(text: str, budget: int) -> int:
    """Return the longest character count whose UTF-16 length fits budget."""
    used = 0
    for index, character in enumerate(text):
        width = 2 if ord(character) > 0xFFFF else 1
        if used + width > budget:
            return index
        used += width
    return len(text)


def split_text(text: str, maximum: int) -> list[str]:
    """Split text into chunks of at most ``maximum`` UTF-16 code units.

    Telegram measures the 4096 sendMessage limit in UTF-16 code units, so an
    astral emoji costs two units even though Python counts it as one
    character. Paragraph, line, and word boundaries are preserved when a
    reasonable one exists inside the budget.
    """
    budget = int(maximum)
    if budget < 2:
        raise TelegramError("Telegram chunk budget was too small")
    remaining = text.strip()
    if not remaining:
        raise TelegramError("Telegram reply text was empty")
    chunks: list[str] = []
    while utf16_units(remaining) > budget:
        cut = _utf16_prefix(remaining, budget)
        if cut <= 0:
            raise TelegramError("Telegram chunk budget was too small")
        window = remaining[:cut]
        split_at = cut
        for boundary in ("\n\n", "\n", " "):
            index = window.rfind(boundary)
            if (
                index > 0
                and utf16_units(window[:index].rstrip()) >= budget // 2
            ):
                split_at = index
                break
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            split_at = cut
            chunk = window
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    if not chunks:
        raise TelegramError("Telegram reply text was empty")
    return chunks


def _private_parent(path: Path) -> None:
    parent = path.parent
    if parent.exists() and parent.is_symlink():
        raise TelegramError("refusing symlinked Telegram state directory")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent.is_dir():
        raise TelegramError("Telegram state parent is not a directory")
    parent.chmod(0o700)


def _open_lock(path: Path) -> int:
    if path.exists() and path.is_symlink():
        raise TelegramError("refusing symlinked Telegram lock file")
    _private_parent(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def single_instance(purpose: str) -> Iterator[None]:
    """Hold the exclusive Telegram lock, or refuse to start a second process.

    The lock is a kernel ``flock``: it is released automatically when this
    process exits or is killed, so a crash can never leave it stuck.
    """
    path = config.TELEGRAM_LOCK_PATH
    descriptor = _open_lock(path)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise TelegramLockedError(
                "another FitLit Telegram process is already running; stop "
                f"fitlit-telegram.service before running {purpose}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def listener_running() -> bool:
    """Probe the single-instance lock without disturbing a live listener."""
    path = config.TELEGRAM_LOCK_PATH
    if not path.is_file():
        return False
    try:
        descriptor = _open_lock(path)
    except (OSError, TelegramError):
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


class TelegramState:
    """Atomic metadata-only update offset."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.TELEGRAM_STATE_PATH
        _private_parent(self.path)
        if self.path.exists() and self.path.is_symlink():
            raise TelegramError("refusing symlinked Telegram state file")
        self.value = {
            "version": 1,
            "last_update_id": -1,
            "status": "new",
            "updated_at": 0,
        }
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise TelegramError("Telegram state is malformed") from exc
            if (
                not isinstance(value, dict)
                or set(value) != set(self.value)
                or value.get("version") != 1
                or not isinstance(value.get("last_update_id"), int)
                or not isinstance(value.get("status"), str)
                or not isinstance(value.get("updated_at"), int)
            ):
                raise TelegramError("Telegram state had an invalid shape")
            self.value = value
            self.path.chmod(0o600)

    @property
    def offset(self) -> int | None:
        value = self.value["last_update_id"]
        if value < 0 or self.idle_seconds() >= UPDATE_ID_RESET_IDLE_SECONDS:
            # Telegram may hand out a randomly chosen, possibly lower
            # update_id after a week of silence. Confirming nothing lets the
            # server replay whatever is genuinely pending; every processing
            # path is idempotent, so a replayed update is harmless.
            return None
        return value + 1

    def idle_seconds(self) -> int:
        updated_at = self.value["updated_at"]
        if updated_at <= 0:
            return UPDATE_ID_RESET_IDLE_SECONDS
        return max(0, int(time.time()) - int(updated_at))

    def finish(self, update_id: int, status: str) -> None:
        if (
            update_id < self.value["last_update_id"]
            and self.idle_seconds() < UPDATE_ID_RESET_IDLE_SECONDS
        ):
            # A recent lower identifier is a stale replay, never a reset.
            return
        self.value = {
            "version": 1,
            "last_update_id": update_id,
            "status": status,
            "updated_at": int(time.time()),
        }
        self._persist()

    def _persist(self) -> None:
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.value, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()


class TelegramTranscriptStore:
    """Owner-only indexed conversations that are archived, never deleted."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.TELEGRAM_TRANSCRIPT_PATH
        _private_parent(self.path)
        if self.path.exists() and self.path.is_symlink():
            raise TelegramError("refusing symlinked Telegram transcript database")
        with self._connect() as connection:
            memory_index_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'turns_fts'
                """
            ).fetchone() is not None
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    system_instructions TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    created_at_ms INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    one_active_telegram_conversation
                    ON conversations(user_id) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id),
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    provider_content TEXT,
                    sent_at_ms INTEGER NOT NULL,
                    source_update_id INTEGER NOT NULL,
                    UNIQUE(conversation_id, role, source_update_id)
                );
                CREATE TABLE IF NOT EXISTS new_conversation_updates (
                    user_id INTEGER NOT NULL,
                    source_update_id INTEGER NOT NULL,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id),
                    PRIMARY KEY(user_id, source_update_id)
                );
                CREATE TABLE IF NOT EXISTS outbound_replies (
                    user_id INTEGER NOT NULL,
                    source_update_id INTEGER NOT NULL,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id),
                    assistant_text TEXT NOT NULL,
                    record_in_transcript INTEGER NOT NULL DEFAULT 1
                        CHECK (record_in_transcript IN (0, 1)),
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(user_id, source_update_id)
                );
                CREATE TABLE IF NOT EXISTS outbound_parts (
                    user_id INTEGER NOT NULL,
                    source_update_id INTEGER NOT NULL,
                    part_index INTEGER NOT NULL,
                    kind TEXT NOT NULL
                        CHECK (kind IN ('text', 'document', 'photo')),
                    payload BLOB NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (
                            status IN (
                                'pending', 'sending', 'delivered',
                                'failed', 'uncertain'
                            )
                        ),
                    telegram_message_id INTEGER,
                    PRIMARY KEY(user_id, source_update_id, part_index),
                    FOREIGN KEY(user_id, source_update_id)
                        REFERENCES outbound_replies(user_id, source_update_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS completed_fixed_replies (
                    user_id INTEGER NOT NULL,
                    source_update_id INTEGER NOT NULL,
                    completed_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(user_id, source_update_id)
                );
                CREATE TABLE IF NOT EXISTS update_failures (
                    update_id INTEGER PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    first_seen_ms INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
                    content,
                    content = 'turns',
                    content_rowid = 'id',
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS turns_fts_insert
                AFTER INSERT ON turns BEGIN
                    INSERT INTO turns_fts(rowid, content)
                    VALUES (new.id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS turns_fts_delete
                AFTER DELETE ON turns BEGIN
                    INSERT INTO turns_fts(turns_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS turns_fts_update
                AFTER UPDATE OF content ON turns BEGIN
                    INSERT INTO turns_fts(turns_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                    INSERT INTO turns_fts(rowid, content)
                    VALUES (new.id, new.content);
                END;
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(outbound_replies)"
                ).fetchall()
            }
            if "record_in_transcript" not in columns:
                connection.execute(
                    """
                    ALTER TABLE outbound_replies
                    ADD COLUMN record_in_transcript INTEGER NOT NULL DEFAULT 1
                    """
                )
            turn_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(turns)"
                ).fetchall()
            }
            if "provider_content" not in turn_columns:
                connection.execute(
                    "ALTER TABLE turns ADD COLUMN provider_content TEXT"
                )
            if not memory_index_exists:
                connection.execute(
                    "INSERT INTO turns_fts(turns_fts) VALUES ('rebuild')"
                )
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _decode_conversation(row: tuple[Any, ...]) -> TelegramConversation:
        conversation_id, encoded = row
        try:
            values = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TelegramError(
                "Telegram conversation system instructions were malformed"
            ) from exc
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
        ):
            raise TelegramError(
                "Telegram conversation system instructions were invalid"
            )
        return TelegramConversation(conversation_id, tuple(values))

    @staticmethod
    def _new_system_instructions() -> str:
        return json.dumps(
            email_agent.system_instructions("telegram"),
            separators=(",", ":"),
        )

    def active(self, user_id: int) -> TelegramConversation:
        current_instructions = self._new_system_instructions()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, system_instructions
                FROM conversations
                WHERE user_id = ? AND active = 1
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO conversations (
                        user_id, system_instructions, active, created_at_ms
                    ) VALUES (?, ?, 1, ?)
                    """,
                    (
                        user_id,
                        current_instructions,
                        int(time.time() * 1000),
                    ),
                )
                row = (cursor.lastrowid, current_instructions)
            elif row[1] != current_instructions:
                connection.execute(
                    """
                    UPDATE conversations
                    SET system_instructions = ?
                    WHERE id = ? AND user_id = ? AND active = 1
                    """,
                    (current_instructions, row[0], user_id),
                )
                row = (row[0], current_instructions)
        return self._decode_conversation(row)

    def start_new(
        self,
        user_id: int,
        source_update_id: int,
    ) -> TelegramConversation:
        instructions = self._new_system_instructions()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT conversations.id, conversations.system_instructions
                FROM new_conversation_updates
                JOIN conversations
                    ON conversations.id =
                        new_conversation_updates.conversation_id
                WHERE new_conversation_updates.user_id = ?
                    AND new_conversation_updates.source_update_id = ?
                """,
                (user_id, source_update_id),
            ).fetchone()
            if existing is not None:
                return self._decode_conversation(existing)
            connection.execute(
                "UPDATE conversations SET active = 0 WHERE user_id = ?",
                (user_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO conversations (
                    user_id, system_instructions, active, created_at_ms
                ) VALUES (?, ?, 1, ?)
                """,
                (user_id, instructions, int(time.time() * 1000)),
            )
            connection.execute(
                """
                INSERT INTO new_conversation_updates (
                    user_id, source_update_id, conversation_id
                ) VALUES (?, ?, ?)
                """,
                (user_id, source_update_id, cursor.lastrowid),
            )
            row = (cursor.lastrowid, instructions)
        return self._decode_conversation(row)

    def append(
        self,
        conversation: TelegramConversation,
        *,
        user_id: int,
        role: str,
        content: str,
        provider_content: str | None = None,
        sent_at_ms: int,
        source_update_id: int,
    ) -> None:
        if role not in {"user", "assistant"} or not content:
            raise TelegramError("Telegram transcript turn was invalid")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO turns (
                    conversation_id, role, content, provider_content,
                    sent_at_ms, source_update_id
                )
                SELECT id, ?, ?, ?, ?, ?
                FROM conversations
                WHERE id = ? AND user_id = ?
                """,
                (
                    role,
                    content,
                    provider_content,
                    sent_at_ms,
                    source_update_id,
                    conversation.conversation_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT 1 FROM turns
                    WHERE conversation_id = ?
                        AND role = ?
                        AND source_update_id = ?
                    """,
                    (
                        conversation.conversation_id,
                        role,
                        source_update_id,
                    ),
                ).fetchone()
                if existing is None:
                    raise TelegramError(
                        "Telegram transcript conversation was unavailable"
                    )
            if cursor.rowcount not in {0, 1}:
                raise TelegramError("Telegram transcript write failed")

    def has_turn(
        self,
        conversation: TelegramConversation,
        *,
        role: str,
        source_update_id: int,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM turns
                WHERE conversation_id = ? AND role = ? AND source_update_id = ?
                """,
                (conversation.conversation_id, role, source_update_id),
            ).fetchone()
        return row is not None

    def history(
        self,
        conversation: TelegramConversation,
    ) -> list[email_agent.ThreadTurn]:
        """Return the model-visible view of a stored conversation.

        The database keeps every byte forever. Operational FitLit notices and
        runtime delivery/evidence annotations are withheld here so a later
        provider turn is not conditioned on text the model never wrote.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, provider_content, sent_at_ms
                FROM turns
                WHERE conversation_id = ?
                ORDER BY id
                """,
                (conversation.conversation_id,),
            ).fetchall()
        turns = []
        for role, content, provider_content, sent_at_ms in rows:
            if role == "assistant":
                visible = model_visible_assistant_text(
                    provider_content or content
                )
                if visible is None:
                    continue
            else:
                visible = content
            turns.append(
                email_agent.ThreadTurn(
                    role=role,
                    content=visible,
                    internal_date_ms=sent_at_ms,
                )
            )
        return turns

    def stats(self, user_id: int) -> tuple[int, int]:
        with self._connect() as connection:
            conversations = connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            turns = connection.execute(
                """
                SELECT COUNT(*)
                FROM turns
                WHERE conversation_id IN (
                    SELECT id FROM conversations WHERE user_id = ?
                )
                """,
                (user_id,),
            ).fetchone()[0]
        return int(conversations), int(turns)

    def reply_plan(
        self,
        user_id: int,
        source_update_id: int,
    ) -> TelegramReplyPlan | None:
        with self._connect() as connection:
            reply = connection.execute(
                """
                SELECT conversation_id, assistant_text, record_in_transcript
                FROM outbound_replies
                WHERE user_id = ? AND source_update_id = ?
                """,
                (user_id, source_update_id),
            ).fetchone()
            if reply is None:
                return None
            rows = connection.execute(
                """
                SELECT part_index, kind, payload, filename, mime_type, status
                FROM outbound_parts
                WHERE user_id = ? AND source_update_id = ?
                ORDER BY part_index
                """,
                (user_id, source_update_id),
            ).fetchall()
        if not rows:
            raise TelegramError("Telegram outbound reply had no delivery parts")
        return TelegramReplyPlan(
            user_id=user_id,
            source_update_id=source_update_id,
            conversation_id=int(reply[0]),
            assistant_text=str(reply[1]),
            record_in_transcript=bool(reply[2]),
            parts=tuple(
                TelegramOutboundPart(
                    part_index=int(row[0]),
                    kind=str(row[1]),
                    payload=bytes(row[2]),
                    filename=str(row[3]),
                    mime_type=str(row[4]),
                    status=str(row[5]),
                )
                for row in rows
            ),
        )

    def prepare_reply(
        self,
        conversation: TelegramConversation,
        *,
        user_id: int,
        source_update_id: int,
        assistant_text: str,
        parts: list[tuple[str, bytes, str, str]],
        record_in_transcript: bool = True,
    ) -> TelegramReplyPlan:
        if not assistant_text or not parts:
            raise TelegramError("Telegram outbound reply was empty")
        if any(
            kind not in {"text", "document", "photo"}
            or not payload
            or kind == "text" and (filename or mime_type != "text/plain")
            or kind != "text" and (not filename or not mime_type)
            for kind, payload, filename, mime_type in parts
        ):
            raise TelegramError("Telegram outbound reply part was invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM outbound_replies
                WHERE user_id = ? AND source_update_id = ?
                """,
                (user_id, source_update_id),
            ).fetchone()
            if existing is None:
                owner = connection.execute(
                    """
                    SELECT 1 FROM conversations
                    WHERE id = ? AND user_id = ?
                    """,
                    (conversation.conversation_id, user_id),
                ).fetchone()
                if owner is None:
                    raise TelegramError(
                        "Telegram outbound conversation was unavailable"
                    )
                connection.execute(
                    """
                    INSERT INTO outbound_replies (
                        user_id, source_update_id, conversation_id,
                        assistant_text, record_in_transcript, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        source_update_id,
                        conversation.conversation_id,
                        assistant_text,
                        int(record_in_transcript),
                        int(time.time() * 1000),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO outbound_parts (
                        user_id, source_update_id, part_index, kind,
                        payload, filename, mime_type, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    [
                        (
                            user_id,
                            source_update_id,
                            index,
                            kind,
                            payload,
                            filename,
                            mime_type,
                        )
                        for index, (kind, payload, filename, mime_type)
                        in enumerate(parts)
                    ],
                )
        plan = self.reply_plan(user_id, source_update_id)
        if plan is None:
            raise TelegramError("Telegram outbound reply was not persisted")
        return plan

    def set_part_status(
        self,
        plan: TelegramReplyPlan,
        part_index: int,
        status: str,
        *,
        telegram_message_id: int | None = None,
    ) -> None:
        if status not in {
            "pending",
            "sending",
            "delivered",
            "failed",
            "uncertain",
        }:
            raise TelegramError("Telegram delivery status was invalid")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE outbound_parts
                SET status = ?, telegram_message_id = ?
                WHERE user_id = ? AND source_update_id = ? AND part_index = ?
                """,
                (
                    status,
                    telegram_message_id,
                    plan.user_id,
                    plan.source_update_id,
                    part_index,
                ),
            )
            if cursor.rowcount != 1:
                raise TelegramError("Telegram delivery part was unavailable")

    def fail_remaining(self, plan: TelegramReplyPlan) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbound_parts
                SET status = 'failed', telegram_message_id = NULL
                WHERE user_id = ? AND source_update_id = ?
                    AND status = 'pending'
                """,
                (plan.user_id, plan.source_update_id),
            )

    def complete_reply(
        self,
        plan: TelegramReplyPlan,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parts = connection.execute(
                """
                SELECT status, kind, payload
                FROM outbound_parts
                WHERE user_id = ? AND source_update_id = ?
                ORDER BY part_index
                """,
                (plan.user_id, plan.source_update_id),
            ).fetchall()
            statuses = [str(row[0]) for row in parts]
            if not statuses:
                return
            if any(status in {"pending", "sending"} for status in statuses):
                raise TelegramError("Telegram outbound reply was incomplete")
            content = plan.assistant_text
            if "uncertain" in statuses:
                content += f"\n\n{UNCERTAIN_ANNOTATION}"
            elif "failed" in statuses:
                content += f"\n\n{FAILED_ANNOTATION}"
            if "delivered" not in statuses:
                # Nothing reached Telegram, so later model context must not
                # assume the owner ever saw this reply.
                content = NOT_DELIVERED_PREFIX + content
            text_parts = [row for row in parts if str(row[1]) == "text"]
            delivered_text = [
                bytes(row[2]).decode("utf-8")
                for row in text_parts
                if str(row[0]) == "delivered"
            ]
            if text_parts and len(delivered_text) == len(text_parts):
                provider_content = plan.assistant_text
            elif delivered_text:
                provider_content = (
                    "\n".join(delivered_text)
                    + "\n\n[Only part of the previous reply was delivered.]"
                )
            else:
                provider_content = (
                    "[The previous reply was not delivered to the user.]"
                )
            if plan.record_in_transcript:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO turns (
                        conversation_id, role, content, provider_content,
                        sent_at_ms, source_update_id
                    )
                    SELECT id, 'assistant', ?, ?, ?, ?
                    FROM conversations
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        content,
                        provider_content,
                        int(time.time() * 1000),
                        plan.source_update_id,
                        plan.conversation_id,
                        plan.user_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO completed_fixed_replies (
                        user_id, source_update_id, completed_at_ms
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        plan.user_id,
                        plan.source_update_id,
                        int(time.time() * 1000),
                    ),
                )
            connection.execute(
                """
                DELETE FROM outbound_replies
                WHERE user_id = ? AND source_update_id = ?
                """,
                (plan.user_id, plan.source_update_id),
            )

    def discard_reply(self, user_id: int, source_update_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM outbound_replies
                WHERE user_id = ? AND source_update_id = ?
                """,
                (user_id, source_update_id),
            )

    def fixed_reply_completed(
        self,
        user_id: int,
        source_update_id: int,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM completed_fixed_replies
                WHERE user_id = ? AND source_update_id = ?
                """,
                (user_id, source_update_id),
            ).fetchone()
        return row is not None

    def record_failure(self, update_id: int, last_error: str) -> int:
        """Count one deterministic local failure and return the new total."""
        sanitized = last_error[:120]
        now_ms = int(time.time() * 1000)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO update_failures (
                    update_id, attempts, last_error, first_seen_ms
                ) VALUES (?, 1, ?, ?)
                ON CONFLICT(update_id) DO UPDATE SET
                    attempts = attempts + 1,
                    last_error = excluded.last_error
                """,
                (update_id, sanitized, now_ms),
            )
            row = connection.execute(
                "SELECT attempts FROM update_failures WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def clear_failure(self, update_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM update_failures WHERE update_id = ?",
                (update_id,),
            )

    def failure_count(self, update_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM update_failures WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def quarantined_count(
        self,
        attempts: int = config.TELEGRAM_POISON_ATTEMPTS,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM update_failures WHERE attempts >= ?",
                (attempts,),
            ).fetchone()
        return int(row[0]) if row else 0

    def pending_parts(self, user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM outbound_parts
                WHERE user_id = ?
                    AND status IN ('pending', 'sending', 'uncertain')
                """,
                (user_id,),
            ).fetchone()
        return int(row[0]) if row else 0


class TelegramClient:
    """Small official Bot API client that never logs token-bearing URLs."""

    def __init__(
        self,
        token: str,
        *,
        request_timeout: int = config.TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = config.TELEGRAM_MAX_RESPONSE_BYTES,
        retry_sleeper: Callable[[float], bool] = _blocking_retry_sleep,
    ) -> None:
        if not TOKEN_PATTERN.fullmatch(token):
            raise TelegramConfigError(
                "FITLIT_TELEGRAM_BOT_TOKEN has an invalid format"
            )
        self._base = f"https://api.telegram.org/bot{token}/"
        self.request_timeout = request_timeout
        self.max_response_bytes = max_response_bytes
        self._retry_sleeper = retry_sleeper
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def set_retry_sleeper(
        self,
        retry_sleeper: Callable[[float], bool],
    ) -> None:
        self._retry_sleeper = retry_sleeper

    def _open(self, request: urllib.request.Request, timeout: int) -> BinaryIO:
        return self._opener.open(request, timeout=timeout)

    def _request(
        self,
        method: str,
        fields: dict[str, str | int],
        *,
        timeout: int | None = None,
        content_type: str = "application/x-www-form-urlencoded",
        encoded: bytes | None = None,
    ) -> Any:
        if not METHOD_PATTERN.fullmatch(method):
            raise TelegramError("Telegram method was invalid")
        body = encoded if encoded is not None else urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(
            self._base + method,
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )
        try:
            with self._open(request, timeout or self.request_timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(self.max_response_bytes + 1)
            retry_after = None
            try:
                error_value = json.loads(raw.decode("utf-8"))
                parameters = (
                    error_value.get("parameters")
                    if isinstance(error_value, dict)
                    else None
                )
                candidate = (
                    parameters.get("retry_after")
                    if isinstance(parameters, dict)
                    else None
                )
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    retry_after = max(0, min(3600, candidate))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise TelegramAPIError(exc.code, retry_after) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramTransportError(
                "Telegram Bot API is unreachable"
            ) from exc
        if len(raw) > self.max_response_bytes:
            raise TelegramError("Telegram response exceeded its size limit")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramError("Telegram returned malformed JSON") from exc
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            code = envelope.get("error_code") if isinstance(envelope, dict) else None
            parameters = (
                envelope.get("parameters")
                if isinstance(envelope, dict)
                else None
            )
            candidate = (
                parameters.get("retry_after")
                if isinstance(parameters, dict)
                else None
            )
            retry_after = (
                max(0, min(3600, candidate))
                if isinstance(candidate, int) and not isinstance(candidate, bool)
                else None
            )
            raise TelegramAPIError(
                code if isinstance(code, int) else None,
                retry_after,
            )
        return envelope.get("result")

    def _request_with_flood_retry(
        self,
        method: str,
        fields: dict[str, str | int],
        *,
        retry_state: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> Any:
        backoff = 2
        while True:
            try:
                return self._request(method, fields, **kwargs)
            except TelegramAPIError as exc:
                if exc.error_code != 429:
                    raise
                LOG.warning("Telegram rate limited outbound delivery; retrying.")
                if retry_state is not None:
                    retry_state("pending")
                if self._retry_sleeper(_retry_delay(exc, backoff)):
                    raise TelegramNotDeliveredError(
                        "Telegram delivery was interrupted"
                    ) from exc
                if retry_state is not None:
                    retry_state("sending")
                backoff = min(60, backoff * 2)

    def get_me(self) -> dict[str, Any]:
        result = self._request("getMe", {})
        if not isinstance(result, dict) or result.get("is_bot") is not True:
            raise TelegramError("Telegram bot identity response was invalid")
        return result

    def delete_webhook(self, *, drop_pending_updates: bool) -> None:
        self._request(
            "deleteWebhook",
            {"drop_pending_updates": str(drop_pending_updates).lower()},
        )

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        fields: dict[str, str | int] = {
            "timeout": timeout,
            "limit": limit,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            fields["offset"] = offset
        result = self._request(
            "getUpdates",
            fields,
            timeout=max(self.request_timeout, timeout + 10),
        )
        if (
            not isinstance(result, list)
            or any(
                not isinstance(item, dict)
                or _nonnegative_int(item.get("update_id")) is None
                for item in result
            )
        ):
            raise TelegramError("Telegram updates response was invalid")
        return result

    def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        retry_state: Callable[[str], None] | None = None,
    ) -> list[int]:
        message_ids = []
        for chunk in split_text(text, config.TELEGRAM_MESSAGE_CHUNK_CHARS):
            result = self._request_with_flood_retry(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    # Documented Bot API field; disable_web_page_preview is a
                    # legacy alias that Telegram no longer documents.
                    "link_preview_options": json.dumps(
                        {"is_disabled": True},
                        separators=(",", ":"),
                    ),
                },
                retry_state=retry_state,
            )
            message_id = (
                _positive_int(result.get("message_id"))
                if isinstance(result, dict)
                else None
            )
            if message_id is None:
                raise TelegramError("Telegram send response had no message ID")
            message_ids.append(message_id)
        return message_ids

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        if action != "typing":
            raise TelegramError("unsupported Telegram chat action")
        result = self._request(
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
        )
        if result is not True:
            raise TelegramError("Telegram chat action response was invalid")

    def _send_file(
        self,
        method: str,
        field_name: str,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        allowed_types: dict[str, str],
        retry_state: Callable[[str], None] | None = None,
    ) -> int:
        extension = allowed_types.get(mime_type)
        if (
            extension is None
            or Path(filename).name != filename
            or not FILENAME_PATTERN.fullmatch(filename)
            or not filename.lower().endswith(extension)
            or "\x00" in filename
        ):
            raise TelegramError("Telegram document metadata was invalid")
        if (
            not content
            or len(content) > config.EMAIL_AGENT_MAX_ATTACHMENT_BYTES
        ):
            raise TelegramError("Telegram document was outside its size bound")
        boundary = f"fitlit-{secrets.token_hex(16)}"
        output = io.BytesIO()

        def line(value: bytes) -> None:
            output.write(value + b"\r\n")

        line(f"--{boundary}".encode())
        line(b'Content-Disposition: form-data; name="chat_id"')
        line(b"")
        line(str(chat_id).encode())
        line(f"--{boundary}".encode())
        safe_filename = filename.replace('"', "")
        line(
            b'Content-Disposition: form-data; name="'
            + field_name.encode("ascii")
            + b'"; filename="'
            + safe_filename.encode("ascii")
            + b'"'
        )
        line(f"Content-Type: {mime_type}".encode())
        line(b"")
        output.write(content)
        output.write(b"\r\n")
        line(f"--{boundary}--".encode())
        result = self._request_with_flood_retry(
            method,
            {},
            retry_state=retry_state,
            content_type=f"multipart/form-data; boundary={boundary}",
            encoded=output.getvalue(),
        )
        message_id = (
            _positive_int(result.get("message_id"))
            if isinstance(result, dict)
            else None
        )
        if message_id is None:
            raise TelegramError("Telegram document response had no message ID")
        return message_id

    def send_document(
        self,
        chat_id: int,
        path: Path,
        filename: str,
        mime_type: str,
    ) -> int:
        return self._send_file(
            "sendDocument",
            "document",
            chat_id,
            path.read_bytes(),
            filename,
            mime_type,
            DOCUMENT_MIME_TYPES,
        )

    def send_photo(
        self,
        chat_id: int,
        path: Path,
        filename: str,
        mime_type: str,
    ) -> int:
        return self._send_file(
            "sendPhoto",
            "photo",
            chat_id,
            path.read_bytes(),
            filename,
            mime_type,
            PHOTO_MIME_TYPES,
        )

    def send_document_bytes(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        *,
        retry_state: Callable[[str], None] | None = None,
    ) -> int:
        return self._send_file(
            "sendDocument",
            "document",
            chat_id,
            content,
            filename,
            mime_type,
            DOCUMENT_MIME_TYPES,
            retry_state,
        )

    def send_photo_bytes(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        *,
        retry_state: Callable[[str], None] | None = None,
    ) -> int:
        return self._send_file(
            "sendPhoto",
            "photo",
            chat_id,
            content,
            filename,
            mime_type,
            PHOTO_MIME_TYPES,
            retry_state,
        )


def _text_parts(text: str) -> list[tuple[str, bytes, str, str]]:
    return [
        ("text", chunk.encode("utf-8"), "", "text/plain")
        for chunk in split_text(text, config.TELEGRAM_MESSAGE_CHUNK_CHARS)
    ]


def _reply_parts(reply: email_agent.AgentReply) -> list[tuple[str, bytes, str, str]]:
    parts = _text_parts(reply.text)
    for attachment in reply.attachments:
        # Every artifact, including PNG screenshots, is uploaded as a document
        # so Telegram keeps the exact locally rendered bytes.
        parts.append((
            "document",
            attachment.path.read_bytes(),
            attachment.filename,
            attachment.mime_type,
        ))
    return parts


def _delivery_notice(delivered: int, statuses: list[str]) -> str:
    if "uncertain" in statuses:
        if delivered == 0:
            return (
                "FitLit could not confirm whether that answer reached this "
                "chat and did not resend it, because Telegram offers no way "
                "to make a resend safe. Please ask again if nothing arrived."
            )
        return (
            "Part of that answer could not be confirmed as delivered. FitLit "
            "did not resend it, so some of it may be missing."
        )
    if delivered == 0:
        return (
            "FitLit could not deliver that answer. Nothing from it reached "
            "this chat, so please ask again."
        )
    return (
        "Telegram rejected part of that answer, so some of it is missing."
    )


def _send_delivery_notice(
    client: TelegramClient,
    chat_id: int,
    delivered: int,
    statuses: list[str],
) -> None:
    """Disclose an incomplete delivery without ever wedging the update."""
    try:
        client.send_text(chat_id, _delivery_notice(delivered, statuses))
    except (TelegramError, OSError):
        LOG.error("Telegram delivery notice could not be sent.")


def _deliver_reply_plan(
    client: TelegramClient,
    transcript: TelegramTranscriptStore,
    chat_id: int,
    plan: TelegramReplyPlan,
) -> None:
    for part in plan.parts:
        if part.status in {"delivered", "failed"}:
            continue
        if part.status in {"sending", "uncertain"}:
            if part.status == "sending":
                transcript.set_part_status(
                    plan,
                    part.part_index,
                    "uncertain",
                )
            LOG.error(
                "Telegram update %s part %s was delivery-uncertain; it was "
                "not resent.",
                plan.source_update_id,
                part.part_index,
            )
            continue
        transcript.set_part_status(plan, part.part_index, "sending")
        retry_state = lambda status: transcript.set_part_status(
            plan,
            part.part_index,
            status,
        )
        try:
            if part.kind == "text":
                message_ids = client.send_text(
                    chat_id,
                    part.payload.decode("utf-8"),
                    retry_state=retry_state,
                )
                if len(message_ids) != 1:
                    raise TelegramError(
                        "Telegram text part produced an invalid receipt"
                    )
                message_id = message_ids[0]
            elif part.kind == "photo":
                message_id = client.send_photo_bytes(
                    chat_id,
                    part.payload,
                    part.filename,
                    part.mime_type,
                    retry_state=retry_state,
                )
            else:
                message_id = client.send_document_bytes(
                    chat_id,
                    part.payload,
                    part.filename,
                    part.mime_type,
                    retry_state=retry_state,
                )
        except TelegramAPIError as exc:
            if _retryable_api_error(exc):
                transcript.set_part_status(plan, part.part_index, "pending")
                raise
            transcript.set_part_status(plan, part.part_index, "failed")
            if part.kind == "text":
                transcript.fail_remaining(plan)
                LOG.error(
                    "Telegram update %s had its reply text permanently "
                    "rejected with status %s.",
                    plan.source_update_id,
                    exc.error_code,
                )
                break
            LOG.error(
                "Telegram update %s had one reply artifact permanently "
                "rejected with status %s; delivery continues.",
                plan.source_update_id,
                exc.error_code,
            )
        except TelegramNotDeliveredError:
            transcript.set_part_status(plan, part.part_index, "pending")
            raise
        except TelegramError:
            raise
        else:
            transcript.set_part_status(
                plan,
                part.part_index,
                "delivered",
                telegram_message_id=message_id,
            )
    final = transcript.reply_plan(plan.user_id, plan.source_update_id) or plan
    statuses = [item.status for item in final.parts]
    delivered = statuses.count("delivered")
    incomplete = any(status in {"failed", "uncertain"} for status in statuses)
    if incomplete:
        LOG.error(
            "Telegram update %s completed with %s of %s parts delivered.",
            plan.source_update_id,
            delivered,
            len(statuses),
        )
    transcript.complete_reply(plan)
    if incomplete:
        _send_delivery_notice(client, chat_id, delivered, statuses)


def _deliver_fixed_text(
    client: TelegramClient,
    transcript: TelegramTranscriptStore,
    inbound: TelegramInbound,
    text: str,
    *,
    conversation: TelegramConversation | None = None,
) -> None:
    if transcript.fixed_reply_completed(
        inbound.user_id,
        inbound.update_id,
    ):
        transcript.discard_reply(inbound.user_id, inbound.update_id)
        return
    selected = conversation or transcript.active(inbound.user_id)
    plan = transcript.reply_plan(inbound.user_id, inbound.update_id)
    if plan is None:
        plan = transcript.prepare_reply(
            selected,
            user_id=inbound.user_id,
            source_update_id=inbound.update_id,
            assistant_text=text,
            parts=_text_parts(text),
            record_in_transcript=False,
        )
    _deliver_reply_plan(client, transcript, inbound.chat_id, plan)


@contextmanager
def _typing_presence(
    client: TelegramClient,
    chat_id: int,
):
    stopped = threading.Event()

    def refresh() -> None:
        while not stopped.is_set():
            try:
                client.send_chat_action(chat_id, "typing")
            except (TelegramError, OSError):
                LOG.warning("Telegram typing presence could not be refreshed.")
            if stopped.wait(TYPING_REFRESH_SECONDS):
                return

    worker = threading.Thread(
        target=refresh,
        name="fitlit-telegram-typing",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=1)


def _grounded_reply(
    client: TelegramClient,
    inbound: TelegramInbound,
    transcript: TelegramTranscriptStore,
) -> None:
    conversation = transcript.active(inbound.user_id)
    user_turn = email_agent.ThreadTurn(
        role="user",
        content=inbound.text or "",
        internal_date_ms=inbound.sent_at_ms,
    )
    transcript.append(
        conversation,
        user_id=inbound.user_id,
        role="user",
        content=user_turn.content,
        sent_at_ms=user_turn.internal_date_ms,
        source_update_id=inbound.update_id,
    )
    if transcript.has_turn(
        conversation,
        role="assistant",
        source_update_id=inbound.update_id,
    ):
        transcript.discard_reply(inbound.user_id, inbound.update_id)
        return
    plan = transcript.reply_plan(inbound.user_id, inbound.update_id)
    if plan is None:
        turns = transcript.history(conversation)
        now = datetime.fromtimestamp(inbound.sent_at_ms / 1000, PACIFIC)
        try:
            with _typing_presence(client, inbound.chat_id):
                with email_agent.draft(
                    turns,
                    now=now,
                    context_limit=None,
                    channel="telegram",
                    instructions=conversation.system_instructions,
                    model=_telegram_model(),
                    reasoning_effort=config.TELEGRAM_REASONING_EFFORT,
                ) as reply:
                    assistant_text = reply.text
                    parts = _reply_parts(reply)
        except email_agent.EmailAgentInputTooLargeError:
            LOG.error(
                "Telegram headless provider input exceeded its byte budget."
            )
            assistant_text = TOO_LARGE_REPLY
            parts = _text_parts(assistant_text)
        except email_agent.EmailAgentError as exc:
            LOG.error("Telegram headless provider failed: %s", exc)
            assistant_text = PROVIDER_FAILURE_REPLY
            parts = _text_parts(assistant_text)
        except (OSError, TelegramError) as exc:
            # A local rendering, attachment, or chunking fault must degrade to
            # a safe reply instead of retrying the same update forever.
            LOG.error(
                "Telegram reply plan could not be built: %s",
                type(exc).__name__,
            )
            assistant_text = PROVIDER_FAILURE_REPLY
            parts = _text_parts(assistant_text)
        plan = transcript.prepare_reply(
            conversation,
            user_id=inbound.user_id,
            source_update_id=inbound.update_id,
            assistant_text=assistant_text,
            parts=parts,
        )
    _deliver_reply_plan(client, transcript, inbound.chat_id, plan)


def process_update(
    client: TelegramClient,
    state: TelegramState,
    transcript: TelegramTranscriptStore,
    update: dict[str, Any],
) -> str:
    """Handle exactly one update and return its recorded outcome."""
    update_id = _update_id(update)
    inbound = parse_inbound(update)
    if (
        inbound is None
        or inbound.user_id != config.TELEGRAM_TRUSTED_USER_ID
    ):
        state.finish(update_id, "ignored")
        return "ignored"
    if inbound.text is None:
        _deliver_fixed_text(
            client,
            transcript,
            inbound,
            "FitLit currently accepts text messages and sends evidence files.",
        )
        state.finish(update_id, "unsupported")
        return "unsupported"
    if (
        "\x00" in inbound.text
        or len(inbound.text) > config.TELEGRAM_BODY_MAX_CHARS
    ):
        _deliver_fixed_text(
            client,
            transcript,
            inbound,
            "That message is too long for the private FitLit channel.",
        )
        state.finish(update_id, "too-long")
        return "too-long"
    command = _command(inbound.text)
    if command in {"/start", "/help", "/pair"}:
        _deliver_fixed_text(
            client,
            transcript,
            inbound,
            (
                "FitLit is connected. Send a health question, or use /new to "
                "archive the active thread and start a fresh conversation."
            ),
        )
        state.finish(update_id, "command")
        return "command"
    if command == "/reset":
        _deliver_fixed_text(
            client,
            transcript,
            inbound,
            "Reset is disabled because transcripts are never deleted. Use /new.",
        )
        state.finish(update_id, "reset-disabled")
        return "reset-disabled"
    if command == "/new":
        conversation = transcript.start_new(inbound.user_id, update_id)
        _deliver_fixed_text(
            client,
            transcript,
            inbound,
            (
                f"Started conversation {conversation.conversation_id}. "
                "Earlier conversations remain archived locally."
            ),
            conversation=conversation,
        )
        state.finish(update_id, "new-conversation")
        return "new-conversation"
    _grounded_reply(client, inbound, transcript)
    state.finish(update_id, "replied")
    return "replied"


def _transient_failure(error: BaseException) -> bool:
    """True when retrying the same update later can still succeed."""
    if isinstance(error, (TelegramTransportError, TelegramNotDeliveredError)):
        return True
    if isinstance(error, TelegramAPIError):
        return _retryable_api_error(error)
    if isinstance(error, sqlite3.OperationalError):
        return True
    # State-file and other filesystem faults are environmental, not poison.
    return isinstance(error, OSError)


def _sanitized_failure(error: BaseException) -> str:
    """Describe a failure with no message body, user identity, or payload."""
    if isinstance(error, TelegramAPIError):
        return f"TelegramAPIError:{error.error_code}"
    return type(error).__name__[:120]


def _quarantine_update(
    client: TelegramClient,
    state: TelegramState,
    transcript: TelegramTranscriptStore,
    update_id: int,
    attempts: int,
) -> None:
    """Give up on one poison update so later updates stop being blocked."""
    # Persist the advancing offset first. If this fails, no user-visible notice
    # is emitted and the retry remains idempotent.
    state.finish(update_id, "quarantined")
    user_id = config.TELEGRAM_TRUSTED_USER_ID
    if user_id is not None:
        try:
            client.send_text(user_id, QUARANTINE_NOTICE)
        except (TelegramError, OSError):
            LOG.error("Telegram quarantine notice could not be sent.")
        try:
            transcript.discard_reply(user_id, update_id)
        except sqlite3.Error:
            LOG.error("Telegram quarantined reply could not be discarded.")
    LOG.error(
        "Telegram update %s was quarantined after %s failed attempts.",
        update_id,
        attempts,
    )


def _process_with_quarantine(
    client: TelegramClient,
    state: TelegramState,
    transcript: TelegramTranscriptStore,
    update: dict[str, Any],
) -> str:
    """Run one update, bounding deterministic failures so none can block."""
    update_id = _update_id(update)
    started = time.monotonic()
    try:
        outcome = process_update(client, state, transcript, update)
    except Exception as exc:
        elapsed = time.monotonic() - started
        if _transient_failure(exc):
            LOG.error(
                "Telegram update %s hit a transient failure (%s) after "
                "%.2f seconds; it keeps its full retry budget.",
                update_id,
                _sanitized_failure(exc),
                elapsed,
            )
            raise
        try:
            attempts = transcript.record_failure(
                update_id,
                _sanitized_failure(exc),
            )
        except sqlite3.Error:
            # The quarantine ledger itself is unavailable, so this attempt is
            # environmental: keep the retry budget and let the loop back off.
            LOG.error(
                "Telegram update %s could not record its failure; treating "
                "the attempt as transient.",
                update_id,
            )
            raise
        LOG.error(
            "Telegram update %s failed locally (%s), attempt %s of %s, "
            "after %.2f seconds.",
            update_id,
            _sanitized_failure(exc),
            attempts,
            config.TELEGRAM_POISON_ATTEMPTS,
            elapsed,
        )
        if attempts < config.TELEGRAM_POISON_ATTEMPTS:
            if isinstance(exc, TelegramError):
                raise
            raise TelegramError(
                "Telegram update processing failed"
            ) from exc
        _quarantine_update(client, state, transcript, update_id, attempts)
        return "quarantined"
    try:
        transcript.clear_failure(update_id)
    except sqlite3.Error:
        # The update really did succeed; a stale ledger row is harmless.
        LOG.error(
            "Telegram update %s could not clear its failure record.",
            update_id,
        )
    LOG.info(
        "Telegram update %s finished as %s in %.2f seconds.",
        update_id,
        outcome,
        time.monotonic() - started,
    )
    return outcome

def _update_env(values: dict[str, str]) -> None:
    path = config.BASE_DIR / ".env"
    if path.exists() and path.is_symlink():
        raise TelegramError("refusing symlinked .env")
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for raw in lines:
        key, separator, _ = raw.partition("=")
        normalized = key.strip()
        if separator and normalized in remaining:
            output.append(f"{normalized}={remaining.pop(normalized)}")
        else:
            output.append(raw)
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output).rstrip("\n") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def pair(client: TelegramClient) -> int:
    """Bind one numeric user ID while no listener owns the update stream."""
    if (
        config.TELEGRAM_ENABLED
        or config.TELEGRAM_TRUSTED_USER_ID is not None
    ):
        raise TelegramConfigError(
            "Telegram is already paired; rotate the bot token without "
            "re-pairing, or explicitly clear the pairing configuration first"
        )
    with single_instance("pairing"):
        return _pair(client)


def _pair(client: TelegramClient) -> int:
    identity = client.get_me()
    # Before the first trusted user is established, queued updates have no
    # authenticated owner and are deliberately discarded.
    client.delete_webhook(drop_pending_updates=True)
    username = identity.get("username")
    destination = f"@{username}" if isinstance(username, str) else "your bot"
    code = secrets.token_urlsafe(18)
    print(f"Open {destination} in Telegram and send exactly:")
    print(f"/pair {code}")
    print("Waiting up to five minutes...")
    deadline = time.monotonic() + 300
    state = TelegramState()
    # The persisted offset is only ever advanced here, never rewound, so
    # pairing cannot replay or skip anything the listener already handled.
    offset = state.offset
    expected = f"/pair {code}"
    while time.monotonic() < deadline:
        timeout = max(1, min(20, int(deadline - time.monotonic())))
        for update in client.get_updates(
            offset=offset,
            timeout=timeout,
            limit=100,
        ):
            update_id = _update_id(update)
            offset = update_id + 1
            inbound = parse_inbound(update)
            if inbound is not None and secrets.compare_digest(
                (inbound.text or "").encode("utf-8"),
                expected.encode("utf-8"),
            ):
                _update_env({
                    "FITLIT_TELEGRAM_TRUSTED_USER_ID": str(inbound.user_id),
                    "FITLIT_TELEGRAM_ENABLED": "true",
                })
                state.finish(update_id, "paired")
                client.send_text(
                    inbound.chat_id,
                    "FitLit pairing completed. The private channel is ready.",
                )
                print("Telegram pairing completed; private .env was updated.")
                return 0
            state.finish(update_id, "pairing-ignored")
    raise TelegramError("Telegram pairing timed out")


def _retry_delay(error: TelegramAPIError | None, backoff: int) -> int:
    return max(backoff, error.retry_after or 0) if error else backoff


def _initialize(
    client: TelegramClient,
    *,
    stopping: Callable[[], bool],
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    backoff = 2
    while not stopping():
        try:
            client.get_me()
            client.delete_webhook(drop_pending_updates=False)
            return True
        except TelegramAPIError as exc:
            if exc.error_code == 401:
                raise TelegramConfigError(
                    "Telegram rejected the configured bot token"
                ) from exc
            LOG.error("Telegram startup request failed; retrying.")
            sleeper(_retry_delay(exc, backoff))
        except TelegramError:
            LOG.error("Telegram startup transport failed; retrying.")
            sleeper(backoff)
        backoff = min(60, backoff * 2)
    return False


def _telegram_model() -> str | None:
    return {
        "copilot": config.TELEGRAM_COPILOT_MODEL,
        "codex": config.TELEGRAM_CODEX_MODEL,
        "claude": config.TELEGRAM_CLAUDE_MODEL,
        "opencode": config.TELEGRAM_OPENCODE_MODEL,
    }.get(config.HARNESS) or None


def provider_installed() -> bool:
    provider = config.HARNESS
    return (
        provider in email_agent.PROVIDERS
        and bool(shutil.which(provider))
    )


def model_valid() -> bool:
    model = _telegram_model()
    # email_agent applies exactly this pattern before it ever builds a command
    # line; reuse it rather than re-implementing provider argument validation.
    return model is None or bool(
        email_agent._MODEL_PATTERN.fullmatch(model)
    )


def effort_valid() -> bool:
    return config.TELEGRAM_REASONING_EFFORT in email_agent._REASONING_EFFORTS


def _validate_agent_settings() -> None:
    """Fail fast before polling rather than once per delivered question."""
    if config.HARNESS not in email_agent.PROVIDERS:
        raise TelegramConfigError(
            "HARNESS is not a supported headless harness"
        )
    if not provider_installed():
        raise TelegramConfigError(
            "the configured headless provider is not installed"
        )
    if not model_valid():
        raise TelegramConfigError(
            "the configured Telegram harness model has an invalid format"
        )
    if not effort_valid():
        raise TelegramConfigError(
            "FITLIT_TELEGRAM_REASONING_EFFORT is not a supported effort"
        )


def run(client: TelegramClient) -> int:
    """Poll Telegram for the paired owner under an exclusive instance lock."""
    if not config.TELEGRAM_ENABLED:
        raise TelegramConfigError("FITLIT_TELEGRAM_ENABLED must be true")
    if (
        config.TELEGRAM_TRUSTED_USER_ID is None
        or config.TELEGRAM_TRUSTED_USER_ID <= 0
    ):
        raise TelegramConfigError("Telegram trusted user is not configured")
    _validate_agent_settings()
    with single_instance("the listener"):
        return _run(client)


def _run(client: TelegramClient) -> int:
    state = TelegramState()
    transcript = TelegramTranscriptStore()
    stopping = threading.Event()

    def stop(*_: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client.set_retry_sleeper(stopping.wait)
    if not _initialize(
        client,
        stopping=stopping.is_set,
        sleeper=stopping.wait,
    ):
        return 0
    backoff = 2
    LOG.info(
        "FitLit Telegram listener started with complete active-thread context.",
    )
    while not stopping.is_set():
        try:
            updates = client.get_updates(
                offset=state.offset,
                timeout=config.TELEGRAM_POLL_TIMEOUT_SECONDS,
                limit=1,
            )
            if updates:
                _process_with_quarantine(client, state, transcript, updates[0])
            backoff = 2
        except TelegramAPIError as exc:
            if exc.error_code == 401:
                # Only 401 proves the token itself is rejected. Exiting with
                # EX_CONFIG stops the restart loop instead of hammering
                # Telegram with a credential it will never accept.
                raise TelegramConfigError(
                    "Telegram rejected the configured bot token"
                ) from exc
            if exc.error_code == 409:
                LOG.error(
                    "Telegram polling conflict (409); stop other bot pollers "
                    "or webhooks. Retrying in %s seconds.",
                    _retry_delay(exc, backoff),
                )
            else:
                # A 403 here can mean many things (chat blocked, restricted
                # method), so it is never treated as an invalid token.
                LOG.error(
                    "Telegram API request failed with status %s; retrying in "
                    "%s seconds.",
                    exc.error_code,
                    _retry_delay(exc, backoff),
                )
            stopping.wait(_retry_delay(exc, backoff))
            backoff = min(60, backoff * 2)
        except TelegramError as exc:
            # TelegramError messages are fixed operational strings, never
            # message bodies or identities.
            delay = _retry_delay(None, backoff)
            LOG.error(
                "Telegram transport or response failed: %s (%s); retrying in "
                "%s seconds.",
                exc,
                _sanitized_failure(exc),
                delay,
            )
            stopping.wait(delay)
            backoff = min(60, backoff * 2)
        except Exception as exc:
            # Last resort: an unexpected defect must not end the listener,
            # because a dead daemon delivers nothing at all.
            delay = _retry_delay(None, backoff)
            LOG.exception(
                "Telegram listener hit an unexpected failure (%s); retrying "
                "in %s seconds.",
                _sanitized_failure(exc),
                delay,
            )
            stopping.wait(delay)
            backoff = min(60, backoff * 2)
    return 0


def _state_snapshot() -> dict[str, Any]:
    if not config.TELEGRAM_STATE_PATH.is_file():
        return {
            "last_update_id": None,
            "last_outcome": None,
            "last_update_at": None,
            "seconds_since_last_update": None,
        }
    try:
        state = TelegramState()
    except TelegramError:
        return {
            "last_update_id": None,
            "last_outcome": "unreadable",
            "last_update_at": None,
            "seconds_since_last_update": None,
        }
    updated_at = int(state.value["updated_at"])
    last_update_id = int(state.value["last_update_id"])
    return {
        "last_update_id": last_update_id if last_update_id >= 0 else None,
        "last_outcome": str(state.value["status"]),
        "last_update_at": (
            datetime.fromtimestamp(updated_at, PACIFIC).isoformat()
            if updated_at > 0
            else None
        ),
        "seconds_since_last_update": (
            state.idle_seconds() if updated_at > 0 else None
        ),
    }


def status() -> int:
    conversations = 0
    turns = 0
    pending_parts = 0
    quarantined = 0
    if (
        config.TELEGRAM_TRUSTED_USER_ID is not None
        and config.TELEGRAM_TRANSCRIPT_PATH.is_file()
    ):
        store = TelegramTranscriptStore()
        conversations, turns = store.stats(config.TELEGRAM_TRUSTED_USER_ID)
        pending_parts = store.pending_parts(config.TELEGRAM_TRUSTED_USER_ID)
        quarantined = store.quarantined_count()
    print(json.dumps({
        "enabled": config.TELEGRAM_ENABLED,
        "bot_token_configured": bool(config.TELEGRAM_BOT_TOKEN),
        "trusted_user_configured": (
            config.TELEGRAM_TRUSTED_USER_ID is not None
        ),
        "state_exists": config.TELEGRAM_STATE_PATH.is_file(),
        "transcript_exists": config.TELEGRAM_TRANSCRIPT_PATH.is_file(),
        "listener_running": listener_running(),
        "conversation_count": conversations,
        "turn_count": turns,
        "pending_outbound_parts": pending_parts,
        "quarantined_updates": quarantined,
        "context_policy": "complete-active-conversation",
        "poll_seconds": config.TELEGRAM_POLL_TIMEOUT_SECONDS,
        "harness": config.HARNESS,
        "provider_installed": provider_installed(),
        "model": email_agent.selected_model(_telegram_model()),
        "model_valid": model_valid(),
        "reasoning_effort": config.TELEGRAM_REASONING_EFFORT,
        "effort_valid": effort_valid(),
        "timezone": "America/Los_Angeles",
        **_state_snapshot(),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pair", "run", "status"))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if args.command == "status":
        return status()
    try:
        client = TelegramClient(config.TELEGRAM_BOT_TOKEN)
        return pair(client) if args.command == "pair" else run(client)
    except TelegramConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EX_CONFIG if args.command == "run" else 1
    except TelegramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Telegram command interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
