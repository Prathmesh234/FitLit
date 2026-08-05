#!/usr/bin/env python3
"""Scan public Git surfaces for likely secrets, identifiers, and host paths."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "google-api-key": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "google-access-token": re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
    "google-client-secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b"),
    "google-refresh-token": re.compile(r"\b1//[A-Za-z0-9_-]{20,}\b"),
    "oauth-code": re.compile(r"\b4/0A[A-Za-z0-9_-]{20,}\b"),
    "oauth-client-id": re.compile(r"\b\d{6,}-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com\b"),
    "telegram-bot-token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    "e164-phone": re.compile(r"(?<!\d)\+[1-9]\d{9,14}\b"),
    "absolute-home": re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+/"),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
}
SAFE_EMAILS = {
    "person@example.com",
    "you@gmail.com",
    "you@example.com",
    "noreply@anthropic.com",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout


def _scan_text(label: str, text: str) -> list[str]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            matches = list(pattern.finditer(line))
            if kind == "email":
                matches = [
                    match for match in matches
                    if match.group(0).lower() not in SAFE_EMAILS
                    and not match.group(0).lower().endswith("@users.noreply.github.com")
                ]
            if matches:
                findings.append(f"{label}:{line_number}: {kind}")
    return findings


def scan_current() -> list[str]:
    findings = []
    for path in _git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
    ).splitlines():
        file_path = ROOT / path
        try:
            text = file_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_scan_text(path, text))
    return findings


def scan_history() -> tuple[list[str], list[str]]:
    identities = _git("log", "--all", "--format=%H%x09%an%x09%ae%x09%cn%x09%ce")
    patches = _git("log", "--all", "--format=commit:%H", "--patch", "--no-ext-diff")
    return (
        _scan_text("git-history-patches", patches),
        _scan_text("git-history-identities", identities),
    )


def _print_category(name: str, findings: list[str], limit: int = 100) -> None:
    print(f"{name}: {len(findings)} finding(s)")
    for finding in findings[:limit]:
        print(finding)
    if len(findings) > limit:
        print(f"... {len(findings) - limit} more {name} findings")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan all reachable commits")
    args = parser.parse_args(argv)
    current_findings = scan_current()
    history_findings: list[str] = []
    identity_findings: list[str] = []
    if args.history:
        history_findings, identity_findings = scan_history()
    protected = [
        path for path in ("AGENTS.md", "data/Body-Comp-HandOff/")
        if subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=ROOT,
            check=False,
        ).returncode == 0
    ]
    print(f"scanned public working tree{' and history' if args.history else ''}")
    print(f"protected local paths: {', '.join(protected) if protected else 'none'}")
    _print_category("current-content", current_findings)
    if args.history:
        _print_category("history-content", history_findings)
        _print_category("history-identities", identity_findings)
    return 1 if current_findings or history_findings or identity_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
