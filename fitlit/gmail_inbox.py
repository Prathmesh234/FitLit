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

from fitlit import config, email_agent, gmail_auth, gmail_client
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
    followup: bool = False
    internal_date_ms: int = 0


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
                    retry_after TEXT,
                    intent TEXT,
                    rfc_message_id TEXT,
                    gmail_internal_date INTEGER NOT NULL DEFAULT 0
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
            if "intent" not in columns:
                connection.execute(
                    "ALTER TABLE inbound_messages ADD COLUMN intent TEXT"
                )
            if "rfc_message_id" not in columns:
                connection.execute(
                    "ALTER TABLE inbound_messages ADD COLUMN rfc_message_id TEXT"
                )
            if "gmail_internal_date" not in columns:
                connection.execute(
                    "ALTER TABLE inbound_messages ADD COLUMN "
                    "gmail_internal_date INTEGER NOT NULL DEFAULT 0"
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

    def primary_thread_id(self) -> str | None:
        """Return the first successfully established FitLit conversation."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id FROM inbound_messages "
                "WHERE status='sent' AND thread_id IS NOT NULL "
                "ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
        return str(row["thread_id"]) if row and row["thread_id"] else None

    def candidate_thread_id(self) -> str | None:
        """Pin discovery to the first root still awaiting a successful reply."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id FROM inbound_messages "
                "WHERE status IN ('sending','retryable') "
                "AND thread_id IS NOT NULL "
                "ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
        return str(row["thread_id"]) if row and row["thread_id"] else None

    def pending_deliveries(self, thread_id: str | None) -> list[dict]:
        if not thread_id:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT message_id,rfc_message_id FROM inbound_messages "
                "WHERE thread_id=? AND status='sending'",
                (thread_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_thread_ids(self) -> list[str]:
        """Return recorded threads whose delivery status needs reconciliation."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT thread_id FROM inbound_messages "
                "WHERE status='sending' AND thread_id IS NOT NULL "
                "ORDER BY created_at,rowid"
            ).fetchall()
        return list(dict.fromkeys(str(row["thread_id"]) for row in rows))

    def stale_pending_message_ids(
        self,
        thread_id: str,
        before: datetime,
    ) -> list[str]:
        """Return unreconciled sends older than the full provider/send window."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT message_id FROM inbound_messages AS message "
                "WHERE thread_id=? AND status='sending' "
                "AND COALESCE(("
                "SELECT attempted_at FROM inbound_attempts "
                "WHERE message_id=message.message_id "
                "ORDER BY id DESC LIMIT 1"
                "),created_at) < ? "
                "ORDER BY created_at,rowid",
                (thread_id, before.astimezone(timezone.utc).isoformat()),
            ).fetchall()
        return [str(row["message_id"]) for row in rows]

    def reconcile_delivery(self, message_id: str, reply_id: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE inbound_messages SET status='sent',processed_at=?,reply_id=?,"
                "error=NULL,retry_after=NULL WHERE message_id=? AND status='sending'",
                (timestamp, reply_id, message_id),
            )
            connection.execute(
                "UPDATE inbound_attempts SET status='sent',finished_at=?,reply_id=?,"
                "error=NULL WHERE id=("
                "SELECT MAX(id) FROM inbound_attempts WHERE message_id=?"
                ") AND status='sending'",
                (timestamp, reply_id, message_id),
            )
            connection.commit()

    def reserve(
        self,
        command: InboundCommand,
        now: datetime,
        *,
        intent: str,
    ) -> bool:
        if (
            isinstance(command.internal_date_ms, bool)
            or not isinstance(command.internal_date_ms, int)
            or not 0 <= command.internal_date_ms <= 2**63 - 1
        ):
            raise ValueError("Gmail internal date is outside the supported range")
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
                    "error=NULL,retry_after=NULL,intent=?,rfc_message_id=?,"
                    "gmail_internal_date=? "
                    "WHERE message_id=?",
                    (
                        day,
                        intent,
                        command.rfc_message_id,
                        command.internal_date_ms,
                        command.message_id,
                    ),
                )
            else:
                connection.execute(
                    "INSERT INTO inbound_messages("
                    "message_id,thread_id,pacific_date,status,created_at,intent,"
                    "rfc_message_id,gmail_internal_date) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        command.message_id,
                        command.thread_id,
                        day,
                        "sending",
                        timestamp,
                        intent,
                        command.rfc_message_id,
                        command.internal_date_ms,
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

    def supersede(
        self,
        message_id: str,
        thread_id: str | None,
        now: datetime,
    ) -> None:
        day = now.astimezone(PACIFIC).date().isoformat()
        timestamp = datetime.now(timezone.utc).isoformat()
        reason = "superseded by a newer message in the primary Gmail thread"
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO inbound_messages("
                "message_id,thread_id,pacific_date,status,created_at,processed_at,error"
                ") VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET "
                "status='ignored',processed_at=excluded.processed_at,"
                "error=excluded.error,retry_after=NULL",
                (
                    message_id,
                    thread_id,
                    day,
                    "ignored",
                    now.astimezone(timezone.utc).isoformat(),
                    timestamp,
                    reason,
                ),
            )
            connection.execute(
                "UPDATE inbound_attempts SET status='ignored',finished_at=?,error=? "
                "WHERE id=("
                "SELECT MAX(id) FROM inbound_attempts WHERE message_id=?"
                ") AND status IN ('sending','retryable')",
                (timestamp, reason, message_id),
            )
            connection.commit()

    def finish(
        self,
        message_id: str,
        *,
        reply_id: str | None = None,
        error: str | None = None,
        intent: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE inbound_messages SET status=?,processed_at=?,reply_id=?,"
                "error=?,intent=? "
                "WHERE message_id=?",
                (
                    "sent" if reply_id else "failed",
                    datetime.now(timezone.utc).isoformat(),
                    reply_id,
                    error,
                    intent,
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
    query: dict[str, str | int | list[str]] | None = None,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    url = f"{config.GMAIL_API_BASE}/users/me/{path.lstrip('/')}"
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
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
    lines = text.splitlines()
    kept = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        lowered = stripped.lower()
        lookahead = {
            candidate.strip().lower().split(":", 1)[0]
            for candidate in lines[index:index + 6]
            if ":" in candidate
        }
        standard_header_block = (
            lowered.startswith("from:")
            and len(lookahead & {"from", "sent", "date", "to", "subject"}) >= 3
        )
        if (
            stripped.startswith(">")
            or standard_header_block
            or stripped.startswith("________________________________")
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


def _parse_message(
    payload: dict,
    *,
    allow_followup: bool = False,
) -> tuple[InboundCommand | None, str | None]:
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
    is_command = bool(
        prefix
        and (
            subject == bare_prefix
            or subject.startswith(prefix)
        )
    )
    is_followup = bool(
        allow_followup
        and subject.lower().startswith("re:")
    )
    if not is_command and not is_followup:
        return None, "subject prefix did not match exactly"
    subject_question = (
        ""
        if subject == bare_prefix or is_followup
        else subject[len(prefix):].strip()
    )
    body_question = _bounded_body(body)
    question = "\n".join(
        value for value in (subject_question, body_question) if value
    ).strip()
    if not question:
        question = "help"
    raw_internal_date = payload.get("internalDate")
    try:
        internal_date_ms = int(raw_internal_date) if raw_internal_date else 0
    except (TypeError, ValueError):
        return None, "invalid Gmail internal date"
    if not 0 <= internal_date_ms <= 2**63 - 1:
        return None, "invalid Gmail internal date"
    return InboundCommand(
        message_id=message_id,
        thread_id=str(thread_id) if thread_id else None,
        rfc_message_id=headers.get("message-id") or None,
        references=headers.get("references") or None,
        subject=subject,
        question=question,
        followup=is_followup,
        internal_date_ms=internal_date_ms,
    ), None


def _list_message_ids() -> list[dict]:
    bare_prefix = config.GMAIL_INBOX_SUBJECT_PREFIX.rstrip(":").rstrip()
    query_subject = bare_prefix.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        f'in:sent from:me to:me newer_than:{config.GMAIL_INBOX_LOOKBACK_DAYS}d '
        f'subject:"{query_subject}"'
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


def _get_thread_message_ids(thread_id: str) -> list[str]:
    payload = _api_json(
        f"threads/{urllib.parse.quote(thread_id, safe='')}",
        query={"format": "minimal"},
    )
    return [
        str(message.get("id"))
        for message in payload.get("messages") or []
        if message.get("id")
    ]


def _thread_payloads(thread_id: str) -> list[dict]:
    message_ids = _get_thread_message_ids(thread_id)
    selected = message_ids[-config.EMAIL_AGENT_CONTEXT_MESSAGES:]
    payloads = [_get_message(message_id) for message_id in selected]
    return sorted(
        payloads,
        key=lambda payload: int(payload.get("internalDate") or 0),
    )


def _discover_thread(store: InboxStore) -> tuple[str | None, list[dict]]:
    """Find the oldest unprocessed root command, then scope to only its thread."""
    for summary in reversed(_list_message_ids()):
        message_id = str(summary.get("id") or "")
        if not message_id or store.has(message_id):
            continue
        payload = _get_message(message_id)
        command, _ = _parse_message(payload, allow_followup=False)
        if command and command.thread_id:
            return command.thread_id, _thread_payloads(command.thread_id)
    return None, []


def _conversation_turns(
    payloads: list[dict],
    *,
    allow_followup: bool,
) -> list[email_agent.ThreadTurn]:
    turns: list[email_agent.ThreadTurn] = []
    expected = config.GMAIL_TO.strip().lower()
    for payload in payloads[-config.EMAIL_AGENT_CONTEXT_MESSAGES:]:
        body = payload.get("payload") or {}
        headers = _headers(body)
        _, sender = parseaddr(headers.get("from", ""))
        recipients = {
            address.lower()
            for _, address in getaddresses([
                headers.get("to", ""),
                headers.get("delivered-to", ""),
            ])
            if address
        }
        if (
            "SENT" not in set(payload.get("labelIds") or [])
            or not expected
            or sender.lower() != expected
            or expected not in recipients
        ):
            continue
        raw_date = payload.get("internalDate")
        try:
            internal_date_ms = int(raw_date) if raw_date else 0
        except (TypeError, ValueError):
            continue
        if not 0 <= internal_date_ms <= 2**63 - 1:
            continue
        if headers.get("x-fitlit-notification") == "email-assistant":
            content = _bounded_body(body)
            if content:
                turns.append(email_agent.ThreadTurn(
                    role="assistant",
                    content=content,
                    internal_date_ms=internal_date_ms,
                ))
            continue
        command, _ = _parse_message(
            payload,
            allow_followup=allow_followup,
        )
        if command:
            turns.append(email_agent.ThreadTurn(
                role="user",
                content=command.question,
                internal_date_ms=internal_date_ms,
            ))
    return turns[-config.EMAIL_AGENT_CONTEXT_MESSAGES:]


def _get_thread_metadata(thread_id: str) -> dict:
    return _api_json(
        f"threads/{urllib.parse.quote(thread_id, safe='')}",
        query={
            "format": "metadata",
            "metadataHeaders": [
                "In-Reply-To",
                "X-FitLit-Notification",
                "X-FitLit-Source-Message-ID",
            ],
        },
    )


def _reconcile_thread_deliveries(
    thread_id: str,
    pending: list[dict],
    store: InboxStore,
) -> None:
    expected_by_rfc = {
        str(row["rfc_message_id"]): str(row["message_id"])
        for row in pending
        if row.get("rfc_message_id")
    }
    expected_ids = {
        str(row["message_id"])
        for row in pending
        if row.get("message_id")
    }
    if not expected_ids:
        return
    thread = _get_thread_metadata(thread_id)
    for value in thread.get("messages") or []:
        if "SENT" not in (value.get("labelIds") or []):
            continue
        headers = _headers(value.get("payload") or {})
        if headers.get("x-fitlit-notification") != "email-assistant":
            continue
        source_id = headers.get("x-fitlit-source-message-id", "")
        if source_id not in expected_ids:
            source_id = expected_by_rfc.get(
                headers.get("in-reply-to", ""),
                "",
            )
        reply_id = str(value.get("id") or "")
        if source_id and reply_id:
            store.reconcile_delivery(source_id, reply_id)


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
        primary_thread = store.primary_thread_id()
        pending_threads = (
            [primary_thread] if primary_thread else store.pending_thread_ids()
        )
    except sqlite3.Error as exc:
        result["status"] = "ledger-error"
        result["failed"].append(str(exc))
        return result

    try:
        stale_before = local.astimezone(timezone.utc) - timedelta(
            seconds=(
                2 * config.EMAIL_AGENT_TIMEOUT_SECONDS
                + 2 * config.REQUEST_TIMEOUT
                + 300
            )
        )
        for thread_id in pending_threads:
            pending = store.pending_deliveries(thread_id)
            if pending:
                _reconcile_thread_deliveries(thread_id, pending, store)
            for message_id in store.stale_pending_message_ids(
                thread_id,
                stale_before,
            ):
                store.retry(
                    message_id,
                    "interrupted delivery attempt was not found in Gmail",
                )
                result["transient_failure"] = True
                result["failed"].append({
                    "message_id": message_id,
                    "error": "interrupted delivery attempt scheduled for retry",
                })
    except (GmailInboxError, gmail_auth.GmailAuthError) as exc:
        result["failed"].append(str(exc))
        result["transient_failure"] = True
        return result
    except sqlite3.Error as exc:
        result["status"] = "ledger-error"
        result["failed"].append(str(exc))
        return result

    try:
        primary_thread = store.primary_thread_id()
        if primary_thread:
            active_thread = primary_thread
            payloads = _thread_payloads(primary_thread)
        else:
            candidate_thread = store.candidate_thread_id()
            if candidate_thread:
                active_thread = candidate_thread
                payloads = _thread_payloads(candidate_thread)
            else:
                active_thread, payloads = _discover_thread(store)
    except (GmailInboxError, gmail_auth.GmailAuthError) as exc:
        result["status"] = "auth-or-api-error"
        result["failed"].append(str(exc))
        return result
    except sqlite3.Error as exc:
        result["status"] = "ledger-error"
        result["failed"].append(str(exc))
        return result

    if not active_thread or not payloads:
        result["attempted_today"] = store.attempted_today(
            local.date().isoformat()
        )
        result["daily_max"] = config.GMAIL_INBOX_DAILY_MAX
        return result

    commands: list[InboundCommand] = []
    for payload in payloads:
        try:
            command, reason = _parse_message(payload, allow_followup=True)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            result["failed"].append({
                "message_id": str(payload.get("id") or ""),
                "error": f"malformed Gmail message: {exc}",
            })
            continue
        if command and command.thread_id == active_thread:
            commands.append(command)
            continue
        message_id = str(payload.get("id") or "")
        if message_id and not dry_run:
            try:
                if not store.has(message_id):
                    store.ignore(
                        message_id,
                        str(payload.get("threadId") or "") or None,
                        local,
                        reason or "message was not an accepted command",
                    )
            except sqlite3.Error as exc:
                result["status"] = "ledger-error"
                result["failed"].append(str(exc))
                return result

    if not commands:
        result["attempted_today"] = store.attempted_today(
            local.date().isoformat()
        )
        result["daily_max"] = config.GMAIL_INBOX_DAILY_MAX
        return result

    command = commands[-1]
    if not dry_run:
        try:
            for older in commands[:-1]:
                if not store.has(older.message_id):
                    store.supersede(
                        older.message_id,
                        older.thread_id,
                        local,
                    )
                    result["ignored"].append({
                        "message_id": older.message_id,
                        "reason": "superseded by the latest thread message",
                    })
            if store.has(command.message_id):
                result["skipped"].append(command.message_id)
                result["attempted_today"] = store.attempted_today(
                    local.date().isoformat()
                )
                result["daily_max"] = config.GMAIL_INBOX_DAILY_MAX
                return result
        except sqlite3.Error as exc:
            result["status"] = "ledger-error"
            result["failed"].append(str(exc))
            return result

    try:
        attempted = store.attempted_today(local.date().isoformat())
    except sqlite3.Error as exc:
        result["status"] = "ledger-error"
        result["failed"].append(str(exc))
        return result
    if attempted >= config.GMAIL_INBOX_DAILY_MAX:
        result["skipped"].append(command.message_id)
        result["attempted_today"] = attempted
        result["daily_max"] = config.GMAIL_INBOX_DAILY_MAX
        return result

    turns = _conversation_turns(payloads, allow_followup=True)
    while turns and turns[-1].role != "user":
        turns.pop()
    if not turns:
        result["failed"].append({
            "message_id": command.message_id,
            "error": "the primary thread had no bounded user context",
        })
        return result

    if not dry_run:
        try:
            reserved = store.reserve(command, local, intent="agent")
        except (ValueError, sqlite3.Error) as exc:
            if isinstance(exc, sqlite3.Error):
                result["status"] = "ledger-error"
                result["failed"].append(str(exc))
                return result
            result["failed"].append({
                "message_id": command.message_id,
                "error": str(exc),
            })
            return result
        if not reserved:
            result["skipped"].append(command.message_id)
            return result

    references = " ".join(
        value for value in (command.references, command.rfc_message_id) if value
    ) or None
    reply_subject = (
        command.subject
        if command.subject.lower().startswith("re:")
        else f"Re: {command.subject}"
    )
    try:
        with email_agent.draft(turns, now=local) as rendered:
            if dry_run:
                result["preview"].append({
                    "message_id": command.message_id,
                    "topic": rendered.topic,
                    "provider": rendered.provider,
                    "context_messages": len(turns),
                    "artifacts": [
                        attachment.filename
                        for attachment in rendered.attachments
                    ],
                    "subject": reply_subject,
                })
            else:
                reply_id = gmail_client.send(
                    reply_subject,
                    rendered.text,
                    rendered.html,
                    thread_id=command.thread_id,
                    in_reply_to=command.rfc_message_id,
                    references=references,
                    category="email-assistant",
                    attachments=rendered.attachments,
                    source_message_id=command.message_id,
                )
    except email_agent.EmailAgentError as exc:
        if not dry_run:
            store.retry(command.message_id, str(exc))
        result["transient_failure"] = True
        result["failed"].append({
            "message_id": command.message_id,
            "error": str(exc),
        })
        return result
    except gmail_auth.GmailAuthError as exc:
        store.retry(command.message_id, str(exc))
        result["transient_failure"] = True
        result["failed"].append({
            "message_id": command.message_id,
            "error": str(exc),
        })
        return result
    except gmail_client.GmailSendError as exc:
        if exc.delivery_uncertain:
            result["transient_failure"] = True
        elif exc.retryable:
            store.retry(command.message_id, str(exc))
            result["transient_failure"] = True
        else:
            store.finish(command.message_id, error=str(exc))
        result["failed"].append({
            "message_id": command.message_id,
            "error": str(exc),
        })
        return result

    if not dry_run:
        store.finish(
            command.message_id,
            reply_id=reply_id,
            intent=rendered.topic,
        )
        result["sent"].append({
            "message_id": command.message_id,
            "reply_id": reply_id,
            "topic": rendered.topic,
            "provider": rendered.provider,
            "context_messages": len(turns),
            "artifacts": [
                attachment.filename
                for attachment in rendered.attachments
            ],
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
