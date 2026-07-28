"""Constrained self-addressed Gmail command reader for FitLit."""
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path

from fitlit import config, email_assistant, gmail_auth, gmail_client
from fitlit.journal import PACIFIC


class GmailInboxError(RuntimeError):
    """Raised when Gmail inbox polling fails."""


@dataclass(frozen=True)
class InboundCommand:
    message_id: str
    thread_id: str | None
    rfc_message_id: str | None
    references: str | None
    subject: str
    question: str


class InboxStore:
    """Immutable-message ledger and independent reply-rate limiter."""

    def __init__(self, path: Path = config.GMAIL_INBOX_DB):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS inbound_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    pacific_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    reply_id TEXT,
                    error TEXT,
                    retry_after TEXT
                );
                CREATE TABLE IF NOT EXISTS inbound_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    pacific_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    finished_at TEXT,
                    reply_id TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_inbound_attempt_day
                ON inbound_attempts(pacific_date, status);
                CREATE INDEX IF NOT EXISTS idx_inbound_message_attempt
                ON inbound_attempts(message_id, id);
            """)
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(inbound_messages)"
                )
            }
            if "retry_after" not in columns:
                connection.execute(
                    "ALTER TABLE inbound_messages ADD COLUMN retry_after TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def has(self, message_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status,retry_after FROM inbound_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        if not row:
            return False
        if row["status"] != "retryable":
            return True
        retry_after = row["retry_after"]
        if not retry_after:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(retry_after)
        except ValueError:
            return False

    def attempted_today(self, day: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM inbound_attempts WHERE pacific_date=?",
                (day,),
            ).fetchone()[0]

    def reserve(self, command: InboundCommand, now: datetime) -> bool:
        day = now.astimezone(PACIFIC).date().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM inbound_messages WHERE message_id=?",
                (command.message_id,),
            ).fetchone()
            if existing and existing["status"] != "retryable":
                return False
            attempted = connection.execute(
                "SELECT COUNT(*) FROM inbound_attempts WHERE pacific_date=?",
                (day,),
            ).fetchone()[0]
            if attempted >= config.GMAIL_INBOX_DAILY_MAX:
                return False
            timestamp = now.astimezone(timezone.utc).isoformat()
            if existing:
                connection.execute(
                    "UPDATE inbound_messages SET pacific_date=?,status='sending',"
                    "error=NULL,retry_after=NULL "
                    "WHERE message_id=?",
                    (day, command.message_id),
                )
            else:
                connection.execute(
                    "INSERT INTO inbound_messages("
                    "message_id,thread_id,pacific_date,status,created_at"
                    ") VALUES(?,?,?,?,?)",
                    (
                        command.message_id,
                        command.thread_id,
                        day,
                        "sending",
                        timestamp,
                    ),
                )
            connection.execute(
                "INSERT INTO inbound_attempts("
                "message_id,pacific_date,status,attempted_at"
                ") VALUES(?,?,?,?)",
                (command.message_id, day, "sending", timestamp),
            )
            connection.commit()
            return True

    def ignore(self, message_id: str, thread_id: str | None, now: datetime, reason: str) -> None:
        day = now.astimezone(PACIFIC).date().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO inbound_messages("
                "message_id,thread_id,pacific_date,status,created_at,processed_at,error"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    message_id,
                    thread_id,
                    day,
                    "ignored",
                    now.astimezone(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    reason[:300],
                ),
            )
            connection.commit()

    def finish(
        self,
        message_id: str,
        *,
        reply_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE inbound_messages SET status=?,processed_at=?,reply_id=?,error=? "
                "WHERE message_id=?",
                (
                    "sent" if reply_id else "failed",
                    datetime.now(timezone.utc).isoformat(),
                    reply_id,
                    error,
                    message_id,
                ),
            )
            connection.execute(
                "UPDATE inbound_attempts SET status=?,finished_at=?,reply_id=?,error=? "
                "WHERE id=(SELECT MAX(id) FROM inbound_attempts WHERE message_id=?)",
                (
                    "sent" if reply_id else "failed",
                    datetime.now(timezone.utc).isoformat(),
                    reply_id,
                    error,
                    message_id,
                ),
            )
            connection.commit()

    def retry(self, message_id: str, error: str) -> None:
        with self._connect() as connection:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM inbound_attempts WHERE message_id=?",
                (message_id,),
            ).fetchone()[0]
            delay = min(
                3600,
                config.GMAIL_INBOX_RETRY_BASE_SECONDS
                * (2 ** min(max(0, attempts - 1), 7)),
            )
            now = datetime.now(timezone.utc)
            connection.execute(
                "UPDATE inbound_messages SET status='retryable',processed_at=?,"
                "error=?,retry_after=? "
                "WHERE message_id=? AND status='sending'",
                (
                    now.isoformat(),
                    error,
                    (now + timedelta(seconds=delay)).isoformat(),
                    message_id,
                ),
            )
            connection.execute(
                "UPDATE inbound_attempts SET status='retryable',finished_at=?,error=? "
                "WHERE id=(SELECT MAX(id) FROM inbound_attempts WHERE message_id=?)",
                (
                    now.isoformat(),
                    error,
                    message_id,
                ),
            )
            connection.commit()

    def recent(self, limit: int = 10) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT message_id,pacific_date,status,processed_at,reply_id,error "
                "FROM inbound_messages ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _api_json(
    path: str,
    *,
    query: dict[str, str | int] | None = None,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    url = f"{config.GMAIL_API_BASE}/users/me/{path.lstrip('/')}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    token = gmail_auth.get_inbox_access_token()
    for attempt in range(2):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=config.REQUEST_TIMEOUT,
            ) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GmailInboxError("Gmail inbox API returned malformed JSON") from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                token = gmail_auth.get_inbox_access_token(force_refresh=True)
                continue
            detail = exc.read().decode("utf-8", "replace")
            raise GmailInboxError(
                f"Gmail inbox API {exc.code} {exc.reason}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GmailInboxError(f"Gmail inbox API unreachable: {exc}") from exc
    raise GmailInboxError("Gmail inbox authorization retry failed")


def _decode(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding).decode("utf-8", "replace")
    except (ValueError, UnicodeError, binascii.Error):
        return ""


def _plain_parts(payload: dict) -> list[str]:
    values = []
    mime_type = str(payload.get("mimeType", "")).lower()
    part_headers = _headers(payload)
    disposition = part_headers.get("content-disposition", "").lower()
    if (
        payload.get("filename")
        or disposition.startswith("attachment")
        or mime_type == "message/rfc822"
    ):
        return values
    if (
        mime_type == "text/plain"
        and payload.get("body", {}).get("data")
    ):
        values.append(_decode(payload["body"]["data"]))
    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            values.extend(_plain_parts(part))
    return values


def _bounded_body(payload: dict) -> str:
    text = "\n".join(_plain_parts(payload)).replace("\x00", "")
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if (
            stripped.startswith(">")
            or lowered in (
                "-----original message-----",
                "begin forwarded message:",
            )
            or lowered.startswith("---------- forwarded message")
            or (
            stripped.lower().startswith("on ")
            and stripped.lower().endswith(" wrote:")
            )
        ):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:config.GMAIL_INBOX_BODY_MAX_CHARS]


def _headers(payload: dict) -> dict[str, str]:
    return {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", [])
        if item.get("name")
    }


def _parse_message(payload: dict) -> tuple[InboundCommand | None, str | None]:
    message_id = str(payload.get("id", ""))
    thread_id = payload.get("threadId")
    body = payload.get("payload") or {}
    headers = _headers(body)
    expected = config.GMAIL_TO.strip().lower()
    _, sender = parseaddr(headers.get("from", ""))
    recipients = {
        address.lower()
        for _, address in getaddresses([
            headers.get("to", ""),
            headers.get("delivered-to", ""),
        ])
        if address
    }
    if not message_id:
        return None, "missing Gmail message id"
    if "SENT" not in set(payload.get("labelIds") or []):
        return None, "message was not in the authenticated account's Sent mailbox"
    if not expected or sender.lower() != expected or expected not in recipients:
        return None, "sender or recipient did not match configured self-address"
    if headers.get("auto-submitted", "").lower() not in ("", "no"):
        return None, "automated message rejected"
    if headers.get("x-fitlit-notification"):
        return None, "FitLit-generated message rejected"
    subject = headers.get("subject", "").strip()
    prefix = config.GMAIL_INBOX_SUBJECT_PREFIX
    bare_prefix = prefix.rstrip(":").rstrip()
    if not prefix or (
        subject != bare_prefix
        and not subject.startswith(prefix)
    ):
        return None, "subject prefix did not match exactly"
    subject_question = (
        ""
        if subject == bare_prefix
        else subject[len(prefix):].strip()
    )
    body_question = _bounded_body(body)
    question = "\n".join(
        value for value in (subject_question, body_question) if value
    ).strip()
    if not question:
        question = "help"
    return InboundCommand(
        message_id=message_id,
        thread_id=str(thread_id) if thread_id else None,
        rfc_message_id=headers.get("message-id") or None,
        references=headers.get("references") or None,
        subject=subject,
        question=question,
    ), None


def _list_message_ids() -> list[dict]:
    query = (
        f'in:sent from:me to:me newer_than:{config.GMAIL_INBOX_LOOKBACK_DAYS}d '
        f'subject:"{config.GMAIL_INBOX_SUBJECT_PREFIX}"'
    )
    payload = _api_json(
        "messages",
        query={
            "q": query,
            "maxResults": config.GMAIL_INBOX_BATCH_MAX * 4,
        },
    )
    return list(payload.get("messages") or [])


def _get_message(message_id: str) -> dict:
    return _api_json(
        f"messages/{urllib.parse.quote(message_id, safe='')}",
        query={"format": "full"},
    )


def process(
    now: datetime | None = None,
    *,
    dry_run: bool = False,
    store: InboxStore | None = None,
) -> dict:
    local = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    result = {
        "status": "disabled",
        "sent": [],
        "preview": [],
        "skipped": [],
        "ignored": [],
        "failed": [],
        "transient_failure": False,
    }
    if not config.GMAIL_INBOX_ENABLED:
        return result
    if store is None:
        try:
            store = InboxStore()
        except (OSError, sqlite3.Error) as exc:
            result["status"] = "ledger-error"
            result["failed"].append(str(exc))
            return result
    if not gmail_auth.is_inbox_configured():
        result["status"] = "not-configured"
        return result
    result["status"] = "dry-run" if dry_run else "ok"
    try:
        summaries = _list_message_ids()
    except (GmailInboxError, gmail_auth.GmailAuthError) as exc:
        result["status"] = "auth-or-api-error"
        result["failed"].append(str(exc))
        return result

    accepted = 0
    for summary in reversed(summaries):
        if accepted >= config.GMAIL_INBOX_BATCH_MAX:
            break
        message_id = str(summary.get("id", ""))
        try:
            already_seen = bool(message_id and store.has(message_id))
        except sqlite3.Error as exc:
            result["status"] = "ledger-error"
            result["failed"].append(str(exc))
            return result
        if not message_id or already_seen:
            if message_id:
                result["skipped"].append(message_id)
            continue
        try:
            payload = _get_message(message_id)
        except (GmailInboxError, gmail_auth.GmailAuthError) as exc:
            result["failed"].append({"message_id": message_id, "error": str(exc)})
            result["transient_failure"] = True
            continue
        try:
            command, reason = _parse_message(payload)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            result["failed"].append({
                "message_id": message_id,
                "error": f"malformed Gmail message: {exc}",
            })
            continue
        if not command:
            if not dry_run:
                store.ignore(message_id, payload.get("threadId"), local, reason or "rejected")
            result["ignored"].append({"message_id": message_id, "reason": reason})
            continue
        accepted += 1
        try:
            attempted = store.attempted_today(local.date().isoformat())
        except sqlite3.Error as exc:
            result["status"] = "ledger-error"
            result["failed"].append(str(exc))
            return result
        if attempted >= config.GMAIL_INBOX_DAILY_MAX:
            result["skipped"].append(command.message_id)
            continue
        if dry_run:
            try:
                rendered = email_assistant.answer(
                    command.question,
                    now=local,
                    include_ai=False,
                )
            except (
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
            ) as exc:
                result["failed"].append({
                    "message_id": command.message_id,
                    "error": f"could not build preview: {exc}",
                })
                continue
            result["preview"].append({
                "message_id": command.message_id,
                "intent": rendered.intent,
                "subject": f"Re: {command.subject}",
            })
            continue
        try:
            reserved = store.reserve(command, local)
        except sqlite3.Error as exc:
            result["status"] = "ledger-error"
            result["failed"].append(str(exc))
            return result
        if not reserved:
            result["skipped"].append(command.message_id)
            continue
        try:
            rendered = email_assistant.answer(command.question, now=local)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            store.retry(command.message_id, f"could not build answer: {exc}")
            result["transient_failure"] = True
            result["failed"].append({
                "message_id": command.message_id,
                "error": f"could not build answer: {exc}",
            })
            continue
        references = " ".join(
            value for value in (command.references, command.rfc_message_id) if value
        ) or None
        try:
            reply_id = gmail_client.send(
                f"Re: {command.subject}",
                rendered.text,
                rendered.html,
                thread_id=command.thread_id,
                in_reply_to=command.rfc_message_id,
                references=references,
                category="email-assistant",
            )
        except gmail_auth.GmailAuthError as exc:
            store.retry(command.message_id, str(exc))
            result["transient_failure"] = True
            result["failed"].append({"message_id": command.message_id, "error": str(exc)})
            continue
        except gmail_client.GmailSendError as exc:
            if exc.retryable:
                store.retry(command.message_id, str(exc))
                result["transient_failure"] = True
            else:
                store.finish(command.message_id, error=str(exc))
            result["failed"].append({"message_id": command.message_id, "error": str(exc)})
            continue
        store.finish(command.message_id, reply_id=reply_id)
        result["sent"].append({
            "message_id": command.message_id,
            "reply_id": reply_id,
            "intent": rendered.intent,
        })
    result["attempted_today"] = store.attempted_today(local.date().isoformat())
    result["daily_max"] = config.GMAIL_INBOX_DAILY_MAX
    return result


def status(store: InboxStore | None = None) -> dict:
    local = datetime.now(PACIFIC)
    if not config.GMAIL_INBOX_ENABLED and store is None:
        return {
            "enabled": False,
            "configured": gmail_auth.is_inbox_configured(),
            "subject_prefix": config.GMAIL_INBOX_SUBJECT_PREFIX,
            "attempted_today": 0,
            "daily_max": config.GMAIL_INBOX_DAILY_MAX,
            "recent": [],
        }
    if store is None:
        try:
            store = InboxStore()
        except (OSError, sqlite3.Error) as exc:
            return {
                "enabled": config.GMAIL_INBOX_ENABLED,
                "configured": gmail_auth.is_inbox_configured(),
                "subject_prefix": config.GMAIL_INBOX_SUBJECT_PREFIX,
                "status": "ledger-error",
                "error": str(exc),
            }
    return {
        "enabled": config.GMAIL_INBOX_ENABLED,
        "configured": gmail_auth.is_inbox_configured(),
        "subject_prefix": config.GMAIL_INBOX_SUBJECT_PREFIX,
        "attempted_today": store.attempted_today(local.date().isoformat()),
        "daily_max": config.GMAIL_INBOX_DAILY_MAX,
        "recent": store.recent(),
    }
