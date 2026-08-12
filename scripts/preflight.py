#!/usr/bin/env python3
"""Report whether a clone is ready for OAuth setup and daemon installation."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
REQUIRED_HEALTH = (
    "GOOGLE_HEALTH_CLIENT_ID",
    "GOOGLE_HEALTH_CLIENT_SECRET",
    "GOOGLE_HEALTH_REFRESH_TOKEN",
)
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
# The listener now refuses to start when either value is malformed, so the
# preflight checks exactly what fitlit/email_agent.py accepts.
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
HARNESSES = ("copilot", "codex", "claude", "opencode")


def _dotenv() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values = {}
    for line in ENV_PATH.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def _value(dotenv: dict[str, str], name: str, default: str = "") -> str:
    return os.environ.get(name, dotenv.get(name, default))


def _enabled(dotenv: dict[str, str], name: str) -> bool:
    return _value(dotenv, name).lower() in ("1", "true", "yes", "on")


def collect() -> dict:
    dotenv = _dotenv()
    keys = set(dotenv) | set(os.environ)
    env_mode = stat.S_IMODE(ENV_PATH.stat().st_mode) if ENV_PATH.exists() else None
    providers = {
        name: bool(shutil.which(name)) for name in HARNESSES
    }
    harness = _value(
        dotenv,
        "HARNESS",
        _value(dotenv, "FITLIT_EMAIL_AGENT_PROVIDER", "copilot"),
    ).lower()
    telegram_enabled = _enabled(dotenv, "FITLIT_TELEGRAM_ENABLED")
    telegram_token = _value(
        dotenv,
        "FITLIT_TELEGRAM_BOT_TOKEN",
    )
    telegram_user = _value(
        dotenv,
        "FITLIT_TELEGRAM_TRUSTED_USER_ID",
    )
    email_model_names = {
        "copilot": "FITLIT_EMAIL_AGENT_COPILOT_MODEL",
        "codex": "FITLIT_EMAIL_AGENT_CODEX_MODEL",
        "claude": "FITLIT_EMAIL_AGENT_CLAUDE_MODEL",
        "opencode": "FITLIT_EMAIL_AGENT_OPENCODE_MODEL",
    }
    telegram_model_names = {
        "copilot": "FITLIT_TELEGRAM_COPILOT_MODEL",
        "codex": "FITLIT_TELEGRAM_CODEX_MODEL",
        "claude": "FITLIT_TELEGRAM_CLAUDE_MODEL",
        "opencode": "FITLIT_TELEGRAM_OPENCODE_MODEL",
    }
    email_defaults = {"copilot": "gpt-5.6-sol"}
    telegram_defaults = {"copilot": "gpt-5.6-terra"}
    email_model = _value(
        dotenv,
        email_model_names.get(harness, ""),
        email_defaults.get(harness, ""),
    ) if harness in HARNESSES else None
    telegram_model = _value(
        dotenv,
        telegram_model_names.get(harness, ""),
        telegram_defaults.get(harness, email_model or ""),
    ) if harness in HARNESSES else None
    telegram_effort = _value(
        dotenv,
        "FITLIT_TELEGRAM_REASONING_EFFORT",
        "high",
    ).lower()
    telegram_model_valid = (
        telegram_model is None or bool(MODEL_PATTERN.fullmatch(telegram_model))
    )
    telegram_effort_valid = telegram_effort in REASONING_EFFORTS
    telegram_token_configured = bool(telegram_token)
    telegram_token_valid = bool(TELEGRAM_TOKEN_PATTERN.fullmatch(telegram_token))
    telegram_user_configured = bool(telegram_user)
    telegram_user_valid = (
        telegram_user.isdecimal() and int(telegram_user) > 0
    )
    telegram_ready = all((
        telegram_token_valid,
        telegram_user_valid,
        telegram_model_valid,
        telegram_effort_valid,
        providers.get(harness, False),
    ))
    transcript_path = ROOT / "data" / "state" / "telegram-conversations.sqlite3"
    memory_indexed = False
    if transcript_path.is_file() and not transcript_path.is_symlink():
        try:
            with sqlite3.connect(
                f"file:{transcript_path.resolve()}?mode=ro",
                uri=True,
            ) as connection:
                memory_indexed = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'turns_fts'
                    """
                ).fetchone() is not None
        except sqlite3.Error:
            memory_indexed = False
    return {
        "python": {
            "version": ".".join(map(str, sys.version_info[:3])),
            "supported": sys.version_info >= (3, 11),
        },
        "uv": shutil.which("uv"),
        "env": {
            "exists": ENV_PATH.exists(),
            "private_permissions": env_mode is None or env_mode & 0o077 == 0,
            "health_oauth_names_present": all(name in keys for name in REQUIRED_HEALTH),
            "gmail_names_present": all(
                name in keys for name in ("GMAIL_REFRESH_TOKEN", "FITLIT_GMAIL_TO")
            ),
        },
        "ai": {
            "enabled": _enabled(dotenv, "FITLIT_AI_ENABLED"),
            "harness": harness,
            "harness_supported": harness in HARNESSES,
            "providers_installed": providers,
        },
        "gmail_poll": {
            "enabled": _enabled(dotenv, "FITLIT_GMAIL_INBOX_ENABLED"),
            "interval_seconds": int(_value(
                dotenv,
                "FITLIT_GMAIL_INBOX_POLL_SECONDS",
                "5",
            )),
            "email_agent": {
                "harness": harness,
                "harness_supported": harness in HARNESSES,
                "provider_installed": providers.get(harness, False),
                "model": email_model or None,
                "reasoning_effort": _value(
                    dotenv,
                    "FITLIT_EMAIL_AGENT_REASONING_EFFORT",
                    "high",
                ),
                "context_messages": int(_value(
                    dotenv,
                    "FITLIT_EMAIL_AGENT_CONTEXT_MESSAGES",
                    "5",
                )),
            },
        },
        "telegram": {
            "enabled": telegram_enabled,
            "ready": telegram_ready,
            "bot_token_configured": telegram_token_configured,
            "bot_token_format_valid": telegram_token_valid,
            "trusted_user_configured": telegram_user_configured,
            "trusted_user_valid": telegram_user_valid,
            "harness": harness,
            "harness_supported": harness in HARNESSES,
            "provider_installed": providers.get(harness, False),
            "context_policy": "complete-active-conversation",
            "transcript_path": "data/state/telegram-conversations.sqlite3",
            "memory_search_tool": "search_transcript_memory",
            "memory_indexed": memory_indexed,
            "lock_path": "data/state/telegram-service.lock",
            "model": telegram_model,
            "model_valid": telegram_model_valid,
            "reasoning_effort": telegram_effort,
            "effort_valid": telegram_effort_valid,
            "long_poll_seconds": int(_value(
                dotenv,
                "FITLIT_TELEGRAM_POLL_TIMEOUT_SECONDS",
                "5",
            )),
        },
        "systemd": bool(shutil.which("systemctl")),
        "repository": str(ROOT),
    }


def main() -> int:
    result = collect()
    print(json.dumps(result, indent=2))
    required_ok = (
        result["python"]["supported"]
        and bool(result["uv"])
        and result["env"]["exists"]
        and result["env"]["private_permissions"]
        and (
            not result["telegram"]["enabled"]
            or result["telegram"]["ready"]
        )
        and result["ai"]["harness_supported"]
    )
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
