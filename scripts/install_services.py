#!/usr/bin/env python3
"""Render portable systemd units and optionally install/start them."""
from __future__ import annotations

import argparse
import os
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
RENDERED = ROOT / "data" / "state" / "systemd"
SERVICE_NAMES = ("fitlit.service", "fitlit-gc.service", "fitlit-gmail.service")
POLL_SERVICE_NAME = "fitlit-gmail-poll.service"
TELEGRAM_SERVICE_NAME = "fitlit-telegram.service"
UNIT_NAMES = (
    *SERVICE_NAMES,
    POLL_SERVICE_NAME,
    TELEGRAM_SERVICE_NAME,
    "fitlit-gmail.timer",
)
LEGACY_UNIT_NAMES = (
    "fitlit-gmail-push.service",
    "fitlit-whatsapp.service",
)
TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
PRIVATE_PATHS = (
    ROOT / ".env",
    ROOT / "AGENTS.md",
    ROOT / "data",
)


def _service_user() -> tuple[str, Path]:
    name = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
    entry = pwd.getpwnam(name)
    return name, Path(entry.pw_dir)


def _find_uv(home: Path) -> Path:
    candidates = [
        Path(shutil.which("uv") or ""),
        home / ".local" / "bin" / "uv",
        home / ".cargo" / "bin" / "uv",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise RuntimeError("uv was not found; install it before installing services")


def _env_enabled(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        env_path = ROOT / ".env"
        if env_path.exists():
            for raw in env_path.read_text().splitlines():
                key, separator, candidate = raw.partition("=")
                if separator and key.strip() == name:
                    value = candidate.strip().strip('"').strip("'")
                    break
    return str(value or "").lower() in ("1", "true", "yes", "on")


def _env_value(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        env_path = ROOT / ".env"
        if env_path.exists():
            for raw in env_path.read_text().splitlines():
                key, separator, candidate = raw.partition("=")
                if separator and key.strip() == name:
                    value = candidate.strip().strip('"').strip("'")
                    break
    return str(value or "").strip()


def _env_configured(name: str) -> bool:
    return bool(_env_value(name))


def _telegram_ready() -> bool:
    token = _env_value("FITLIT_TELEGRAM_BOT_TOKEN")
    user_id = _env_value("FITLIT_TELEGRAM_TRUSTED_USER_ID")
    return bool(
        TELEGRAM_TOKEN_PATTERN.fullmatch(token)
        and user_id.isdecimal()
        and int(user_id) > 0
    )


def remove_legacy_whatsapp_state() -> bool:
    removed = False
    for path in (
        ROOT / "data" / "state" / "whatsapp-auth",
        ROOT / "data" / "state" / "whatsapp-ledger.json",
    ):
        if path.is_symlink():
            raise RuntimeError(f"refusing symlinked legacy private path: {path}")
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed = True
    return removed


def _harden_private_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise RuntimeError(f"refusing to chmod symlinked private path: {path}")
    if path.is_file():
        path.chmod(0o600)
        return
    path.chmod(0o700)
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        child.chmod(0o700 if child.is_dir() else 0o600)


def harden_private_paths() -> None:
    for path in PRIVATE_PATHS:
        _harden_private_path(path)


def render() -> list[Path]:
    user, home = _service_user()
    uv = _find_uv(home)
    path_entries = [
        str(uv.parent),
        str(home / ".local" / "bin"),
        str(home / ".cargo" / "bin"),
        str(home / ".opencode" / "bin"),
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
    ]
    values = {
        "__FITLIT_USER__": user,
        "__FITLIT_ROOT__": str(ROOT),
        "__FITLIT_PATH__": ":".join(dict.fromkeys(path_entries)),
        "__UV_PATH__": str(uv),
    }
    RENDERED.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name in UNIT_NAMES:
        text = (DEPLOY / name).read_text()
        for marker, value in values.items():
            text = text.replace(marker, value)
        if (
            "__FITLIT_" in text
            or "__UV_PATH__" in text
        ):
            raise RuntimeError(f"unresolved placeholder in {name}")
        output = RENDERED / name
        output.write_text(text)
        outputs.append(output)
    return outputs


def install(outputs: list[Path], *, start: bool) -> None:
    if os.geteuid() != 0:
        command = "sudo uv run python scripts/install_services.py --install"
        if start:
            command += " --start"
        raise RuntimeError(f"installation needs root; rerun: {command}")
    harden_private_paths()
    for name in LEGACY_UNIT_NAMES:
        load_state = subprocess.run(
            ["systemctl", "show", name, "--property=LoadState", "--value"],
            check=True,
            capture_output=True,
            text=True,
        )
        if load_state.stdout.strip() != "not-found":
            subprocess.run(
                ["systemctl", "disable", "--now", name],
                check=True,
            )
        legacy = Path("/etc/systemd/system") / name
        if legacy.exists():
            legacy.unlink()
    if remove_legacy_whatsapp_state():
        print(
            "warning: removed legacy local WhatsApp state; revoke the old "
            "FitLit linked device in the WhatsApp mobile app",
            file=sys.stderr,
        )
    for output in outputs:
        shutil.copyfile(output, Path("/etc/systemd/system") / output.name)
        os.chmod(Path("/etc/systemd/system") / output.name, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if start:
        enabled_units = [
            "fitlit.service",
            "fitlit-gc.service",
            "fitlit-gmail.timer",
        ]
        if _env_enabled("FITLIT_GMAIL_INBOX_ENABLED"):
            enabled_units.append(POLL_SERVICE_NAME)
        else:
            subprocess.run(
                ["systemctl", "disable", "--now", POLL_SERVICE_NAME],
                check=True,
            )
        if (
            _env_enabled("FITLIT_TELEGRAM_ENABLED")
            and _telegram_ready()
        ):
            enabled_units.append(TELEGRAM_SERVICE_NAME)
        else:
            subprocess.run(
                ["systemctl", "disable", "--now", TELEGRAM_SERVICE_NAME],
                check=True,
            )
            if _env_enabled("FITLIT_TELEGRAM_ENABLED"):
                print(
                    "warning: Telegram is enabled but its token or trusted "
                    "user is missing; leaving fitlit-telegram.service disabled",
                    file=sys.stderr,
                )
        subprocess.run(
            ["systemctl", "enable", "--now", *enabled_units],
            check=True,
        )
        subprocess.run(
            ["systemctl", "restart", *enabled_units],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="copy units into /etc/systemd/system")
    parser.add_argument("--start", action="store_true", help="enable and start long-running units")
    args = parser.parse_args(argv)
    try:
        outputs = render()
        if args.install:
            install(outputs, start=args.start)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
