# The personal section

FitLit started as a health aggregator and is now the owner's assistant across
their whole day. `fitlit/` is the health half: wearable ingestion, analysis, and
the Gmail and Telegram channels. `personal/` is everything else — scheduled
personal tasks, their durable record, and the skills that describe them.

The two halves are one assistant. They share `.env`, the data directory, the
Gmail sender, and the same headless harness; the conversational agent is given
a read-only view of the personal ledger so it can answer questions about what a
task actually did. What they do not share is a budget: a personal task keeps its
own ledger and cannot consume or be throttled by the health send allowance.

## Layout

| Path | Purpose |
|---|---|
| `personal/config.py` | Personal settings, layered on `fitlit.config` |
| `personal/store.py` | The system of record — SQLite at `data/state/personal.db` |
| `personal/agent.py` | Isolated, **web-enabled** headless-harness runner |
| `personal/context.py` | The slice of the ledger the conversational agent sees |
| `personal/emails.py` | Gmail-safe rendering for personal mail |
| `personal/runner.py` | The CLI every timer and cron entry calls |
| `personal/tasks/coffee.py` | The daily coffee-shop recommendation |
| `personal/skills/` | `personal-overview`, `personal-coffee` (symlinked into `.claude/skills/`) |
| `deploy/fitlit-personal@.service` | One templated systemd unit for every task |
| `deploy/fitlit-personal-coffee.timer` | 9:00 AM **Pacific**, `Persistent=true` |

## The CLI

```bash
uv run python -m personal.runner list
uv run python -m personal.runner status [coffee]
uv run python -m personal.runner history coffee --limit 20
uv run python -m personal.runner run coffee --dry-run
uv run python -m personal.runner run coffee
uv run python -m personal.runner feedback "Victrola Coffee" disliked --note "too loud"
```

`--dry-run` researches and renders but writes nothing and mails nothing.
`--force` reopens a day that already went out. `--no-send` records without
mailing. Every command prints JSON, so a timer's journal stays greppable.

Two independent guards stop a double send: a kernel `flock` on
`data/state/personal-tasks.lock` for concurrent processes, and a per-task,
per-Pacific-day row in `personal_task_runs` for a timer catching up after a
reboot. A day that ended in `failed` may be retried; a day that ended in `sent`
may not, short of `--force`.

## The ledger — `data/state/personal.db`

Private (0600, gitignored, under the same `data/` tree as the health databases).

| Table | Contents |
|---|---|
| `personal_task_runs` | `(task, day)` unique. `reserved` → `sent` / `failed` / `skipped` |
| `coffee_recommendations` | One row per day, plus the full model output in `payload_json` |
| `coffee_feedback` | The owner's verdict on a shop; the newest per shop wins |

## The daily coffee recommendation

At 9:00 AM Pacific, one email: a Seattle coffee shop to visit that day.

| Constraint | Value | Override |
|---|---|---|
| Origin | South Lake Union | `FITLIT_PERSONAL_COFFEE_ORIGIN` |
| Drive | 15 min target, 18 min ceiling | `..._DRIVE_MINUTES`, `..._DRIVE_TOLERANCE_MINUTES` |
| Atmosphere | `very quiet`, `quiet`, or `moderate` | — |
| Repeat window | 60 days | `..._REPEAT_WINDOW_DAYS` |
| Delivery | 09:00 Pacific | `..._SEND_HOUR` **and** the timer's `OnCalendar` |

### Why the answer is trustworthy

A recommendation is a fact about the physical world on a specific morning, so
the task refuses to answer from the model's memory. `personal/agent.py` grants
exactly `WebSearch` and `WebFetch`, and the run is then checked against rules
the JSON schema cannot express (`personal/tasks/coffee.py::validate`):

* **The search really happened.** The harness envelope reports its own tool use;
  a run claiming zero web searches is rejected outright, so stale hours can
  never be presented as live.
* **The hours are today's.** `verified_date` must equal today's Pacific date,
  `open_today` must be true, and `hours_source` must name where the hours came
  from. The email prints all three.
* **The sources are real and plural.** At least two URLs from at least two
  different hosts, each a well-formed link.
* **The drive is within the ceiling**, the room is not loud, and the maps link
  is a genuine Google Maps URL.
* **The shop is not blocked** by the owner.

The prompt asks for plain Google-style queries (`victrola coffee seattle hours`)
rather than model-speak, and requires reading the shop's own site or its Google
Business listing before reporting an opening time. The queries it actually ran
are stored in `payload_json.search_queries`, so a bad pick can be audited.

### Duplicates

Every shop inside the repeat window is listed in the prompt as an exclusion.
Names are compared by a normalized key — `Victrola Coffee Roasters` and
`victrola coffee` are one shop — so a rename does not defeat the guard. If the
model still returns a repeat, it is told which shop and asked again, up to
`FITLIT_PERSONAL_COFFEE_ATTEMPTS` times. A repeat that survives all attempts is
sent anyway, labeled in the email with the date it was last suggested: an
occasional repeat is better than a silent gap.

### Feedback

```bash
uv run python -m personal.runner feedback "Milstead & Co" loved   --note "quiet upstairs"
uv run python -m personal.runner feedback "Somewhere"     blocked --note "never again"
```

Sentiments: `loved`, `liked`, `neutral`, `disliked`, `blocked`. `blocked` is a
permanent hard exclusion applied in code before the model sees the request;
every other verdict, with its note, is passed to the next run as standing taste
guidance. The newest verdict per shop wins, so re-recording updates rather than
duplicates — including unblocking.

The `personal-coffee` skill instructs the assistant to record feedback whenever
the owner reacts to a shop in chat or email, so this rarely needs typing.

## What the conversational agent knows

`fitlit.email_agent.build_grounding` adds a `personal` block to every reply's
snapshot: today's recommendation, the last few picks, standing feedback, and the
blocked list. It is selected only by its own vocabulary (coffee, cafe, espresso,
recommendation…), so a sleep question never spends evidence slots on it.

The system prompt frames the assistant accordingly: FitLit is the owner's
assistant across their whole day, health is its deepest domain rather than its
boundary, and paths beginning `personal.` are facts of record — report what was
actually sent, pair hours with the date they were verified, and never invent a
business, an address, or an opening time.

## Deployment

```bash
uv run python scripts/preflight.py          # personal.tasks.coffee.ready must be true
sudo uv run python scripts/install_services.py --install --start
systemctl list-timers fitlit-personal-coffee.timer
systemctl status fitlit-personal@coffee.service
journalctl -u fitlit-personal@coffee.service -n 50
```

The timer carries `OnCalendar=*-*-* 09:00:00 America/Los_Angeles`, so 9:00 AM
stays 9:00 AM across the PST/PDT change without touching the unit.
`Persistent=true` means a machine that was off at 09:00 still sends the day's
email when it comes back. `TimeoutStartSec=1800` allows for real web research.

Set `FITLIT_PERSONAL_COFFEE_ENABLED=false` and re-run the installer to turn the
task off; the installer disables the timer rather than leaving it armed.

`HARNESS` must be `claude`. The other harnesses are not wired for the web tools
this task depends on, and `personal/agent.py` refuses to substitute one rather
than quietly returning an unverified answer.

## Adding a task

1. `personal/tasks/<name>.py` exposing `run(*, now, dry_run, force, send)`,
   `status()`, and `history(limit)`.
2. A JSON output schema plus a `validate()` enforcing everything the schema
   cannot — freshness, bounds, duplicates.
3. Register in `personal/runner.py::TASKS` and `personal/config.py::TASKS`.
4. `deploy/fitlit-personal-<name>.timer` (Pacific `OnCalendar`,
   `Persistent=true`, `Unit=fitlit-personal@<name>.service`), registered in
   `PERSONAL_TIMERS` in `scripts/install_services.py`.
5. A `personal-<name>` skill in `personal/skills/`, symlinked into
   `.claude/skills/`.
6. Tests alongside `tests/test_personal_*.py`.
