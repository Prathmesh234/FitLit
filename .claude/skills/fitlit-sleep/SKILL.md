---
name: fitlit-sleep
description: The user's sleep — sessions, stages (light/deep/REM/awake), duration, bedtime/wake time, and nightly sleep summaries. Use when they ask about sleep quality, how they slept, bedtime, time in deep/REM, or sleep trends.
---

# Sleep

Primary DB: **`data/db/sleep.db`** (pulled hourly). Sleep-related vitals are in
`daily_summaries.db` (`respiratoryRateSleepSummary`, `dailySleepTemperatureDerivations`).

| Metric | Table | Typed column | Notes |
|---|---|---|---|
| Sleep session | `sleep` | `sleep_type` (TEXT, e.g. `MAIN`/`NAP`) | Session shape; `interval` start/end = bed→wake |
| Sleep summary | `sleepSummary` | — (`data_json`) | per-night totals |

Sleep **stages** are a nested array in `data_json`:
`json_extract(data_json,'$.sleepStages')` → items with `stage`
(`LIGHT`/`DEEP`/`REM`/`AWAKE`) and an `interval`.

## Recipes (read-only)
Last 7 nights (duration from the session interval):
```bash
sqlite3 -header -column "file:data/db/sleep.db?mode=ro" "
 SELECT substr(start_time,1,10) night, start_time bed, end_time wake, sleep_type,
        ROUND((julianday(end_time)-julianday(start_time))*24,1) hours
 FROM sleep ORDER BY start_time DESC LIMIT 7;"
```
Count of stage segments for the latest night:
```bash
sqlite3 "file:data/db/sleep.db?mode=ro" "
 SELECT json_array_length(data_json,'\$.sleepStages') FROM sleep ORDER BY start_time DESC LIMIT 1;"
```
Time per stage (latest night, minutes) — needs json_each:
```bash
sqlite3 -header -column "file:data/db/sleep.db?mode=ro" "
 WITH s AS (SELECT data_json j FROM sleep ORDER BY start_time DESC LIMIT 1)
 SELECT json_extract(value,'\$.stage') stage,
        ROUND(SUM((julianday(json_extract(value,'\$.interval.endTime'))
                  -julianday(json_extract(value,'\$.interval.startTime')))*1440)) minutes
 FROM s, json_each(s.j,'\$.sleepStages') GROUP BY stage ORDER BY minutes DESC;"
```

## How to interpret (be a coach)
- Healthy adults: ~7–9h total; deep + REM each roughly 13–23% of the night
  (individual). Report the user's own trend, not just clinical ranges.
- Flag short/fragmented nights and correlate with **fitlit-heart** (resting HR/HRV
  up = worse recovery) and next-day **fitlit-activity**.
- Bedtime/wake **consistency** matters as much as duration — surface variance.
- If `respiratoryRateSleepSummary` (in `daily_summaries.db`) shows big swings, see
  **fitlit-cardio-vitals**.
