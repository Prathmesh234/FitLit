#!/usr/bin/env python3
"""Report whether a clone is ready for OAuth setup and daemon installation."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
REQUIRED_HEALTH = (
    "GOOGLE_HEALTH_CLIENT_ID",
    "GOOGLE_HEALTH_CLIENT_SECRET",
    "GOOGLE_HEALTH_REFRESH_TOKEN",
)
WHATSAPP_AUTH = ROOT / "data" / "state" / "whatsapp-auth" / "creds.json"


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


def _node_status() -> dict:
    node = shutil.which("node")
    version = None
    supported = False
    if node:
        try:
            version = subprocess.run(
                [node, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip().lstrip("v")
            major = int(version.split(".", 1)[0])
            supported = major >= 20
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return {
        "path": node,
        "version": version,
        "supported": supported,
        "npm": shutil.which("npm"),
    }


def _whatsapp_paired() -> bool:
    if WHATSAPP_AUTH.is_symlink():
        return False
    try:
        value = json.loads(WHATSAPP_AUTH.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("registered") is True


def collect() -> dict:
    dotenv = _dotenv()
    keys = set(dotenv) | set(os.environ)
    env_mode = stat.S_IMODE(ENV_PATH.stat().st_mode) if ENV_PATH.exists() else None
    providers = {
        name: bool(shutil.which(name)) for name in ("copilot", "codex", "claude")
    }
    email_provider = _value(
        dotenv,
        "FITLIT_EMAIL_AGENT_PROVIDER",
        "copilot",
    ).lower()
    whatsapp_enabled = _enabled(dotenv, "FITLIT_WHATSAPP_ENABLED")
    trusted_number_configured = bool(_value(
        dotenv,
        "FITLIT_WHATSAPP_TRUSTED_USER_E164",
    ))
    whatsapp_paired = _whatsapp_paired()
    node_status = _node_status()
    whatsapp_dependencies = (
        ROOT
        / "whatsapp-bridge"
        / "node_modules"
        / "baileys"
        / "package.json"
    ).is_file()
    whatsapp_ready = all((
        trusted_number_configured,
        whatsapp_paired,
        node_status["supported"],
        whatsapp_dependencies,
        providers.get(email_provider, False),
    ))
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
                "provider": email_provider,
                "provider_installed": providers.get(email_provider, False),
                "model": _value(
                    dotenv,
                    "FITLIT_EMAIL_AGENT_COPILOT_MODEL",
                    "gpt-5.6-sol",
                ) if email_provider == "copilot" else None,
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
        "whatsapp": {
            "enabled": whatsapp_enabled,
            "ready": whatsapp_ready,
            "trusted_number_configured": trusted_number_configured,
            "paired": whatsapp_paired,
            "context_messages": int(_value(
                dotenv,
                "FITLIT_WHATSAPP_CONTEXT_MESSAGES",
                "5",
            )),
            "node": node_status,
            "dependencies_installed": whatsapp_dependencies,
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
            not result["whatsapp"]["enabled"]
            or result["whatsapp"]["ready"]
        )
    )
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
