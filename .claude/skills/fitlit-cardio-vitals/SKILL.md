---
name: fitlit-cardio-vitals
description: The user's cardio-fitness & vitals — VO2 max / cardio fitness score, blood oxygen (SpO2), respiratory rate, and cardiac events (ECG readings, AFib analysis). Use when they ask about VO2 max, cardio fitness, oxygen saturation, breathing rate, ECG, or irregular rhythm.
---

# Cardio fitness & vitals

Spans two DBs:
- **`data/db/daily_summaries.db`** — VO2 max, SpO2, respiratory rate.
- **`data/db/cardiac.db`** — ECG & AFib events (pulled every 5 min).

| Metric | DB · Table | Notes |
|---|---|---|
| VO2 max | `daily_summaries.db` · `vo2Max`, `dailyVo2Max`, `runVo2Max` | cardio-fitness score |
| Blood oxygen | `daily_summaries.db` · `oxygenSaturation`, `dailyOxygenSaturation` | SpO2 %, `$.percentage` |
| Respiratory rate | `daily_summaries.db` · `respiratoryRate`, `dailyRespiratoryRate` | breaths/min, `$.breathsPerMinute` |
| ECG measurement | `cardiac.db` · `ecgMeasurement` | raw ECG record |
| ECG rhythm | `cardiac.db` · `ecgRhythmClassification` | classification result |
| AFib window | `cardiac.db` · `afibAnalysisWindow` | irregular-rhythm analysis |

These types mostly have no typed columns — read `data_json` and confirm keys via
a `raw_json` dump (op #9 in **fitlit-sqlite-ops**).

## Recipes (read-only)
VO2 max trend:
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT substr(start_time,1,10) day, json_extract(data_json,'\$.value') vo2max
 FROM dailyVo2Max ORDER BY start_time DESC LIMIT 14;"
```
SpO2 (last 14 nights):
```bash
sqlite3 -header -column "file:data/db/daily_summaries.db?mode=ro" "
 SELECT substr(start_time,1,10) day, json_extract(data_json,'\$.percentage') spo2_pct
 FROM dailyOxygenSaturation ORDER BY start_time DESC LIMIT 14;"
```
Recent ECG / AFib events:
```bash
sqlite3 -header -column "file:data/db/cardiac.db?mode=ro" \
 "SELECT start_time, data_type, recording_method FROM ecgRhythmClassification ORDER BY start_time DESC LIMIT 10;"
sqlite3 -header -column "file:data/db/cardiac.db?mode=ro" \
 "SELECT start_time, raw_json FROM afibAnalysisWindow ORDER BY start_time DESC LIMIT 3;"
```

## How to interpret (be a coach, NOT a doctor)
- **VO2 max** up = improving aerobic fitness; it's the best single fitness proxy
  here. Trend over weeks.
- **SpO2** normally ~95–100% at rest; persistent dips can matter — but these are
  consumer-grade. Respiratory rate is most useful as a personal baseline.
- **ECG/AFib are medical signals.** Report what's recorded factually. If a reading
  is flagged abnormal or the user reports symptoms, **explicitly recommend they
  consult a clinician** — never diagnose or reassure away a concerning result.
