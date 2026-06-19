# Fitbit / Google Health API — Complete Endpoint Research

> **Research stage deliverable.** This document catalogues *every* API endpoint
> available for pulling Fitbit data, grouped into domains. It covers **both**
> APIs that exist as of **June 2026**.
>
> Last researched: **2026-06-19**.

---

## 0. The big picture: there are TWO APIs right now

Fitbit is a Google company, and the developer platform is **mid-migration**:

| | Legacy **Fitbit Web API** | New **Google Health API** |
|---|---|---|
| Base URL | `https://api.fitbit.com` | `https://health.googleapis.com` |
| Version | `1`, `1.1`, `1.2` (per resource) | `v4` |
| Auth | Fitbit OAuth 2.0 | Google OAuth 2.0 |
| Style | One bespoke REST endpoint **per data type** (~90 endpoints) | A **small generic set of methods** applied over ~31 **data types** |
| Status | **Deprecated — turned down September 2026** | **Current** — launched at Google I/O May 2026 |
| Use it for | Existing integrations until the cutover | **All new development (recommended target)** |

Both run side by side **May → September 2026**. After the turn-down, only the
Google Health API will sync data. OAuth tokens do **not** carry over — users
must reconnect.

> **Recommendation for FitLit:** build the cron fetcher against the **Google
> Health API**, but keep this catalogue of the legacy endpoints because (a) the
> legacy API is still live today, (b) almost all docs/examples still reference
> it, and (c) the data-type semantics map across.

A note on the device: a **Fitbit** wearable (e.g. Charge / Sense / Versa /
Inspire / Ace line) exposes data through the *account-level* Web API — the
endpoints below are the same regardless of which tracker model is paired. Some
metrics (ECG, SpO2 intraday, skin temperature, breathing rate, HRV, IRN) are
only populated if the specific device hardware supports them.

---

# PART A — Legacy Fitbit Web API (`https://api.fitbit.com`)

`-` in a path means "the authenticated user" (`/user/-/`). All data responses
are `.json` unless noted (TCX is XML). Grouped into **27 domains**.

## A1. Authorization & OAuth
| Method | Path | Purpose |
|---|---|---|
| POST | `/oauth2/token` | Exchange code / refresh for an access token |
| POST | `/oauth2/revoke` | Revoke a token |
| POST | `/1.1/oauth2/introspect` | Introspect / validate a token |

**Scopes:** `activity`, `cardio_fitness`, `electrocardiogram`, `heartrate`,
`irregular_rhythm_notifications`, `location`, `nutrition`, `oxygen_saturation`,
`profile`, `respiratory_rate`, `settings`, `sleep`, `social`, `temperature`,
`weight`.

## A2. User / Profile
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/profile.json` | Get the user's profile |
| POST | `/1/user/-/profile.json` | Update the user's profile |
| GET | `/1/user/-/badges.json` | Get the user's badges |

## A3. Devices
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/devices.json` | List paired devices (battery, last sync, etc.) |
| GET | `/1/user/-/devices/tracker/{tracker-id}/alarms.json` | List alarms |
| POST | `/1/user/-/devices/tracker/{tracker-id}/alarms.json` | Add an alarm |
| POST | `/1/user/-/devices/tracker/{tracker-id}/alarms/{alarm-id}.json` | Update an alarm |
| DELETE | `/1/user/-/devices/tracker/{tracker-id}/alarms/{alarm-id}.json` | Delete an alarm |

## A4. Activity (summary, logging, types, favorites, goals)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/date/{date}.json` | Daily activity summary |
| GET | `/1/user/-/activities.json` | Lifetime activity stats |
| POST | `/1/user/-/activities.json` | Log an activity |
| DELETE | `/1/user/-/activities/{activity-log-id}.json` | Delete an activity log |
| GET | `/1/user/-/activities/list.json` | Paginated activity log list |
| GET | `/1/user/-/activities/{log-id}.tcx` | Download an activity as TCX (GPS) |
| GET | `/1/activities.json` | Browse the activity type catalogue |
| GET | `/1/activities/{activity-id}.json` | Activity type details |
| GET | `/1/user/-/activities/favorite.json` | Favorite activities |
| POST | `/1/user/-/activities/favorite/{activity-id}.json` | Add favorite |
| DELETE | `/1/user/-/activities/favorite/{activity-id}.json` | Remove favorite |
| GET | `/1/user/-/activities/frequent.json` | Frequent activities |
| GET | `/1/user/-/activities/recent.json` | Recent activities |
| GET | `/1/user/-/activities/goals/{period}.json` | Get activity goals (`daily`/`weekly`) |
| POST | `/1/user/-/activities/goals/{period}.json` | Set activity goals |

## A5. Activity Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/{resource-path}/date/{date}/{period}.json` | Series by date + period |
| GET | `/1/user/-/activities/{resource-path}/date/{base-date}/{end-date}.json` | Series by date range |
| GET | `/1/user/-/activities/tracker/{resource-path}/date/{date}/{period}.json` | Tracker-only series by period |
| GET | `/1/user/-/activities/tracker/{resource-path}/date/{base-date}/{end-date}.json` | Tracker-only series by range |

`{resource-path}` ∈ `calories`, `caloriesBMR`, `steps`, `distance`, `floors`,
`elevation`, `minutesSedentary`, `minutesLightlyActive`,
`minutesFairlyActive`, `minutesVeryActive`, `activityCalories`.

## A6. Activity Intraday Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/{resource-path}/date/{date}/1d/{detail-level}.json` | Intraday for one day |
| GET | `/1/user/-/activities/{resource-path}/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json` | Intraday + time window |
| GET | `/1/user/-/activities/{resource-path}/date/{base-date}/{end-date}/{detail-level}.json` | Intraday across a range |
| GET | `/1/user/-/activities/{resource-path}/date/{date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json` | Intraday range + time window |

`{detail-level}` ∈ `1min`, `5min`, `15min`. **Intraday access must be approved
by Fitbit per app.**

## A7. Active Zone Minutes (AZM) Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/active-zone-minutes/date/{date}/{period}.json` | AZM by date + period |
| GET | `/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}.json` | AZM by date range |

## A8. Active Zone Minutes (AZM) Intraday
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/active-zone-minutes/date/{date}/1d/{detail-level}.json` | AZM intraday, one day |
| GET | `/1/user/-/activities/active-zone-minutes/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json` | AZM intraday + time window |
| GET | `/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}/{detail-level}.json` | AZM intraday by interval |
| GET | `/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json` | AZM intraday interval + time window |

## A9. Body & Weight (logs + goals)
| Method | Path | Purpose |
|---|---|---|
| POST | `/1/user/-/body/log/weight.json` | Log weight |
| DELETE | `/1/user/-/body/log/weight/{body-weight-log-id}.json` | Delete weight log |
| GET | `/1/user/-/body/log/weight/date/{date}.json` | Weight by date |
| GET | `/1/user/-/body/log/weight/date/{date}/{period}.json` | Weight by date + period |
| GET | `/1/user/-/body/log/weight/date/{base-date}/{end-date}.json` | Weight by date range |
| POST | `/1/user/-/body/log/fat.json` | Log body fat |
| DELETE | `/1/user/-/body/log/fat/{body-fat-log-id}.json` | Delete body fat log |
| GET | `/1/user/-/body/log/fat/date/{date}.json` | Body fat by date |
| GET | `/1/user/-/body/log/fat/date/{date}/{period}.json` | Body fat by date + period |
| GET | `/1/user/-/body/log/fat/date/{base-date}/{end-date}.json` | Body fat by date range |
| GET | `/1/user/-/body/log/{goal-type}/goal.json` | Get body goals (`weight`/`fat`) |
| POST | `/1/user/-/body/log/weight/goal.json` | Set weight goal |
| POST | `/1/user/-/body/log/fat/goal.json` | Set body fat goal |

## A10. Body Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/body/{resource-path}/date/{date}/{period}.json` | Body series by period |
| GET | `/1/user/-/body/{resource-path}/date/{base-date}/{end-date}.json` | Body series by range |

`{resource-path}` ∈ `bmi`, `fat`, `weight`.

## A11. Nutrition (foods, water, meals, goals)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/foods/log/date/{date}.json` | Food log for a day |
| POST | `/1/user/-/foods/log.json` | Log a food |
| POST | `/1/user/-/foods/log/{food-log-id}.json` | Edit a food log |
| DELETE | `/1/user/-/foods/log/{food-log-id}.json` | Delete a food log |
| POST | `/1/user/-/foods.json` | Create a custom food |
| DELETE | `/1/user/-/foods/{food-id}.json` | Delete a custom food |
| GET | `/1/foods/{food-id}.json` | Food details |
| GET | `/1/foods/search.json` | Search the food database |
| GET | `/1/foods/units.json` | Food units |
| GET | `/1/foods/locales.json` | Food locales |
| GET | `/1/user/-/foods/log/favorite.json` | Favorite foods |
| POST | `/1/user/-/foods/log/favorite/{food-id}.json` | Add favorite food |
| DELETE | `/1/user/-/foods/log/favorite/{food-id}.json` | Remove favorite food |
| GET | `/1/user/-/foods/log/frequent.json` | Frequent foods |
| GET | `/1/user/-/foods/log/recent.json` | Recent foods |
| GET | `/1/user/-/foods/log/goal.json` | Get food/calorie goal |
| POST | `/1/user/-/foods/log/goal.json` | Set food/calorie goal |
| GET | `/1/user/-/foods/log/water/date/{date}.json` | Water log for a day |
| POST | `/1/user/-/foods/log/water.json` | Log water |
| POST | `/1/user/-/foods/log/water/{water-log-id}.json` | Update water log |
| DELETE | `/1/user/-/foods/log/water/{water-log-id}.json` | Delete water log |
| GET | `/1/user/-/foods/log/water/goal.json` | Get water goal |
| POST | `/1/user/-/foods/log/water/goal.json` | Set water goal |
| GET | `/1/user/-/meals.json` | List meals |
| POST | `/1/user/-/meals.json` | Create a meal |
| GET | `/1/user/-/meals/{meal-id}.json` | Get a meal |
| POST | `/1/user/-/meals/{meal-id}.json` | Update a meal |
| DELETE | `/1/user/-/meals/{meal-id}.json` | Delete a meal |

## A12. Nutrition Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/foods/log/{resource-path}/date/{date}/{period}.json` | Nutrition series by period |
| GET | `/1/user/-/foods/log/{resource-path}/date/{base-date}/{end-date}.json` | Nutrition series by range |

`{resource-path}` ∈ `caloriesIn`, `water`.

## A13. Sleep (API v1.2)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1.2/user/-/sleep/date/{date}.json` | Sleep logs for a date |
| GET | `/1.2/user/-/sleep/date/{base-date}/{end-date}.json` | Sleep logs for a range |
| GET | `/1.2/user/-/sleep/list.json` | Paginated sleep log list |
| POST | `/1.2/user/-/sleep.json` | Log sleep |
| DELETE | `/1.2/user/-/sleep/{log-id}.json` | Delete a sleep log |
| GET | `/1.2/user/-/sleep/goal.json` | Get sleep goal |
| POST | `/1.2/user/-/sleep/goal.json` | Set sleep goal |

## A14. Heart Rate Time Series
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/heart/date/{date}/{period}.json` | HR by date + period |
| GET | `/1/user/-/activities/heart/date/{base-date}/{end-date}.json` | HR by date range |

## A15. Heart Rate Intraday
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/activities/heart/date/{date}/1d/{detail-level}.json` | HR intraday, one day |
| GET | `/1/user/-/activities/heart/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json` | HR intraday + time window |
| GET | `/1/user/-/activities/heart/date/{date}/{end-date}/{detail-level}.json` | HR intraday by interval |
| GET | `/1/user/-/activities/heart/date/{date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json` | HR intraday interval + time window |

`{detail-level}` ∈ `1sec`, `1min`, `5min`, `15min`.

## A16. Heart Rate Variability (HRV)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/hrv/date/{date}.json` | HRV summary for a date |
| GET | `/1/user/-/hrv/date/{startDate}/{endDate}.json` | HRV summary for a range |

## A17. HRV Intraday
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/hrv/date/{date}/all.json` | HRV intraday for a date |
| GET | `/1/user/-/hrv/date/{startDate}/{endDate}/all.json` | HRV intraday for a range |

## A18. Breathing Rate
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/br/date/{date}.json` | Breathing rate for a date |
| GET | `/1/user/-/br/date/{startDate}/{endDate}.json` | Breathing rate for a range |

## A19. Breathing Rate Intraday
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/br/date/{date}/all.json` | Breathing rate intraday for a date |
| GET | `/1/user/-/br/date/{startDate}/{endDate}/all.json` | Breathing rate intraday for a range |

## A20. Cardio Fitness Score (VO2 Max)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/cardioscore/date/{date}.json` | VO2 Max for a date |
| GET | `/1/user/-/cardioscore/date/{startDate}/{endDate}.json` | VO2 Max for a range |

## A21. SpO2 (Blood Oxygen Saturation)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/spo2/date/{date}.json` | SpO2 summary for a date |
| GET | `/1/user/-/spo2/date/{startDate}/{endDate}.json` | SpO2 summary for a range |
| GET | `/1/user/-/spo2/date/{date}/all.json` | SpO2 intraday for a date |
| GET | `/1/user/-/spo2/date/{startDate}/{endDate}/all.json` | SpO2 intraday for a range |

## A22. Temperature (core & skin)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/temp/core/date/{date}.json` | Core temp for a date |
| GET | `/1/user/-/temp/core/date/{startDate}/{endDate}.json` | Core temp for a range |
| GET | `/1/user/-/temp/skin/date/{date}.json` | Skin temp for a date |
| GET | `/1/user/-/temp/skin/date/{startDate}/{endDate}.json` | Skin temp for a range |

## A23. Electrocardiogram (ECG)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/ecg/list.json` | List recorded ECG readings |

## A24. Irregular Rhythm Notifications (IRN)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/irn/profile.json` | IRN enrollment / profile |
| GET | `/1/user/-/irn/alerts/list.json` | List IRN (AFib) alerts |

## A25. Blood Glucose
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/glucose/date/{date}.json` | Blood glucose for a date *(documented in the Fitbit Web API category list; manually-logged glucose values)* |

## A26. Friends / Social
| Method | Path | Purpose |
|---|---|---|
| GET | `/1.1/user/-/friends.json` | List friends |
| GET | `/1.1/user/-/leaderboard/friends.json` | Friends leaderboard |

## A27. Subscriptions (webhooks)
| Method | Path | Purpose |
|---|---|---|
| GET | `/1/user/-/{collection-path}/apiSubscriptions.json` | List subscriptions |
| POST | `/1/user/-/{collection-path}/apiSubscriptions/{subscription-id}.json` | Add subscription |
| DELETE | `/1/user/-/{collection-path}/apiSubscriptions/{subscription-id}.json` | Delete subscription |

`{collection-path}` ∈ `activities`, `body`, `foods`, `sleep`, `userRevokedAccess`
(or empty for all). Subscriptions push change notifications instead of polling.

---

### Legacy domain summary (for cron planning)

| # | Domain | Read endpoints | Notes for a fetcher |
|---|--------|:---:|---|
| A2 | User/Profile | 2 | Slowly-changing — fetch rarely |
| A3 | Devices | 2 | Poll for `lastSyncTime` / battery |
| A4–A8 | Activity (+AZM, intraday) | many | Daily summary + time series are the workhorses |
| A9–A10 | Body/Weight | 7 | Event-driven; small volume |
| A11–A12 | Nutrition | many | Only if the user logs food |
| A13 | Sleep | 3 | One pull per morning |
| A14–A15 | Heart Rate (+intraday) | 6 | Intraday is high-volume |
| A16–A22 | HRV / Breathing / VO2 / SpO2 / Temp | ~16 | Daily granularity, low volume |
| A23–A25 | ECG / IRN / Glucose | 4 | Event-driven |
| A26 | Friends | 2 | Optional |
| A27 | Subscriptions | 3 | Use **instead of** polling where possible |

**Total: 27 functional domains, ~125 endpoint definitions** (templated
variants — e.g. by-date vs by-range vs intraday — counted separately; the
machine-readable count is emitted by `scripts/gen_endpoints_yaml.py`).

---

# PART B — Google Health API (`https://health.googleapis.com`, `v4`)

The new model inverts the design: instead of one endpoint per metric, there is
a **small, generic set of methods** that operate over a **`{dataType}` path
parameter** (~31 data types). This is "Google's latest Fitbit API" and the
**recommended target**.

- **Base URL:** `https://health.googleapis.com`
- **Discovery doc:** `https://health.googleapis.com/$discovery/rest?version=v4`
- **Auth:** Google OAuth 2.0
- **User selector:** `me` (inferred from token) or an explicit user ID
- **Naming quirk:** in a **path** the data type is **kebab-case** (`body-fat`);
  in a **filter** it is **snake_case** (`body_fat`).

## B1. Core resource — `users.dataTypes.dataPoints`
This single resource covers reading and writing all metrics.

| Method | HTTP | Purpose |
|---|---|---|
| `list` | GET `/v4/users/{user}/dataTypes/{dataType}/dataPoints` | Query data points |
| `create` | POST `/v4/users/{user}/dataTypes/{dataType}/dataPoints` | Create one identifiable data point |
| `batchDelete` | POST `.../dataPoints:batchDelete` | Delete a batch of data points |
| `rollUp` | GET/POST `.../dataPoints:rollUp` | Aggregate over **physical** time intervals |
| `dailyRollUp` | GET/POST `.../dataPoints:dailyRollUp` | Aggregate over **civil/calendar** day intervals |
| `reconcile` | POST `.../dataPoints:reconcile` | Merge data points from multiple sources into one stream |

## B2. Webhooks / Notification channels
| Method | Purpose |
|---|---|
| create notification channel | Register a webhook subscriber |
| delete notification channel | Remove a subscriber |
| list notification channels | Enumerate subscriptions |

Payloads are signed (`X-HEALTHAPI-SIGNATURE`); failed deliveries retry for up
to 7 days. Notification-eligible types include `activityLevel`, `bloodGlucose`,
`dailyRespiratoryRate`, `heartRateVariability`, `height`, `hydrationLog`,
`nutritionLog`, `respiratoryRateSleepSummary`, `runVo2Max`, `sedentaryPeriod`,
`timeInHeartRateZone`, and more.

## B3. Supporting resources
- **`operations`** — standard long-running-operation polling (`get`, `list`).
- **`dataSources`** — describe the origin device/app of data streams.

## B4. Data types (the `{dataType}` parameter — ~31 types)
Each falls into a *shape*: **Interval** (spans a duration), **Sample**
(point-in-time), **Daily** (one summary/day), or **Session** (long event +
metadata).

**Activity & fitness** (scope `…/googlehealth.activity_and_fitness[.readonly]`):
`activeZoneMinutes`, `activeMinutes`, `activityLevel`, `altitude`,
`caloriesInHeartRateZone`, `dailyHeartRateZones`, `distance`, `exercise`
(session), `floors`, `sedentaryPeriod`, `steps`, `timeInHeartRateZone`,
`totalCalories`, `dailyVo2Max`, `runVo2Max`, `vo2Max`, `goals`.

**Health metrics & measurements** (scope
`…/googlehealth.health_metrics_and_measurements[.readonly]`):
`bodyFat`, `weight`, `height`, `heartRate`, `dailyRestingHeartRate`,
`heartRateVariability`, `dailyHeartRateVariability`, `oxygenSaturation`,
`dailyOxygenSaturation`, `respiratoryRate`, `dailyRespiratoryRate`,
`respiratoryRateSleepSummary`, `bloodGlucose`, `bodyTemperature`,
`coreBodyTemperature`, `dailySleepTemperatureDerivations`, and ECG types
(ECG measurement, ECG rhythm classification, AFib analysis windows).

**Sleep** (scope `…/googlehealth.sleep.readonly`): `sleep` (session) +
`sleepSummary`.

**Nutrition** (scope `…/googlehealth.nutrition.readonly`): `nutritionLog`,
`hydrationLog`.

**Location** (scope `…/googlehealth.location.readonly`): location/route data
for exercises.

## B5. OAuth scopes (all prefixed `https://www.googleapis.com/auth/`)
| Scope | Grants |
|---|---|
| `googlehealth.activity_and_fitness` | Read + write activity/fitness data |
| `googlehealth.activity_and_fitness.readonly` | Read activity/fitness data |
| `googlehealth.health_metrics_and_measurements` | Read + write health metrics |
| `googlehealth.health_metrics_and_measurements.readonly` | Read health metrics |
| `googlehealth.sleep.readonly` | Read sleep |
| `googlehealth.nutrition.readonly` | Read nutrition |
| `googlehealth.location.readonly` | Read location |

---

# PART C — Recommendation for the FitLit cron fetcher

1. **Target the Google Health API** (`v4`). The legacy API works today but dies
   in September 2026; building on it now is throwaway work.
2. **Read path:** loop over the data types you care about and call
   `dataPoints.list` (raw points) or `dataPoints.dailyRollUp` (daily summaries
   — ideal for a once-a-day cron). One generic client function handles all
   types thanks to the unified design.
3. **Prefer push over poll:** register a **notification channel (webhook)** for
   event-driven types so the cron is a backstop, not the primary trigger.
4. **Token storage:** persist Google OAuth refresh tokens; access tokens are
   short-lived.
5. **Map of intent:** the legacy domains in Part A tell you *what data exists*;
   the Google data types in Part B tell you *how to fetch it now*. The
   structured catalogue in [`../data/fitbit_endpoints.yaml`](../data/fitbit_endpoints.yaml)
   encodes both for programmatic use.

---

## Sources
- [Fitbit Web API Reference](https://dev.fitbit.com/build/reference/web-api/)
- [Fitbit Web API Explorer (Swagger)](https://dev.fitbit.com/build/reference/web-api/explore/)
- [Fitbit Web API Intraday](https://dev.fitbit.com/build/reference/web-api/intraday/)
- [Fitbit Web API Data Dictionary v9 (PDF)](https://assets.ctfassets.net/0ltkef2fmze1/45IN5bvBS827grKEsA8ZB0/648f3778acc936961f0572590c005ef0/Fitbit-Web-API-Data-Dictionary-Downloadable-Version-2.pdf)
- [`fitbit-web-api` OpenAPI client (PyPI)](https://pypi.org/project/fitbit-web-api/) · [GitHub](https://github.com/chemelli74/fitbit-web-api)
- [About the Google Health API](https://developers.google.com/health/about)
- [Google Health API REST reference](https://developers.google.com/health/reference/rest)
- [Google Health API `users.dataTypes.dataPoints`](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints)
- [Google Health API data types](https://developers.google.com/health/data-types)
- [Google Health API scopes](https://developers.google.com/health/scopes) · [setup](https://developers.google.com/health/setup) · [webhooks](https://developers.google.com/health/webhooks)
- [Google Health API ↔ Fitbit migration specs](https://developers.google.com/health/migration/api-specifications)
- [Fitbit → Google Health migration timeline (Validic)](https://help.validic.com/space/VCS/5535203416/Fitbit+to+Google+Health+API+Developer+Migration+Timeline)
- [The complete guide: how the new Google Health API works (Terra)](https://tryterra.co/blog/everything-you-need-to-know-about-google-health-new-api)
