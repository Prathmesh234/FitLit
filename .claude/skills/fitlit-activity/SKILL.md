---
name: fitlit-activity
description: The user's daily movement & activity — steps, distance, calories burned, active minutes, active zone minutes, floors climbed, sedentary time. Use when they ask "how many steps", "how active was I", "did I hit my goal", calories burned, or activity trends.
---

# Activity & movement

Primary DB: **`data/db/live_activity.db`** (pulled every 60s). Goals & some
roll-ups are in `daily_summaries.db`.

| Metric | Table | Typed column(s) | Notes |
|---|---|---|---|
| Steps | `steps` | `count` (int) | per-interval; SUM for a day |
| Distance | `distance` | `meters` (float) | meters; ÷1000 for km |
| Calories | `totalCalories` | — (`data_json`) | total burned |
| Active minutes | `activeMinutes` | — (`data_json`) | array by intensity level |
| Active Zone Min | `activeZoneMinutes` | — (`data_json`) | Fitbit AZM |
| Floors | `floors` | `floors` (float) | flights climbed |
| Activity level | `activityLevel` | — (`data_json`) | sedentary/light/moderate/active |
| Sedentary | `sedentaryPeriod` | — (`data_json`) | inactivity spans |
| Elevation | `altitude` | `gain_millimeters` (int) | ÷1e6 for meters |
| Goals | `goals` (in `daily_summaries.db`) | — (`data_json`) | step/active targets |

## Recipes (read-only)
Steps today:
```bash
sqlite3 "file:data/db/live_activity.db?mode=ro" \
 "SELECT SUM(count) steps_today FROM steps WHERE start_time>=date('now')||'T';"
```
Last 14 days of steps + distance:
```bash
sqlite3 -header -column "file:data/db/live_activity.db?mode=ro" "
 SELECT substr(s.start_time,1,10) day, SUM(s.count) steps,
        ROUND(SUM(d.meters)/1000.0,2) km
 FROM steps s LEFT JOIN distance d ON s.start_time=d.start_time
 GROUP BY day ORDER BY day DESC LIMIT 14;"
```
Floors today:
```bash
sqlite3 "file:data/db/live_activity.db?mode=ro" \
 "SELECT SUM(floors) FROM floors WHERE start_time>=date('now')||'T';"
```

## How to interpret (be a coach)
- Common step targets: 8–10k/day is a solid baseline; surface progress vs the
  user's `goals` if present, not a generic number.
- Distinguish **passive** vs **manual** data via `recording_method`.
- For "how active today", combine steps + active/zone minutes + floors, and
  compare to the trailing 7-day average rather than a single absolute.
- Use **fitlit-sqlite-ops** mechanics; pair with **fitlit-heart** for effort and
  **fitlit-workouts** for logged exercise sessions.
