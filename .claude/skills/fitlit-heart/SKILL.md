---
name: fitlit-heart
description: The user's heart data — live heart rate (bpm), resting heart rate, heart-rate variability (HRV), and heart-rate zones. Use when they ask about pulse, bpm, resting HR, HRV, cardiac strain, recovery, or heart-rate trends.
---

# Heart rate & cardiac rhythm

Spans two DBs:
- **`data/db/heart.db`** — live `heartRate` (every 60s).
- **`data/db/daily_summaries.db`** — daily roll-ups.

| Metric | DB · Table | Typed column | Notes |
|---|---|---|---|
| Live heart rate | `heart.db` · `heartRate` | `beats_per_minute` (int) | sample-in-time bpm |
| Resting HR | `daily_summaries.db` · `dailyRestingHeartRate` | — (`data_json`) | one value/day |
| HRV | `daily_summaries.db` · `heartRateVariability` | — (`data_json`) | ms (rmssd) |
| Daily HRV | `daily_summaries.db` · `dailyHeartRateVariability` | — (`data_json`) | nightly summary |
| HR zones | `daily_summaries.db` · `dailyHeartRateZones` | — (`data_json`) | fat-burn/cardio/peak mins |

Live bpm metadata (motion context, sensor) lives in
`json_extract(data_json,'$.heartRateMetadata')`.

## Recipes (read-only)
Today's HR range + average:
```bash
sqlite3 -header -column "file:data/db/heart.db?mode=ro" "
 SELECT COUNT(*) samples, MIN(beats_per_minute) min, ROUND(AVG(beats_per_minute)) avg,
        MAX(beats_per_minute) max
 FROM heartRate WHERE start_time>=date('now')||'T';"
```
Resting HR trend (last 14 days):
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT substr(start_time,1,10) day, json_extract(data_json,'\$.beatsPerMinute') resting_hr
 FROM dailyRestingHeartRate ORDER BY start_time DESC LIMIT 14;"
```
HRV trend:
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT substr(start_time,1,10) day, json_extract(data_json,'\$.rmssd') hrv_ms
 FROM dailyHeartRateVariability ORDER BY start_time DESC LIMIT 14;"
```
> Field names inside `data_json` vary by type — if `$.beatsPerMinute`/`$.rmssd`
> returns null, inspect one row's `raw_json` (op #9 in fitlit-sqlite-ops) to find
> the real key.

## How to interpret (be a coach)
- **Resting HR**: lower trending = improving fitness/recovery; a multi-day jump
  can signal illness/overtraining/poor sleep. Compare to the user's own baseline.
- **HRV**: higher = better recovery; it's highly individual — trend over absolute.
- Cross-reference with **fitlit-sleep** (recovery) and **fitlit-activity** (load).
- For serious rhythm concerns (ECG/AFib), see **fitlit-cardio-vitals**, and never
  give medical diagnosis — surface the data and suggest a clinician if alarming.
