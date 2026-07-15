#!/usr/bin/env python3
"""Render portable systemd units and optionally install/start them."""
from __future__ import annotations

import argparse
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
RENDERED = ROOT / "data" / "state" / "systemd"
SERVICE_NAMES = ("fitlit.service", "fitlit-gc.service", "fitlit-gmail.service")
UNIT_NAMES = (*SERVICE_NAMES, "fitlit-gmail.timer")


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


def render() -> list[Path]:
    user, home = _service_user()
    uv = _find_uv(home)
    path_entries = [
        str(uv.parent),
        str(home / ".local" / "bin"),
        str(home / ".cargo" / "bin"),
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
        if "__FITLIT_" in text or "__UV_PATH__" in text:
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
    for output in outputs:
        shutil.copyfile(output, Path("/etc/systemd/system") / output.name)
        os.chmod(Path("/etc/systemd/system") / output.name, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if start:
        subprocess.run(
            ["systemctl", "enable", "--now", "fitlit.service", "fitlit-gc.service",
             "fitlit-gmail.timer"],
            check=True,
        )
        subprocess.run(
            ["systemctl", "restart", "fitlit.service", "fitlit-gc.service",
             "fitlit-gmail.timer"],
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
