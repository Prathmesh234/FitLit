"""Isolated, provider-neutral AI observations for deterministic health reports."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fitlit import config

log = logging.getLogger("fitlit.ai_insights")
PROVIDERS = ("copilot", "codex", "claude")
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9 .,%:+/_()\-]{1,80}$")
_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_VERTEX_REGION",
    "AZURE_RESOURCE_NAME",
    "AZURE_API_KEY",
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "minLength": 1, "maxLength": 80},
        "observations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1, "maxLength": 140},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["headline", "observations", "confidence"],
    "additionalProperties": False,
}


class AIInsightError(RuntimeError):
    """A provider was unavailable or returned an invalid response."""


@dataclass(frozen=True)
class AIInsight:
    headline: str
    observations: tuple[str, ...]
    confidence: float
    provider: str


def sanitize_payload(payload: dict[str, Any]) -> dict[str, int | float | str | bool | None]:
    """Allow only shallow, bounded metrics; reject identifiers and nested data."""
    clean: dict[str, int | float | str | bool | None] = {}
    if len(payload) > 30:
        raise AIInsightError("AI payload has too many fields")
    for key, value in payload.items():
        if not isinstance(key, str) or not _SAFE_KEY.fullmatch(key):
            raise AIInsightError(f"unsafe AI payload key: {key!r}")
        if value is None or isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)) or not (-1_000_000 <= float(value) <= 1_000_000):
                raise AIInsightError(f"AI payload value is out of range: {key}")
            clean[key] = value
        elif isinstance(value, str) and _SAFE_TEXT.fullmatch(value):
            clean[key] = value
        else:
            raise AIInsightError(f"unsafe AI payload value: {key}")
    return clean


def minimal_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    source = source or os.environ
    return {key: value for key, value in source.items() if key in _ENV_ALLOWLIST}


def _prompt(payload: dict[str, Any]) -> str:
    metrics = json.dumps(sanitize_payload(payload), sort_keys=True, separators=(",", ":"))
    schema = json.dumps(OUTPUT_SCHEMA, separators=(",", ":"))
    return (
        "You analyze a small, sanitized wearable-health metric object. "
        f"Return only JSON matching this schema: {schema}. Use numbers from the object, "
        "make no diagnosis, do not invent baselines, and do not give medical directives. "
        "Write one short headline and 1-3 concise observations. "
        f"Metrics: {metrics}"
    )


def _validate(value: Any, provider: str) -> AIInsight:
    if not isinstance(value, dict) or set(value) != {"headline", "observations", "confidence"}:
        raise AIInsightError(f"{provider} returned an invalid object")
    headline = value["headline"]
    observations = value["observations"]
    confidence = value["confidence"]
    if not isinstance(headline, str) or not 1 <= len(headline.strip()) <= 80:
        raise AIInsightError(f"{provider} returned an invalid headline")
    if (
        not isinstance(observations, list)
        or not 1 <= len(observations) <= 3
        or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 140 for item in observations)
    ):
        raise AIInsightError(f"{provider} returned invalid observations")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise AIInsightError(f"{provider} returned invalid confidence")
    confidence = float(confidence)
    if not 0 <= confidence <= 1:
        raise AIInsightError(f"{provider} returned out-of-range confidence")
    return AIInsight(
        headline=headline.strip(),
        observations=tuple(item.strip() for item in observations),
        confidence=confidence,
        provider=provider,
    )


def parse_response(raw: str, provider: str) -> AIInsight:
    if len(raw) > config.AI_MAX_OUTPUT_CHARS:
        raise AIInsightError(f"{provider} output exceeded the size limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIInsightError(f"{provider} returned non-JSON output") from exc
    if provider == "claude" and isinstance(value, dict):
        value = value.get("structured_output", value.get("result", value))
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise AIInsightError("claude result did not contain JSON") from exc
    return _validate(value, provider)


def _run(command: list[str], cwd: Path, *, output_path: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=minimal_environment(),
            capture_output=True,
            text=True,
            timeout=config.AI_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIInsightError(f"{command[0]} timed out") from exc
    except OSError as exc:
        raise AIInsightError(f"could not start {command[0]}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[-1][:240]}" if detail else ""
        raise AIInsightError(f"{command[0]} exited {completed.returncode}{suffix}")
    raw = output_path.read_text() if output_path and output_path.exists() else completed.stdout
    if not raw.strip():
        raise AIInsightError(f"{command[0]} returned no output")
    return raw.strip()


def _copilot(prompt: str, cwd: Path) -> str:
    command = [
        "copilot",
        "--prompt",
        prompt,
        "--silent",
        "--no-ask-user",
        "--no-custom-instructions",
        "--disable-builtin-mcps",
        "--no-remote",
        "--no-remote-export",
        "--no-auto-update",
        "--available-tools=",
        "--log-dir",
        str(cwd / "logs"),
    ]
    if config.AI_COPILOT_MODEL:
        command.extend(["--model", config.AI_COPILOT_MODEL])
    return _run(command, cwd)


def _codex(prompt: str, cwd: Path) -> str:
    schema_path = cwd / "schema.json"
    output_path = cwd / "result.json"
    schema_path.write_text(json.dumps(OUTPUT_SCHEMA))
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    if config.AI_CODEX_MODEL:
        command.extend(["--model", config.AI_CODEX_MODEL])
    command.append(prompt)
    return _run(command, cwd, output_path=output_path)


def _claude(prompt: str, cwd: Path) -> str:
    command = [
        "claude",
        "--bare",
        "--print",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(OUTPUT_SCHEMA, separators=(",", ":")),
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-budget-usd",
        config.AI_CLAUDE_MAX_BUDGET_USD,
    ]
    if config.AI_CLAUDE_MODEL:
        command.extend(["--model", config.AI_CLAUDE_MODEL])
    return _run(command, cwd)


_ADAPTERS = {"copilot": _copilot, "codex": _codex, "claude": _claude}


def available_providers() -> list[str]:
    return [provider for provider in PROVIDERS if shutil.which(provider)]


def generate(payload: dict[str, Any]) -> AIInsight | None:
    """Try configured providers in order; return None for deterministic fallback."""
    if not config.AI_ENABLED:
        return None
    providers = (
        config.AI_PROVIDER_ORDER
        if config.AI_PROVIDER == "auto"
        else (config.AI_PROVIDER,)
    )
    try:
        prompt = _prompt(payload)
    except AIInsightError as exc:
        log.warning("AI payload rejected: %s", exc)
        return None
    for provider in providers:
        if provider not in _ADAPTERS or not shutil.which(provider):
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="fitlit-ai-") as directory:
                raw = _ADAPTERS[provider](prompt, Path(directory))
            return parse_response(raw, provider)
        except AIInsightError as exc:
            log.warning("AI provider %s unavailable: %s", provider, exc)
            continue
    return None
