---
name: fitlit-nutrition
description: The user's diet & hydration — food/nutrition logs (calories in, macros) and water intake. Use when they ask about what they ate, calories consumed, protein/carbs/fat, hydration, water intake, or diet trends. Also the place to LOG meals (see fitlit-logging-coaching).
---

# Nutrition & hydration (diet)

Primary DB: **`data/db/nutrition.db`** (pulled every 30 min). These are
logged/manual data, so they only exist if the user (or an app) records them.

| Metric | Table | Typed column | Notes |
|---|---|---|---|
| Food / nutrition log | `nutritionLog` | — (`data_json`) | calories + macros per entry |
| Hydration log | `hydrationLog` | — (`data_json`) | water volume per entry |

Fields live in `data_json` — inspect a row's `raw_json` (op #9 in
**fitlit-sqlite-ops**) to confirm exact keys (e.g. `$.nutrients.calories`,
`$.volumeMilliliters`), since they depend on the source app.

## Recipes (read-only)
Recent food logs:
```bash
sqlite3 -header -column "file:data/db/nutrition.db?mode=ro" "
 SELECT start_time, json_extract(data_json,'\$.name') item,
        json_extract(data_json,'\$.nutrients.calories') kcal
 FROM nutritionLog ORDER BY start_time DESC LIMIT 20;"
```
Calories consumed today (adjust the JSON path to the real key):
```bash
sqlite3 "file:data/db/nutrition.db?mode=ro" "
 SELECT SUM(json_extract(data_json,'\$.nutrients.calories'))
 FROM nutritionLog WHERE start_time>=date('now')||'T';"
```
Water today (ml):
```bash
sqlite3 "file:data/db/nutrition.db?mode=ro" "
 SELECT SUM(json_extract(data_json,'\$.volumeMilliliters'))
 FROM hydrationLog WHERE start_time>=date('now')||'T';"
```

## How to interpret (be a coach)
- Pair calories **in** (here) with calories **out** (`totalCalories` in
  `live_activity.db`, see **fitlit-activity**) for an energy-balance view.
- Surface macro split (protein/carbs/fat) and hydration vs the user's own goals.
- If logs are sparse, that's a logging gap, not zero intake — say so, and offer to
  log via **fitlit-logging-coaching** rather than assuming.
- Never give rigid diet prescriptions; describe patterns and ask before advising.
