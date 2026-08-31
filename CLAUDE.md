# CLAUDE.md

Claude Code entry point for this repository. FitLit's headless harness is
Claude Code (`HARNESS=claude`), so these rules apply both to interactive work
in this clone and to the daemon-invoked agent.

FitLit is the owner's personal assistant. Wearable health is its deepest
domain, not its boundary: `fitlit/` holds the health half, and `personal/`
holds the scheduled personal tasks, their ledger, and their skills.

## Mandatory user rules

@AGENTS.md

The rules in `AGENTS.md` are authoritative and not advisory. They win over any
default behavior or assumption. `AGENTS.md` is gitignored because it holds
private coaching context, so the import above resolves only in a configured
clone and is a no-op in a fresh one.

## Operating the repository

- Runbook and preflight: `AGENT_START.md`
- Harness selection, authentication, and delegation limits:
  `docs/HEADLESS_HARNESSES.md`
- Deployment and systemd units: `docs/DEPLOYMENT.md`
- Gmail and Telegram channels: `docs/GMAIL_SERVICE.md`,
  `docs/TELEGRAM_SERVICE.md`
- Personal tasks, their ledger, and their timers: `docs/PERSONAL.md`

Common commands:

```bash
uv run python scripts/preflight.py
uv run python -m pytest tests -q
uv run python scripts/privacy_scan.py
uv run python -m personal.runner status
```

## Hard constraints

- Never print, commit, or summarize `.env`, OAuth tokens, Telegram credentials,
  private health rows, or rows from the personal ledger
  (`data/state/personal.db`).
- Keep the API bound to `127.0.0.1:8000`; it has no application auth.
- Do not add `--bare` to the headless `claude` invocation: it refuses OAuth and
  requires `ANTHROPIC_API_KEY`. Isolation comes from `--setting-sources ""`
  plus the generated private settings file.

- A personal task must never state a fact about the outside world — opening
  hours, prices, availability, whether a place still exists — that it did not
  fetch from the live web in that run. `personal/agent.py` grants WebSearch and
  WebFetch and then verifies from the harness envelope that a search actually
  happened; do not relax that check.

## Domain skills

`.claude/skills/` holds the health skills (`fitlit-overview`,
`fitlit-activity`, `fitlit-sleep`, `fitlit-heart`, `fitlit-body`,
`fitlit-nutrition`, `fitlit-workouts`, `fitlit-cardio-vitals`,
`fitlit-logging-coaching`, `fitlit-sqlite-ops`) and, symlinked from
`personal/skills/`, the personal ones (`personal-overview`,
`personal-coffee`). Prefer them over ad-hoc queries: start at
`fitlit-overview` for a health question and `personal-overview` for anything
else the assistant does.
