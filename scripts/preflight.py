#!/usr/bin/env python3
"""Report whether a clone is ready for OAuth setup and daemon installation."""
from __future__ import annotations

import json
import os
import shutil
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


def collect() -> dict:
    dotenv = _dotenv()
    keys = set(dotenv) | set(os.environ)
    ai_enabled = os.environ.get(
        "FITLIT_AI_ENABLED",
        dotenv.get("FITLIT_AI_ENABLED", ""),
    )
    env_mode = stat.S_IMODE(ENV_PATH.stat().st_mode) if ENV_PATH.exists() else None
    providers = {
        name: bool(shutil.which(name)) for name in ("copilot", "codex", "claude")
    }
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
            "enabled": ai_enabled.lower() in ("1", "true", "yes", "on"),
            "providers_installed": providers,
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
    )
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
