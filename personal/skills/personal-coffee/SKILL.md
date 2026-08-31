---
name: personal-coffee
description: The user's daily Seattle coffee-shop recommendation — the 9:00 AM Pacific email, the history of shops already sent, and how to record what they thought of one. Use when they mention coffee, a cafe, a coffee shop, where to work or read today, "the place you sent me", or give feedback (loved it / too loud / never again) about a shop.
---

# Daily coffee-shop recommendation

Every morning at **9:00 AM Pacific**, FitLit emails the user one coffee shop in
Seattle to visit that day. The recommendation is researched live on the web by
the headless harness — hours are read off the shop's own listing that morning,
not recalled from memory.

## The standing constraints

| Constraint | Value |
|---|---|
| Origin | South Lake Union, Seattle |
| Drive | ~15 min target, 18 min hard ceiling |
| Atmosphere | `very quiet`, `quiet`, or `moderate` — calm enough to sit, read, or work |
| Status | Must be open **today**, verified this morning |
| Repeats | Excluded for 60 days; an occasional repeat is acceptable and is labeled in the email |

Tunable via `FITLIT_PERSONAL_COFFEE_*` in `.env` — see `docs/PERSONAL.md`.

## Recording the user's feedback — do this whenever they react to a shop

Feedback is the single strongest input to future picks, and it only works if it
gets written down. When the user says anything evaluative about a specific
shop, record it:

```bash
uv run python -m personal.runner feedback "Victrola Coffee" disliked --note "way too loud on a weekday"
uv run python -m personal.runner feedback "Milstead & Co" loved   --note "perfect light, quiet upstairs"
uv run python -m personal.runner feedback "Some Cafe"     blocked --note "never send this again"
```

Sentiments: `loved`, `liked`, `neutral`, `disliked`, `blocked`.

* `blocked` is a **permanent hard exclusion** — the shop is filtered out before
  the model ever sees the request. Use it only when the user clearly means
  "never again".
* Every other verdict, with its note, is passed to the next run as standing
  taste guidance. Write the note in the user's own words; a specific reason
  ("too loud after 10am", "loved the window seats") steers the next pick far
  better than a bare sentiment.
* The newest verdict per shop wins, so re-recording updates rather than
  duplicates.

Confirm back what you logged, and mention that it takes effect on the next
morning's pick.

## Reading the history

```bash
uv run python -m personal.runner history coffee --limit 30
uv run python -m personal.runner status coffee     # config, blocked list, preferences, recent runs
```

Or query directly:

```bash
sqlite3 -header -box data/state/personal.db \
  "SELECT day,name,neighborhood,drive_minutes,noise_level,hours_today
     FROM coffee_recommendations ORDER BY day DESC LIMIT 15;"

sqlite3 -header -box data/state/personal.db \
  "SELECT created_at,shop_name,sentiment,note FROM coffee_feedback ORDER BY id DESC;"
```

The full model output for any day — vibe, sources, the exact search queries the
run used — is kept in `coffee_recommendations.payload_json`:

```bash
sqlite3 data/state/personal.db \
  "SELECT payload_json FROM coffee_recommendations WHERE day='2026-08-30';" | jq .
```

## Running it by hand

```bash
uv run python -m personal.runner run coffee --dry-run   # research + render, no ledger, no email
uv run python -m personal.runner run coffee --force     # re-send today
```

A dry run takes a minute or two — it is doing real web research.

## Answering questions about it

* **"Where should I get coffee today?"** — If today's email already went out,
  read it back from `coffee_recommendations` rather than inventing a new pick.
  If it has not, offer to run the task.
* **Hours** — quote `hours_today` with its `hours_source` and `verified_date`,
  and say when it was checked. If the verified date is not today, say so
  plainly instead of implying the hours are current.
* **Never invent a shop, an address, or an opening time.** Everything here is
  a real place the user may drive to.
