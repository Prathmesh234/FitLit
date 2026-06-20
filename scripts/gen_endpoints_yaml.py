"""Generate data/fitbit_endpoints.yaml from a Python definition.

Keeping the source as Python guarantees the emitted YAML is always valid
(paths contain `{...}` braces that are painful to hand-write in YAML flow
style). Run with:  uv run python scripts/gen_endpoints_yaml.py
"""
from __future__ import annotations

import pathlib

import yaml

HEADER = """\
# FitLit - machine-readable API endpoint catalogue
# Generated during the research stage (2026-06-19) by scripts/gen_endpoints_yaml.py
# Two APIs are catalogued: the legacy Fitbit Web API (deprecated Sep 2026) and
# the new Google Health API v4 (recommended target).
# See ../docs/fitbit-api-research.md for the full human-readable write-up.
# Edit the generator, not this file.
"""


def ep(method: str, path: str, **extra) -> dict:
    d = {"method": method, "path": path}
    d.update(extra)
    return d


LEGACY_DOMAINS: dict = {
    "authorization": {"endpoints": [
        ep("POST", "/oauth2/token"),
        ep("POST", "/oauth2/revoke"),
        ep("POST", "/1.1/oauth2/introspect"),
    ]},
    "user": {"endpoints": [
        ep("GET", "/1/user/-/profile.json"),
        ep("POST", "/1/user/-/profile.json"),
        ep("GET", "/1/user/-/badges.json"),
    ]},
    "devices": {"endpoints": [
        ep("GET", "/1/user/-/devices.json"),
        ep("GET", "/1/user/-/devices/tracker/{tracker-id}/alarms.json"),
        ep("POST", "/1/user/-/devices/tracker/{tracker-id}/alarms.json"),
        ep("POST", "/1/user/-/devices/tracker/{tracker-id}/alarms/{alarm-id}.json"),
        ep("DELETE", "/1/user/-/devices/tracker/{tracker-id}/alarms/{alarm-id}.json"),
    ]},
    "activity": {"endpoints": [
        ep("GET", "/1/user/-/activities/date/{date}.json"),
        ep("GET", "/1/user/-/activities.json"),
        ep("POST", "/1/user/-/activities.json"),
        ep("DELETE", "/1/user/-/activities/{activity-log-id}.json"),
        ep("GET", "/1/user/-/activities/list.json"),
        ep("GET", "/1/user/-/activities/{log-id}.tcx"),
        ep("GET", "/1/activities.json"),
        ep("GET", "/1/activities/{activity-id}.json"),
        ep("GET", "/1/user/-/activities/favorite.json"),
        ep("POST", "/1/user/-/activities/favorite/{activity-id}.json"),
        ep("DELETE", "/1/user/-/activities/favorite/{activity-id}.json"),
        ep("GET", "/1/user/-/activities/frequent.json"),
        ep("GET", "/1/user/-/activities/recent.json"),
        ep("GET", "/1/user/-/activities/goals/{period}.json"),
        ep("POST", "/1/user/-/activities/goals/{period}.json"),
    ]},
    "activity_time_series": {
        "resource_paths": ["calories", "caloriesBMR", "steps", "distance", "floors",
                           "elevation", "minutesSedentary", "minutesLightlyActive",
                           "minutesFairlyActive", "minutesVeryActive", "activityCalories"],
        "endpoints": [
            ep("GET", "/1/user/-/activities/{resource-path}/date/{date}/{period}.json"),
            ep("GET", "/1/user/-/activities/{resource-path}/date/{base-date}/{end-date}.json"),
            ep("GET", "/1/user/-/activities/tracker/{resource-path}/date/{date}/{period}.json"),
            ep("GET", "/1/user/-/activities/tracker/{resource-path}/date/{base-date}/{end-date}.json"),
        ]},
    "activity_intraday": {
        "requires_approval": True,
        "detail_levels": ["1min", "5min", "15min"],
        "endpoints": [
            ep("GET", "/1/user/-/activities/{resource-path}/date/{date}/1d/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/{resource-path}/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json"),
            ep("GET", "/1/user/-/activities/{resource-path}/date/{base-date}/{end-date}/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/{resource-path}/date/{date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json"),
        ]},
    "active_zone_minutes_time_series": {"endpoints": [
        ep("GET", "/1/user/-/activities/active-zone-minutes/date/{date}/{period}.json"),
        ep("GET", "/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}.json"),
    ]},
    "active_zone_minutes_intraday": {
        "detail_levels": ["1min", "5min", "15min"],
        "endpoints": [
            ep("GET", "/1/user/-/activities/active-zone-minutes/date/{date}/1d/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/active-zone-minutes/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json"),
            ep("GET", "/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/active-zone-minutes/date/{start-date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json"),
        ]},
    "body": {"endpoints": [
        ep("POST", "/1/user/-/body/log/weight.json"),
        ep("DELETE", "/1/user/-/body/log/weight/{body-weight-log-id}.json"),
        ep("GET", "/1/user/-/body/log/weight/date/{date}.json"),
        ep("GET", "/1/user/-/body/log/weight/date/{date}/{period}.json"),
        ep("GET", "/1/user/-/body/log/weight/date/{base-date}/{end-date}.json"),
        ep("POST", "/1/user/-/body/log/fat.json"),
        ep("DELETE", "/1/user/-/body/log/fat/{body-fat-log-id}.json"),
        ep("GET", "/1/user/-/body/log/fat/date/{date}.json"),
        ep("GET", "/1/user/-/body/log/fat/date/{date}/{period}.json"),
        ep("GET", "/1/user/-/body/log/fat/date/{base-date}/{end-date}.json"),
        ep("GET", "/1/user/-/body/log/{goal-type}/goal.json"),
        ep("POST", "/1/user/-/body/log/weight/goal.json"),
        ep("POST", "/1/user/-/body/log/fat/goal.json"),
    ]},
    "body_time_series": {
        "resource_paths": ["bmi", "fat", "weight"],
        "endpoints": [
            ep("GET", "/1/user/-/body/{resource-path}/date/{date}/{period}.json"),
            ep("GET", "/1/user/-/body/{resource-path}/date/{base-date}/{end-date}.json"),
        ]},
    "nutrition": {"endpoints": [
        ep("GET", "/1/user/-/foods/log/date/{date}.json"),
        ep("POST", "/1/user/-/foods/log.json"),
        ep("POST", "/1/user/-/foods/log/{food-log-id}.json"),
        ep("DELETE", "/1/user/-/foods/log/{food-log-id}.json"),
        ep("POST", "/1/user/-/foods.json"),
        ep("DELETE", "/1/user/-/foods/{food-id}.json"),
        ep("GET", "/1/foods/{food-id}.json"),
        ep("GET", "/1/foods/search.json"),
        ep("GET", "/1/foods/units.json"),
        ep("GET", "/1/foods/locales.json"),
        ep("GET", "/1/user/-/foods/log/favorite.json"),
        ep("POST", "/1/user/-/foods/log/favorite/{food-id}.json"),
        ep("DELETE", "/1/user/-/foods/log/favorite/{food-id}.json"),
        ep("GET", "/1/user/-/foods/log/frequent.json"),
        ep("GET", "/1/user/-/foods/log/recent.json"),
        ep("GET", "/1/user/-/foods/log/goal.json"),
        ep("POST", "/1/user/-/foods/log/goal.json"),
        ep("GET", "/1/user/-/foods/log/water/date/{date}.json"),
        ep("POST", "/1/user/-/foods/log/water.json"),
        ep("POST", "/1/user/-/foods/log/water/{water-log-id}.json"),
        ep("DELETE", "/1/user/-/foods/log/water/{water-log-id}.json"),
        ep("GET", "/1/user/-/foods/log/water/goal.json"),
        ep("POST", "/1/user/-/foods/log/water/goal.json"),
        ep("GET", "/1/user/-/meals.json"),
        ep("POST", "/1/user/-/meals.json"),
        ep("GET", "/1/user/-/meals/{meal-id}.json"),
        ep("POST", "/1/user/-/meals/{meal-id}.json"),
        ep("DELETE", "/1/user/-/meals/{meal-id}.json"),
    ]},
    "nutrition_time_series": {
        "resource_paths": ["caloriesIn", "water"],
        "endpoints": [
            ep("GET", "/1/user/-/foods/log/{resource-path}/date/{date}/{period}.json"),
            ep("GET", "/1/user/-/foods/log/{resource-path}/date/{base-date}/{end-date}.json"),
        ]},
    "sleep": {"endpoints": [
        ep("GET", "/1.2/user/-/sleep/date/{date}.json"),
        ep("GET", "/1.2/user/-/sleep/date/{base-date}/{end-date}.json"),
        ep("GET", "/1.2/user/-/sleep/list.json"),
        ep("POST", "/1.2/user/-/sleep.json"),
        ep("DELETE", "/1.2/user/-/sleep/{log-id}.json"),
        ep("GET", "/1.2/user/-/sleep/goal.json"),
        ep("POST", "/1.2/user/-/sleep/goal.json"),
    ]},
    "heart_rate_time_series": {"endpoints": [
        ep("GET", "/1/user/-/activities/heart/date/{date}/{period}.json"),
        ep("GET", "/1/user/-/activities/heart/date/{base-date}/{end-date}.json"),
    ]},
    "heart_rate_intraday": {
        "detail_levels": ["1sec", "1min", "5min", "15min"],
        "endpoints": [
            ep("GET", "/1/user/-/activities/heart/date/{date}/1d/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/heart/date/{date}/1d/{detail-level}/time/{start-time}/{end-time}.json"),
            ep("GET", "/1/user/-/activities/heart/date/{date}/{end-date}/{detail-level}.json"),
            ep("GET", "/1/user/-/activities/heart/date/{date}/{end-date}/{detail-level}/time/{start-time}/{end-time}.json"),
        ]},
    "heart_rate_variability": {"endpoints": [
        ep("GET", "/1/user/-/hrv/date/{date}.json"),
        ep("GET", "/1/user/-/hrv/date/{startDate}/{endDate}.json"),
    ]},
    "heart_rate_variability_intraday": {"endpoints": [
        ep("GET", "/1/user/-/hrv/date/{date}/all.json"),
        ep("GET", "/1/user/-/hrv/date/{startDate}/{endDate}/all.json"),
    ]},
    "breathing_rate": {"endpoints": [
        ep("GET", "/1/user/-/br/date/{date}.json"),
        ep("GET", "/1/user/-/br/date/{startDate}/{endDate}.json"),
    ]},
    "breathing_rate_intraday": {"endpoints": [
        ep("GET", "/1/user/-/br/date/{date}/all.json"),
        ep("GET", "/1/user/-/br/date/{startDate}/{endDate}/all.json"),
    ]},
    "cardio_fitness_vo2max": {"endpoints": [
        ep("GET", "/1/user/-/cardioscore/date/{date}.json"),
        ep("GET", "/1/user/-/cardioscore/date/{startDate}/{endDate}.json"),
    ]},
    "spo2": {"endpoints": [
        ep("GET", "/1/user/-/spo2/date/{date}.json"),
        ep("GET", "/1/user/-/spo2/date/{startDate}/{endDate}.json"),
        ep("GET", "/1/user/-/spo2/date/{date}/all.json"),
        ep("GET", "/1/user/-/spo2/date/{startDate}/{endDate}/all.json"),
    ]},
    "temperature": {"endpoints": [
        ep("GET", "/1/user/-/temp/core/date/{date}.json"),
        ep("GET", "/1/user/-/temp/core/date/{startDate}/{endDate}.json"),
        ep("GET", "/1/user/-/temp/skin/date/{date}.json"),
        ep("GET", "/1/user/-/temp/skin/date/{startDate}/{endDate}.json"),
    ]},
    "electrocardiogram": {"endpoints": [
        ep("GET", "/1/user/-/ecg/list.json"),
    ]},
    "irregular_rhythm_notifications": {"endpoints": [
        ep("GET", "/1/user/-/irn/profile.json"),
        ep("GET", "/1/user/-/irn/alerts/list.json"),
    ]},
    "blood_glucose": {"endpoints": [
        ep("GET", "/1/user/-/glucose/date/{date}.json"),
    ]},
    "friends": {"endpoints": [
        ep("GET", "/1.1/user/-/friends.json"),
        ep("GET", "/1.1/user/-/leaderboard/friends.json"),
    ]},
    "subscriptions": {
        "collection_paths": ["activities", "body", "foods", "sleep", "userRevokedAccess"],
        "endpoints": [
            ep("GET", "/1/user/-/{collection-path}/apiSubscriptions.json"),
            ep("POST", "/1/user/-/{collection-path}/apiSubscriptions/{subscription-id}.json"),
            ep("DELETE", "/1/user/-/{collection-path}/apiSubscriptions/{subscription-id}.json"),
        ]},
}

CATALOGUE = {
    "meta": {
        "researched_on": "2026-06-19",
        "recommended_api": "google_health",
    },
    "legacy_fitbit_web_api": {
        "base_url": "https://api.fitbit.com",
        "status": "deprecated",
        "deprecation": "Legacy Web API turned down September 2026",
        "user_placeholder": "-",
        "scopes": ["activity", "cardio_fitness", "electrocardiogram", "heartrate",
                   "irregular_rhythm_notifications", "location", "nutrition",
                   "oxygen_saturation", "profile", "respiratory_rate", "settings",
                   "sleep", "social", "temperature", "weight"],
        "domains": LEGACY_DOMAINS,
    },
    "google_health_api": {
        "base_url": "https://health.googleapis.com",
        "version": "v4",
        "status": "current",
        "discovery": "https://health.googleapis.com/$discovery/rest?version=v4",
        "user_placeholder": "me",
        "naming": "kebab-case in path (body-fat), snake_case in filters (body_fat)",
        "scopes": {
            "activity_and_fitness": "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
            "activity_and_fitness_readonly": "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "health_metrics_and_measurements": "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
            "health_metrics_and_measurements_readonly": "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
            "sleep_readonly": "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "nutrition_readonly": "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
            "location_readonly": "https://www.googleapis.com/auth/googlehealth.location.readonly",
            "ecg_readonly": "https://www.googleapis.com/auth/googlehealth.ecg.readonly",
            "irn_readonly": "https://www.googleapis.com/auth/googlehealth.irn.readonly",
        },
        "resources": {
            "data_points": {"endpoints": [
                ep("GET", "/v4/users/{user}/dataTypes/{dataType}/dataPoints", op="list"),
                ep("POST", "/v4/users/{user}/dataTypes/{dataType}/dataPoints", op="create"),
                ep("POST", "/v4/users/{user}/dataTypes/{dataType}/dataPoints:batchDelete", op="batchDelete"),
                ep("POST", "/v4/users/{user}/dataTypes/{dataType}/dataPoints:rollUp", op="rollUp"),
                ep("POST", "/v4/users/{user}/dataTypes/{dataType}/dataPoints:dailyRollUp", op="dailyRollUp"),
                ep("POST", "/v4/users/{user}/dataTypes/{dataType}/dataPoints:reconcile", op="reconcile"),
            ]},
            "notification_channels": {"endpoints": [
                {"op": "create"}, {"op": "delete"}, {"op": "list"},
            ]},
            "operations": {"endpoints": [{"op": "get"}, {"op": "list"}]},
            "data_sources": {"endpoints": [{"op": "list"}]},
        },
        "data_types": {
            "activity_and_fitness": ["activeZoneMinutes", "activeMinutes", "activityLevel",
                "altitude", "caloriesInHeartRateZone", "dailyHeartRateZones", "distance",
                "exercise", "floors", "sedentaryPeriod", "steps", "timeInHeartRateZone",
                "totalCalories", "dailyVo2Max", "runVo2Max", "vo2Max"],
            "health_metrics_and_measurements": ["bodyFat", "weight", "height", "heartRate",
                "dailyRestingHeartRate", "heartRateVariability", "dailyHeartRateVariability",
                "oxygenSaturation", "dailyOxygenSaturation",
                "dailyRespiratoryRate", "respiratoryRateSleepSummary", "bloodGlucose",
                "coreBodyTemperature", "dailySleepTemperatureDerivations",
                "electrocardiogram", "irregularRhythmNotification"],
            "sleep": ["sleep"],
            "nutrition": ["nutritionLog", "hydrationLog"],
        },
        # These types reject `dataPoints.list` (HTTP 400) and are fetched via
        # `dataPoints:dailyRollUp` instead. See fitlit/client.py.
        "list_unsupported_use_daily_rollup": ["totalCalories", "floors",
            "caloriesInHeartRateZone"],
        # `electrocardiogram` requires the ecg.readonly scope; the
        # irregular-rhythm-notification type requires irn.readonly.
        "scope_gated_types": {
            "electrocardiogram": "ecg_readonly",
            "irregularRhythmNotification": "irn_readonly",
        },
    },
}


def main() -> None:
    out = pathlib.Path(__file__).resolve().parent.parent / "data" / "fitbit_endpoints.yaml"
    body = yaml.safe_dump(CATALOGUE, sort_keys=False, default_flow_style=False, width=100)
    out.write_text(HEADER + "\n" + body)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
