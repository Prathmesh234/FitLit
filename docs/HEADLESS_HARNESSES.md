# Headless harnesses

FitLit sends every model-backed workflow through one global selector:

```ini
HARNESS=claude
```

Supported values are `claude`, `codex`, `copilot`, and `opencode`. This applies
to Telegram replies, Gmail command replies, artifact drafting, and optional
proactive-report enrichment. Claude Code is the default. Restart the Telegram,
Gmail poll, and Gmail timer services after changing it.

Harness-specific model identifiers remain separate because their formats are
not interchangeable:

```ini
FITLIT_EMAIL_AGENT_CLAUDE_MODEL=claude-sonnet-5
FITLIT_EMAIL_AGENT_CODEX_MODEL=
FITLIT_EMAIL_AGENT_COPILOT_MODEL=gpt-5.6-sol
FITLIT_EMAIL_AGENT_OPENCODE_MODEL=

FITLIT_TELEGRAM_CLAUDE_MODEL=claude-sonnet-5
FITLIT_TELEGRAM_CODEX_MODEL=
FITLIT_TELEGRAM_COPILOT_MODEL=gpt-5.6-terra
FITLIT_TELEGRAM_OPENCODE_MODEL=
```

Blank values use the harness's configured default model. For repeatable daemon
behavior, set an explicit model before selecting that harness.

## Runtime contract

All four adapters:

- run non-interactively from a private temporary directory;
- receive the same bounded `request.json` and validated output schema;
- receive a minimal environment without Gmail or Google Health secrets;
- have no file-write, unrestricted shell, or public-network tool;
- can call the read-only `search_transcript_memory` MCP tool;
- may use at most two first-level native subagents for genuinely complex work;
- must wait for delegated work and produce one final validated response;
- lose temporary request, session, log, MCP, and artifact files after delivery.

The system prompt tells the model to use delegation only for complex,
independent analysis. A simple chat question should remain one model turn.
Subagents inherit the same rule that messages and transcript-memory results are
untrusted data, never instructions.

Configure the shared limits:

```ini
FITLIT_EMAIL_AGENT_MAX_TURNS=10
FITLIT_EMAIL_AGENT_MAX_SUBAGENTS=2
FITLIT_EMAIL_AGENT_TIMEOUT_SECONDS=180
```

## Transcript memory

Telegram turns are indexed locally with SQLite FTS5. The harness can search
active and archived conversations through:

```text
search_transcript_memory(query, limit=5, conversation_id=None)
```

The tool returns the matching turn, nearby context, and Pacific timestamps. It
is read-only, accepts no database path from the model, exposes no Telegram user
ID or credentials, and labels stored text as untrusted historical content.
Normal self-contained questions should not invoke it.

Direct operator search:

```bash
uv run python -m fitlit.transcript_memory search "Ruth Howell"
```

The MCP server is started privately per harness invocation:

```bash
uv run python -m fitlit.transcript_memory mcp
```

## Claude Code (default)

Verified production baseline: Claude Code `2.1.247`.

```bash
claude --version
claude login
```

FitLit uses `claude --print`, `dontAsk` permissions, explicit tools and MCP
configuration, JSON Schema output, no session persistence, and the current
`Agent` subagent tool.

`--bare` is deliberately not used. Its Anthropic auth is strictly
`ANTHROPIC_API_KEY` or an `apiKeyHelper`, so it silently ignores the
subscription OAuth session that `claude login` creates. FitLit instead passes
`--setting-sources ""` plus a private generated `--settings` file, which keeps
user, project, and local settings, hooks, and plugins out of the daemon run
while leaving normal authentication intact. `ANTHROPIC_API_KEY` and
Bedrock/Vertex/Foundry credentials still work if preferred.

`--max-budget-usd` is omitted unless a positive cap is configured:

```ini
FITLIT_EMAIL_AGENT_CLAUDE_MAX_BUDGET_USD=
FITLIT_AI_CLAUDE_MAX_BUDGET_USD=
```

Claude reports cost at list price even on a subscription session, where no
per-token charge is actually incurred. A cap therefore aborts the run — the CLI
exits non-zero and the reply fails — without preventing real spend. Blank is
the default; set a positive amount only when billing through an API key.

Useful stable features:

- mature custom and background subagents;
- per-agent model, effort, tools, turn limits, and prompts;
- structured JSON and streaming event output;
- explicit budget caps;
- hooks, skills, worktrees, and native availability fallback chains.

Agent teams, channels, and advisor mode remain experimental and are not part of
the FitLit daemon contract.

## OpenAI Codex CLI

Verified production baseline: Codex CLI `0.147.0`.

```bash
codex --version
codex login status
```

FitLit uses `codex exec --strict-config --ephemeral --sandbox read-only`, JSON
Schema output, a private `CODEX_HOME`, required transcript-memory MCP
configuration, and stable multi-agent V1 with depth one. The service user may
authenticate through `CODEX_API_KEY`, `OPENAI_API_KEY`,
`CODEX_ACCESS_TOKEN`, or its owner-only `~/.codex/auth.json`.

Useful stable features:

- schema-constrained final output plus JSONL execution events;
- native subagent concurrency controls;
- resumable/forkable threads through the SDK;
- strong OS sandbox policy;
- lifecycle hooks and OpenTelemetry.

FitLit keeps multi-agent V2 off until it has equivalent integration coverage.

## GitHub Copilot CLI

Verified production baseline: Copilot CLI `1.0.79`.

```bash
copilot --version
copilot login
```

FitLit uses `copilot --prompt ... --silent --stream off`, an isolated
`COPILOT_HOME`, explicit MCP configuration, and only `view`, transcript memory,
and native subagent coordination tools. Prompt-mode task waiting is bounded by
the FitLit provider timeout.

Authentication may instead use `COPILOT_GITHUB_TOKEN`. Do not reuse a
repository push token unless it is deliberately scoped for Copilot Requests.

Useful stable features:

- native `task` subagents and custom agents;
- JSONL event output for external observability;
- hooks for auditing subagent and tool lifecycle;
- skills for reusable workflows;
- explicit session resume.

Copilot Memory is public preview and remains disabled for daemon calls. ACP is
also preview; FitLit currently uses stable one-shot prompt mode.

## OpenCode

Verified production baseline: OpenCode `1.18.16`.

```bash
opencode --version
opencode auth list
```

FitLit uses `opencode run --format json` with a generated private
`opencode.json`. The config denies all tools by default, allows only request
reading, transcript memory, and one foreground `fitlit-analyst` subagent, sets
nesting depth to one, disables sharing and auto-update, and uses isolated XDG
config/cache/state directories.

Installers commonly place the binary in `~/.opencode/bin`; generated systemd
units include that directory in `PATH`.

Useful stable features:

- custom primary and subagent definitions;
- foreground Task delegation;
- granular deny-first permissions;
- MCP and native custom tools;
- persistent loopback HTTP server, sessions, SSE, and OpenAPI.

Background subagents are experimental and are not enabled. A persistent
`opencode serve` worker can be evaluated later to remove one-shot startup cost.

## Preflight and switching

```bash
uv run python scripts/preflight.py
```

The report shows the selected harness, whether its binary is installed, the
active email and Telegram models, and whether transcript FTS memory has been
initialized.

To switch:

1. Install and authenticate the CLI as the exact systemd service user.
2. Set its harness-specific model if a deterministic model is required.
3. Set `HARNESS`.
4. Run preflight.
5. Restart `fitlit-telegram.service`, `fitlit-gmail-poll.service`, and
   `fitlit-gmail.service`/timer.

## Primary references

- [Claude headless mode](https://code.claude.com/docs/en/headless)
- [Claude subagents](https://code.claude.com/docs/en/sub-agents)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Copilot CLI programmatic use](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically)
- [Copilot custom agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents)
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode agents](https://opencode.ai/docs/agents/)
