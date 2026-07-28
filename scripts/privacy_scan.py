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
    "github-token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "google-api-key": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "oauth-code": re.compile(r"\b4/0A[A-Za-z0-9_-]{20,}\b"),
    "oauth-client-id": re.compile(r"\b\d{6,}-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com\b"),
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
    for path in _git("ls-files").splitlines():
        file_path = ROOT / path
        try:
            text = file_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(_scan_text(path, text))
    return findings


def scan_history() -> list[str]:
    findings = []
    identities = _git("log", "--all", "--format=%H%x09%an%x09%ae%x09%cn%x09%ce")
    findings.extend(_scan_text("git-history-identities", identities))
    patches = _git("log", "--all", "--format=commit:%H", "--patch", "--no-ext-diff")
    findings.extend(_scan_text("git-history-patches", patches))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan all reachable commits")
    args = parser.parse_args(argv)
    findings = scan_current()
    if args.history:
        findings.extend(scan_history())
    protected = [
        path for path in ("AGENTS.md", "data/Body-Comp-HandOff/")
        if subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=ROOT,
            check=False,
        ).returncode == 0
    ]
    print(f"scanned tracked tree{' and history' if args.history else ''}")
    print(f"protected local paths: {', '.join(protected) if protected else 'none'}")
    for finding in findings[:100]:
        print(finding)
    if len(findings) > 100:
        print(f"... {len(findings) - 100} more findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
