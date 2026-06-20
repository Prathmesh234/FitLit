---
name: fitlit-logging-coaching
description: Use FitLit as a personal fitness logger & coach — record manual entries the wearable can't capture (meals, mood, workouts, notes), run daily check-ins, and produce cross-domain weekly summaries/reports. Use when the user wants to LOG something, do a check-in, set/review goals, or get an overall "how am I doing" report.
---

# Personal logging & coaching

FitLit's Google Health pull is **read-only**, so anything the user tells *you*
(meals, mood, soreness, manual workouts, weight off-device) is recorded in a
**separate local journal DB** you own: `data/db/journal.db`. This never conflicts
with the fetchers (its own file) and is safe to write.

## Logging — create/append to the journal
Create the table once (idempotent), then insert. Use a normal read-write
connection here (journal.db is yours; the fetchers never touch it):
```bash
sqlite3 data/db/journal.db "
 CREATE TABLE IF NOT EXISTS entries(
   ts TEXT DEFAULT (datetime('now')),  -- when logged (UTC)
   at TEXT,                            -- when it happened (optional ISO)
   kind TEXT,                          -- meal|workout|weight|mood|note|water|...
   value TEXT,                         -- freeform or number-as-text
   detail_json TEXT                    -- optional structured detail
 );"
# example: log a meal
sqlite3 data/db/journal.db "INSERT INTO entries(at,kind,value,detail_json)
 VALUES('2026-06-19T13:00:00Z','meal','chicken bowl ~650 kcal',
        json_object('kcal',650,'protein_g',45));"
```
Read back:
```bash
sqlite3 -header -column "file:data/db/journal.db?mode=ro" \
 "SELECT ts, kind, value FROM entries ORDER BY ts DESC LIMIT 20;"
```
> Always confirm the parsed values with the user before inserting (e.g. "logging
> 650 kcal, 45g protein — correct?"). Keep `kind` values consistent so they
> aggregate later.

## Daily check-in (suggested flow)
1. Pull today's snapshot across domains (steps, resting HR, last night's sleep,
   workouts, calories in if logged) using the domain skills + **fitlit-sqlite-ops**.
2. Ask the user for what the wearable can't see (meals, mood, energy, soreness)
   and log them to `journal.db`.
3. Give a short, specific summary + one actionable suggestion.

## Weekly report (cross-domain)
Aggregate the last 7 days from each DB and the journal into one digest:
- **Activity**: avg/total steps, active days, workout count & volume.
- **Sleep**: avg duration, consistency, deep/REM trend.
- **Heart**: resting HR & HRV direction.
- **Body**: weight trend (7-day avg).
- **Diet**: logged calories/macros + hydration (from journal/nutrition).
Compare each to the prior week and the user's `goals`; lead with what changed.

## Coaching principles
- **Cite the user's own numbers and trends**, not generic targets.
- Trends > single readings; use moving averages.
- Be encouraging and specific; give **one** clear next action, not a lecture.
- Stay in your lane: describe data and habits, don't diagnose. For concerning
  cardiac/glucose/SpO2 readings, point to **fitlit-cardio-vitals**/**fitlit-body**
  and recommend a clinician.
- Read with `mode=ro` for fetcher DBs; only `journal.db` is yours to write.
