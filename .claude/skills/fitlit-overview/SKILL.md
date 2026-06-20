---
name: fitlit-overview
description: Read FIRST for any FitLit task. Explains what FitLit is, where the wearable data lives (which SQLite database holds which metric), and how to act as the user's personal fitness logger + Fitbit/Google Health data aggregator. Use whenever the user asks about their health/fitness data, "my Fitbit", trends, summaries, or "how am I doing".
---

# FitLit — orientation

FitLit continuously pulls the user's **Fitbit / Google Health (v4)** data and
stores it in local **SQLite** databases. Your job when these skills are active is
to be the user's **personal fitness companion**: query their real data, aggregate
it, surface trends, and help them log/track. Be concrete and cite numbers.

## Where the data lives — one SQLite DB per fetcher
DBs are under `data/db/` (or `$FITLIT_DATA_DIR/db` if that env var is set). Each
**table is one data type**; table name = the camelCase metric name.

| Database | Domain | Tables (data types) |
|---|---|---|
| `live_activity.db` | movement (live) | `steps`, `distance`, `totalCalories`, `activeMinutes`, `activeZoneMinutes`, `floors`, `activityLevel`, `sedentaryPeriod`, `altitude`, `caloriesInHeartRateZone`, `timeInHeartRateZone` |
| `heart.db` | live heart rate | `heartRate` |
| `daily_summaries.db` | daily roll-ups | `dailyRestingHeartRate`, `heartRateVariability`, `dailyHeartRateVariability`, `dailyHeartRateZones`, `vo2Max`, `dailyVo2Max`, `runVo2Max`, `oxygenSaturation`, `dailyOxygenSaturation`, `respiratoryRate`, `dailyRespiratoryRate`, `respiratoryRateSleepSummary`, `dailySleepTemperatureDerivations`, `exercise`, `goals` |
| `body.db` | body metrics | `bodyFat`, `weight`, `height`, `bodyTemperature`, `coreBodyTemperature`, `bloodGlucose` |
| `sleep.db` | sleep | `sleep`, `sleepSummary` |
| `nutrition.db` | diet | `nutritionLog`, `hydrationLog` |
| `cardiac.db` | cardiac events | `ecgMeasurement`, `ecgRhythmClassification`, `afibAnalysisWindow` |
| `location.db` | workout GPS | `location` |

> A domain can span DBs — e.g. heart data is in both `heart.db` (live bpm) and
> `daily_summaries.db` (resting HR, HRV). The domain skills spell this out.

## Every table has the same envelope columns
`name` (PK, the API's unique id), `data_type`, `start_time`, `end_time`,
`start_utc_offset`, `end_utc_offset`, `recording_method`, `platform`,
`device_name`, `update_time`, `fetched_at`, `data_json`, `raw_json`.

Plus **typed value columns** for well-modeled types (e.g. `steps.count`,
`heartRate.beats_per_minute`, `weight.weight_kg`). Any field *not* given a typed
column is still fully present in **`data_json`** (the metric object) and
**`raw_json`** (the whole data point) — pull it with `json_extract(...)`.

## Timestamps
Stored as ISO-8601 **strings** (UTC `...Z`). They sort lexically, so date-range
filters work as plain string comparisons. `date(start_time)` /
`substr(start_time,1,10)` give the calendar day.

## Which skill to use
- **fitlit-sqlite-ops** — the 10 core query operations + fast/safe access (use for any data pull).
- **fitlit-activity / -heart / -sleep / -nutrition / -body / -cardio-vitals / -workouts** — per-domain data + how to interpret it.
- **fitlit-logging-coaching** — using Claude to log entries, do daily check-ins, and produce weekly fitness reports.

## Reality checks
- Data only exists after the service runs with a valid OAuth token. If a table
  is empty/missing, the fetcher hasn't run or there's no such data yet. For
  testing without real data: `uv run python scripts/seed_dummy_data.py`.
- The fetchers **write 24/7** — always read with `mode=ro` (see fitlit-sqlite-ops)
  so you never block a writer.
- The pull is **read-only** from Google Health; FitLit doesn't write back to
  Fitbit. "Logging" means recording into a local side-table — see fitlit-logging-coaching.
