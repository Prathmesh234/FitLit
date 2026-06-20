---
name: fitlit-body
description: The user's body measurements — weight, body fat %, height, BMI, body/skin temperature, and blood glucose. Use when they ask about weight, body composition, BMI, weight trend, temperature, or glucose readings.
---

# Body measurements

Primary DB: **`data/db/body.db`** (pulled every 30 min). Mostly event-driven
(weigh-ins, manual logs), so entries are sparse by nature.

| Metric | Table | Typed column | Notes |
|---|---|---|---|
| Weight | `weight` | `weight_kg` (float) | ÷ for lb: ×2.2046 |
| Body fat | `bodyFat` | — (`data_json`) | percent; `$.percentage` |
| Height | `height` | — (`data_json`) | meters; `$.heightMeters` |
| Body temp | `bodyTemperature` | — (`data_json`) | °C |
| Core temp | `coreBodyTemperature` | — (`data_json`) | °C |
| Blood glucose | `bloodGlucose` | `blood_glucose_mg_per_dl` (float) | mg/dL |

## Recipes (read-only)
Weight trend (last 30 entries, kg + lb):
```bash
sqlite3 -header -column "file:data/db/body.db?mode=ro" "
 SELECT substr(start_time,1,10) day, weight_kg, ROUND(weight_kg*2.2046,1) lb
 FROM weight ORDER BY start_time DESC LIMIT 30;"
```
Latest weight + body fat:
```bash
sqlite3 -header -column "file:data/db/body.db?mode=ro" "
 SELECT (SELECT weight_kg FROM weight ORDER BY start_time DESC LIMIT 1) weight_kg,
        (SELECT json_extract(data_json,'\$.percentage') FROM bodyFat ORDER BY start_time DESC LIMIT 1) body_fat_pct;"
```
BMI (needs latest height + weight):
```bash
sqlite3 "file:data/db/body.db?mode=ro" "
 SELECT ROUND((SELECT weight_kg FROM weight ORDER BY start_time DESC LIMIT 1) /
        ((SELECT json_extract(data_json,'\$.heightMeters') FROM height ORDER BY start_time DESC LIMIT 1) *
         (SELECT json_extract(data_json,'\$.heightMeters') FROM height ORDER BY start_time DESC LIMIT 1)),1) bmi;"
```
Glucose readings:
```bash
sqlite3 -header -column "file:data/db/body.db?mode=ro" "
 SELECT start_time, blood_glucose_mg_per_dl, json_extract(data_json,'\$.mealType') context
 FROM bloodGlucose ORDER BY start_time DESC LIMIT 20;"
```

## How to interpret (be a coach)
- **Weight**: trend > single reading; use a 7-day moving average, note natural
  daily fluctuation (water/food). Don't react to one weigh-in.
- **BMI** is a crude proxy; mention it's limited (ignores muscle/composition).
- **Glucose**: note the `mealType`/timing context (fasting vs post-meal) — values
  mean different things. Flag anything clearly out of range but defer to clinicians.
