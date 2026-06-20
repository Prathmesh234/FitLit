---
name: fitlit-sqlite-ops
description: The fastest, safest way to query FitLit's SQLite health databases, plus the 10 most important data operations. Use for ANY task that reads/aggregates the user's Fitbit data — latest readings, daily/weekly summaries, date ranges, cross-metric joins, pulling fields out of JSON, or checking data freshness.
---

# FitLit SQLite operations (fast + safe)

DBs live in `data/db/<fetcher>.db` (or `$FITLIT_DATA_DIR/db`). See **fitlit-overview**
for the data-type → database map. Table name = the camelCase metric (e.g. `steps`,
`heartRate`, `nutritionLog`).

## Golden rules (do these for speed + safety)
1. **Always read read-only** so you never block the 24/7 writers or hit a WAL lock:
   ```bash
   sqlite3 "file:data/db/heart.db?mode=ro" "SELECT ..."
   ```
2. **Use the indexes.** Every table is indexed on `start_time` and `fetched_at`.
   Filter/sort on those, not on `json_extract` expressions.
3. **Prefer typed columns** (`count`, `beats_per_minute`, `weight_kg`, …). Only
   reach into `json_extract(data_json,'$.field')` for fields without a column.
4. **Timestamps are ISO-8601 UTC strings** → compare as strings:
   `WHERE start_time >= '2026-06-01'`. `date(start_time)` gives the day.
5. **Pretty output** for humans: add `-header -column` (or `-json` to parse).

## The 10 most important operations

**1. Is there data, and how fresh?**
```bash
sqlite3 "file:data/db/heart.db?mode=ro" \
 "SELECT COUNT(*) rows, MIN(start_time) first, MAX(start_time) latest, MAX(fetched_at) last_pull FROM heartRate;"
```

**2. Latest N readings of a metric.**
```bash
sqlite3 -header -column "file:data/db/heart.db?mode=ro" \
 "SELECT start_time, beats_per_minute FROM heartRate ORDER BY start_time DESC LIMIT 20;"
```

**3. Today's total / summary** (UTC day; adjust if user wants local).
```bash
sqlite3 "file:data/db/live_activity.db?mode=ro" \
 "SELECT SUM(count) steps_today FROM steps WHERE start_time >= date('now')||'T';"
```

**4. Daily aggregation (per-day series).**
```bash
sqlite3 -header -column "file:data/db/live_activity.db?mode=ro" \
 "SELECT substr(start_time,1,10) day, SUM(count) steps FROM steps GROUP BY day ORDER BY day DESC LIMIT 14;"
```

**5. Date-range filter (uses the start_time index).**
```bash
sqlite3 "file:data/db/heart.db?mode=ro" \
 "SELECT AVG(beats_per_minute) FROM heartRate WHERE start_time>='2026-06-01' AND start_time<'2026-06-08';"
```

**6. Pull a field that has no typed column (JSON extract).**
```bash
sqlite3 "file:data/db/daily_summaries.db?mode=ro" \
 "SELECT start_time, json_extract(data_json,'\$.breathsPerMinute') bpm FROM respiratoryRate ORDER BY start_time DESC LIMIT 7;"
```

**7. Join two metrics across databases (ATTACH).**
```bash
sqlite3 -header -column "file:data/db/heart.db?mode=ro" "
 ATTACH 'file:data/db/live_activity.db?mode=ro' AS act;
 SELECT substr(h.start_time,1,10) day, ROUND(AVG(h.beats_per_minute),0) avg_hr, SUM(s.count) steps
 FROM heartRate h JOIN act.steps s ON substr(h.start_time,1,10)=substr(s.start_time,1,10)
 GROUP BY day ORDER BY day DESC LIMIT 7;"
```

**8. Discover what tables/metrics a DB actually has.**
```bash
sqlite3 "file:data/db/daily_summaries.db?mode=ro" ".tables"
# or columns of a table:
sqlite3 "file:data/db/body.db?mode=ro" "PRAGMA table_info(weight);"
```

**9. Inspect one full data point (every field, incl. nested arrays).**
```bash
sqlite3 "file:data/db/sleep.db?mode=ro" "SELECT raw_json FROM sleep ORDER BY start_time DESC LIMIT 1;" | python3 -m json.tool
```

**10. Service-wide overview without touching SQL** (if the API is up):
```bash
curl -s localhost:8000/stats | python3 -m json.tool   # row counts per type per DB
```
(If the server isn't running, loop the DBs: `for db in data/db/*.db; do echo "$db"; sqlite3 "file:$db?mode=ro" ".tables"; done`)

## Notes
- Some metrics (e.g. `activeMinutes`, HR zones) store arrays/objects only in
  `data_json` — there's no scalar column; use `json_extract` / `json_each`.
- `recording_method` / `platform` / `device_name` tell you the data's source
  (e.g. `FITBIT`, device `Charge 6`, `PASSIVELY_MEASURED` vs `MANUAL`).
- Empty result ≠ error — that metric may just have no data yet.
