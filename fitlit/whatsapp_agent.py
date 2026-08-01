"""Private stdin/stdout adapter from Baileys to FitLit's grounded agent."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sys
from datetime import datetime
from typing import Any

from fitlit import config, email_agent
from fitlit.journal import PACIFIC


class WhatsAppAgentError(RuntimeError):
    """Raised when the local bridge request is invalid."""


def _read_request() -> dict[str, Any]:
    payload = sys.stdin.buffer.read(config.WHATSAPP_AGENT_MAX_INPUT_BYTES + 1)
    if len(payload) > config.WHATSAPP_AGENT_MAX_INPUT_BYTES:
        raise WhatsAppAgentError("WhatsApp agent request exceeded the size limit")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WhatsAppAgentError("WhatsApp agent request was invalid") from exc
    if not isinstance(value, dict) or set(value) != {"turns", "now_ms"}:
        raise WhatsAppAgentError("WhatsApp agent request had an invalid shape")
    return value


def _timestamp(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 2**63 - 1
    ):
        raise WhatsAppAgentError("WhatsApp message timestamp was invalid")
    return value


def _turns(value: Any) -> list[email_agent.ThreadTurn]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= config.WHATSAPP_CONTEXT_MESSAGES
    ):
        raise WhatsAppAgentError("WhatsApp context was outside its bound")
    output = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "role",
            "content",
            "internal_date_ms",
        }:
            raise WhatsAppAgentError("WhatsApp context turn was invalid")
        role = item["role"]
        content = item["content"]
        if role not in {"user", "assistant"}:
            raise WhatsAppAgentError("WhatsApp context role was invalid")
        if (
            not isinstance(content, str)
            or not 1 <= len(content.strip()) <= config.WHATSAPP_BODY_MAX_CHARS
            or "\x00" in content
        ):
            raise WhatsAppAgentError("WhatsApp context text was invalid")
        output.append(email_agent.ThreadTurn(
            role=role,
            content=content.strip(),
            internal_date_ms=_timestamp(item["internal_date_ms"]),
        ))
    if output[-1].role != "user":
        raise WhatsAppAgentError("latest WhatsApp turn was not user-authored")
    return output


def _now(value: Any) -> datetime:
    timestamp_ms = _timestamp(value)
    seconds = timestamp_ms / 1000
    if not math.isfinite(seconds):
        raise WhatsAppAgentError("WhatsApp request time was invalid")
    try:
        return datetime.fromtimestamp(seconds, PACIFIC)
    except (OverflowError, OSError, ValueError) as exc:
        raise WhatsAppAgentError("WhatsApp request time was invalid") from exc


def draft_payload(value: dict[str, Any]) -> dict[str, Any]:
    turns = _turns(value["turns"])
    documents = []
    with email_agent.draft(turns, now=_now(value["now_ms"])) as reply:
        for attachment in reply.attachments:
            content = attachment.path.read_bytes()
            documents.append({
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
            })
        return {
            "ok": True,
            "text": reply.text,
            "provider": reply.provider,
            "topic": reply.topic,
            "evidence_count": len(reply.evidence_paths),
            "documents": documents,
        }


def main() -> int:
    try:
        result = draft_payload(_read_request())
    except (WhatsAppAgentError, email_agent.EmailAgentError, OSError):
        print(json.dumps({
            "ok": False,
            "error": "FitLit could not prepare a grounded WhatsApp reply",
        }))
        return 1
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
