"""Headless harness runner for personal tasks that need the live web.

The health side of FitLit answers from local SQLite and deliberately runs the
model with no tools. A personal task is the opposite case: a coffee shop's
hours, its current status, and how a room actually feels today are facts that
only exist on the open web, so this runner grants WebSearch and WebFetch and
then *proves* they were used before trusting the answer.

It stays as isolated as the health runners: no user or project settings, no
slash commands, no session persistence, a scrubbed environment, and a private
temporary working directory that is deleted when the run ends.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fitlit import ai_insights, config as fitlit_config
from fitlit.email_agent import _write_private  # noqa: PLC2701
from personal import config

log = logging.getLogger("fitlit.personal.agent")

WEB_TOOLS = ("WebSearch", "WebFetch")


class PersonalAgentError(RuntimeError):
    """The harness was unavailable, timed out, or returned unusable output."""


@dataclass(frozen=True)
class AgentRun:
    data: dict[str, Any]
    web_searches: int
    web_fetches: int
    model: str
    duration_ms: int
    cost_usd: float | None = None
    raw_keys: tuple[str, ...] = field(default_factory=tuple)


def _environment() -> dict[str, str]:
    """The health runner's scrubbed environment, minus push credentials.

    HOME survives the allowlist on purpose: the headless CLI reads its OAuth
    session from there, and `--bare` cannot be used because it refuses OAuth.
    """
    environment = ai_insights.minimal_environment()
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    environment.update({
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "1",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "0",
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
        "CI": "1",
    })
    return environment


def _settings(root: Path) -> Path:
    path = root / "settings.json"
    _write_private(
        path,
        json.dumps(
            {
                "hooks": {},
                "enabledPlugins": {},
                "enableAllProjectMcpServers": False,
                "includeCoAuthoredBy": False,
                "cleanupPeriodDays": 0,
            },
            separators=(",", ":"),
        ),
    )
    return path


def _command(
    root: Path,
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str,
    reasoning_effort: str,
    max_turns: int,
    system_prompt: str | None,
    budget_usd: str,
) -> list[str]:
    tools = ",".join(WEB_TOOLS)
    command = [
        "claude",
        "--print",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--permission-mode",
        "dontAsk",
        # --bare would refuse the OAuth session this daemon runs on. Loading no
        # setting sources keeps user, project, and local configuration out just
        # as effectively.
        "--setting-sources",
        "",
        "--settings",
        str(_settings(root)),
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns",
        str(max_turns),
        "--effort",
        reasoning_effort,
    ]
    if system_prompt:
        command.extend(["--append-system-prompt", system_prompt])
    if budget_usd:
        command.extend(["--max-budget-usd", budget_usd])
    if model:
        command.extend(["--model", model])
    return command


def _tool_counts(envelope: dict[str, Any]) -> tuple[int, int]:
    searches = fetches = 0
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        server = usage.get("server_tool_use")
        if isinstance(server, dict):
            searches += int(server.get("web_search_requests") or 0)
            fetches += int(server.get("web_fetch_requests") or 0)
    models = envelope.get("modelUsage")
    if isinstance(models, dict):
        for entry in models.values():
            if isinstance(entry, dict):
                searches += int(entry.get("webSearchRequests") or 0)
                fetches += int(entry.get("webFetchRequests") or 0)
    return searches, fetches


def _structured(envelope: dict[str, Any]) -> dict[str, Any]:
    value = envelope.get("structured_output")
    if not isinstance(value, dict):
        value = envelope.get("result")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersonalAgentError(
                "the harness result did not contain a JSON object"
            ) from exc
    if not isinstance(value, dict):
        raise PersonalAgentError("the harness did not return a JSON object")
    return value


def run(
    prompt: str,
    schema: dict[str, Any],
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    max_turns: int | None = None,
    timeout_seconds: int | None = None,
    max_output_chars: int | None = None,
    budget_usd: str | None = None,
) -> AgentRun:
    """Run one isolated, web-enabled harness call and return its object."""
    if fitlit_config.HARNESS != "claude":
        raise PersonalAgentError(
            "personal web tasks require HARNESS=claude; "
            f"the configured harness is {fitlit_config.HARNESS!r}"
        )
    if not shutil.which("claude"):
        raise PersonalAgentError("the claude CLI is not installed on PATH")

    timeout = timeout_seconds or config.COFFEE_TIMEOUT_SECONDS
    ceiling = max_output_chars or config.COFFEE_MAX_OUTPUT_CHARS
    with tempfile.TemporaryDirectory(prefix="fitlit-personal-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        command = _command(
            root,
            prompt,
            schema,
            model=model if model is not None else config.COFFEE_CLAUDE_MODEL,
            reasoning_effort=reasoning_effort or config.COFFEE_REASONING_EFFORT,
            max_turns=max_turns or config.COFFEE_MAX_TURNS,
            system_prompt=system_prompt,
            budget_usd=(
                config.COFFEE_MAX_BUDGET_USD if budget_usd is None else budget_usd
            ),
        )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=_environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonalAgentError(
                f"claude timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise PersonalAgentError(f"could not start claude: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[-1][:240]}" if detail else ""
        raise PersonalAgentError(f"claude exited {completed.returncode}{suffix}")
    raw = completed.stdout.strip()
    if not raw:
        raise PersonalAgentError("claude returned no output")
    if len(raw) > ceiling:
        raise PersonalAgentError("claude output exceeded the size limit")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PersonalAgentError("claude returned non-JSON output") from exc
    if not isinstance(envelope, dict):
        raise PersonalAgentError("claude returned an unexpected envelope")
    if envelope.get("is_error"):
        raise PersonalAgentError(
            f"claude reported an error: {str(envelope.get('result'))[:200]}"
        )

    data = _structured(envelope)
    searches, fetches = _tool_counts(envelope)
    cost = envelope.get("total_cost_usd")
    return AgentRun(
        data=data,
        web_searches=searches,
        web_fetches=fetches,
        model=str(envelope.get("model") or model or config.COFFEE_CLAUDE_MODEL),
        duration_ms=int(envelope.get("duration_ms") or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        raw_keys=tuple(sorted(data)),
    )
