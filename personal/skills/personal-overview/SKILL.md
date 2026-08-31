---
name: personal-overview
description: Read FIRST for anything that is not wearable-health data. Explains FitLit's personal section — the personal tasks, their scheduled jobs, the shared ledger at data/state/personal.db, and how to add a new one. Use whenever the user asks about a personal task, a recommendation, a scheduled personal job, coffee, errands, plans, or "what else do you do for me".
---

# FitLit — the personal section

FitLit is the user's **personal assistant**. Wearable health is its deepest
domain, not its only one. The `personal/` package is the rest of the assistant:
scheduled personal tasks, their durable record, and the skills that describe
them.

Use this skill to orient. Then load the specific task skill
(`personal-coffee`) for the domain in question.

## Layout

| Path | What it is |
|---|---|
| `personal/config.py` | Personal-domain settings, layered on `fitlit.config` (which loads `.env`) |
| `personal/store.py` | The system of record — SQLite at `data/state/personal.db` |
| `personal/agent.py` | Isolated, **web-enabled** headless-harness runner for personal tasks |
| `personal/emails.py` | Gmail-safe rendering for personal mail |
| `personal/runner.py` | The CLI every timer and cron entry calls |
| `personal/tasks/` | One module per task (`coffee.py`) |
| `personal/skills/` | These skills (symlinked into `.claude/skills/`) |
| `deploy/fitlit-personal@.service` | One templated systemd unit for every task |
| `deploy/fitlit-personal-*.timer` | One timer per task, in **Pacific** time |

## The CLI

```bash
uv run python -m personal.runner list
uv run python -m personal.runner status              # every task
uv run python -m personal.runner status coffee
uv run python -m personal.runner history coffee --limit 20
uv run python -m personal.runner run coffee --dry-run   # research, render, send nothing
uv run python -m personal.runner run coffee             # the real scheduled path
```

`--dry-run` never writes to the ledger and never mails. `--force` re-runs a day
that already went out. `--no-send` records without mailing.

## The ledger — `data/state/personal.db`

Private (mode 0600, gitignored). Read it with the same care as the health DBs:
never print raw rows into an email or a chat unless the user asked for them.

| Table | Purpose |
|---|---|
| `personal_task_runs` | One row per task per Pacific day: `reserved` → `sent` / `failed` / `skipped`. This is what makes a task idempotent when a timer catches up after a reboot. |
| `coffee_recommendations` | Every shop already sent — the duplicate guard |
| `coffee_feedback` | The user's verdict on a specific shop |

```bash
sqlite3 -header -box data/state/personal.db \
  "SELECT day,status,detail FROM personal_task_runs ORDER BY day DESC LIMIT 10;"
```

## Two rules that apply to every personal task

1. **Pacific, always.** Days, schedules, and timestamps are Pacific. The timers
   carry `America/Los_Angeles` so 9:00 AM stays 9:00 AM across the DST change.
2. **Live facts come from the live web.** A personal task that states a fact
   about the outside world — hours, prices, availability, whether a place still
   exists — must have fetched it in that run. `personal/agent.py` grants
   WebSearch and WebFetch and then verifies from the harness envelope that a
   search really happened; a run that reports zero searches is rejected rather
   than trusted.

## Adding a new personal task

1. Write `personal/tasks/<name>.py` exposing `run(*, now, dry_run, force, send)`
   returning a result dataclass, plus `status()` and `history(limit)`.
2. Give it a JSON output schema and a `validate()` that enforces every rule the
   schema cannot — freshness, bounds, and the duplicate guard.
3. Register it in `personal/runner.py`'s `TASKS` and `personal/config.py`'s
   `TASKS`.
4. Add `deploy/fitlit-personal-<name>.timer` (Pacific `OnCalendar`,
   `Persistent=true`, `Unit=fitlit-personal@<name>.service`) and register it in
   `PERSONAL_TIMERS` in `scripts/install_services.py`.
5. Write a `personal-<name>` skill next to this one.

Details and the deployment steps: `docs/PERSONAL.md`.
