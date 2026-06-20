---
name: fitlit-workouts
description: The user's logged workouts/exercise sessions and their GPS routes — exercise type, duration, calories, pace, distance, and location/route data. Use when they ask about workouts, exercises, runs/walks/rides, a specific session, pace, or route maps.
---

# Workouts & routes

Spans two DBs:
- **`data/db/daily_summaries.db`** — `exercise` sessions (richly typed).
- **`data/db/location.db`** — `location` route/GPS for exercises.

### `exercise` (in `daily_summaries.db`) — typed columns
`exercise_type`, `display_name`, `active_duration` (e.g. `900s`),
`calories_kcal`, `distance_millimeters`, `exercise_steps`, `active_zone_minutes`.
The session `interval` gives start/end; deeper detail (splits, events) is in
`data_json`.

### `location` (in `location.db`)
Route points in `data_json` (lat/lng/time) — for mapping a workout.

## Recipes (read-only)
Recent workouts:
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT substr(start_time,1,10) day, exercise_type, display_name,
        active_duration, calories_kcal, ROUND(distance_millimeters/1e6,2) km, exercise_steps
 FROM exercise ORDER BY start_time DESC LIMIT 15;"
```
This week's training load:
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT COUNT(*) workouts, ROUND(SUM(calories_kcal)) kcal,
        ROUND(SUM(distance_millimeters)/1e6,1) total_km
 FROM exercise WHERE start_time>=date('now','-7 days');"
```
Route points for the latest workout:
```bash
sqlite3 "file:data/db/location.db?mode=ro" \
 "SELECT raw_json FROM location ORDER BY start_time DESC LIMIT 1;" | python3 -m json.tool
```

## How to interpret (be a coach)
- Summarize a session in plain terms: type, duration, distance, pace
  (`active_duration` ÷ distance), calories, AZM.
- Track **weekly volume & frequency** trends; flag big jumps (injury risk) or long
  gaps. Cross-reference **fitlit-heart** (effort/zones) and **fitlit-activity**.
- `recording_method=MANUAL` means the user logged it; `AUTO_DETECTED` means Fitbit
  inferred it — call out which when it matters.
