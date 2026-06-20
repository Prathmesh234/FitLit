"""Seed the SQLite databases with realistic dummy data points.

Runs everything through the real ``fitlit.storage`` path (Pydantic models ->
upsert), so it exercises the exact code the cron fetchers use. Generates a few
hours of minutely steps + heart rate, plus sample body/sleep/glucose points and
one unmodeled type, then prints what landed.

    uv run python scripts/seed_dummy_data.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fitlit import storage

# A fixed base time so re-runs upsert onto the same rows (proves dedup) rather
# than growing forever.
BASE = datetime(2026, 6, 19, 6, 0, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def steps_points(minutes: int) -> list[dict]:
    points = []
    for i in range(minutes):
        start = BASE + timedelta(minutes=i)
        # a plausible step pattern: mostly walking, some idle minutes
        count = (i * 37) % 130
        points.append({
            "name": f"users/me/dataTypes/steps/dataPoints/seed-step-{i:04d}",
            "dataSource": {"recordingMethod": "PASSIVELY_MEASURED", "platform": "FITBIT",
                           "device": {"displayName": "Charge 6"}},
            "steps": {
                "interval": {"startTime": iso(start), "endTime": iso(start + timedelta(minutes=1))},
                "count": str(count),  # int64 arrives as a JSON string
            },
        })
    return points


def heart_points(minutes: int) -> list[dict]:
    points = []
    for i in range(minutes):
        t = BASE + timedelta(minutes=i)
        bpm = 60 + (i % 35)  # resting-ish, wandering
        points.append({
            "name": f"users/me/dataTypes/heartRate/dataPoints/seed-hr-{i:04d}",
            "dataSource": {"platform": "FITBIT", "device": {"displayName": "Sense 2"}},
            "heartRate": {"sampleTime": iso(t), "beatsPerMinute": bpm,
                          "heartRateMetadata": {"motionContext": "SEDENTARY"}},
        })
    return points


def weight_points() -> list[dict]:
    out = []
    for d in range(7):  # a week of morning weigh-ins
        t = BASE + timedelta(days=-d)
        out.append({
            "name": f"users/me/dataTypes/weight/dataPoints/seed-wt-{d}",
            "dataSource": {"recordingMethod": "MANUAL", "platform": "FITBIT"},
            "weight": {"sampleTime": iso(t), "weightKg": round(74.5 + d * 0.1, 1)},
        })
    return out


def glucose_points() -> list[dict]:
    return [{
        "name": "users/me/dataTypes/bloodGlucose/dataPoints/seed-bg-1",
        "dataSource": {"recordingMethod": "MANUAL", "platform": "FITBIT"},
        "bloodGlucose": {"sampleTime": iso(BASE), "bloodGlucoseMilligramsPerDeciliter": 98.0,
                         "mealType": "FASTING"},
    }]


def sleep_points() -> list[dict]:
    start = BASE - timedelta(hours=7)
    return [{
        "name": "users/me/dataTypes/sleep/dataPoints/seed-sleep-1",
        "dataSource": {"recordingMethod": "AUTO_DETECTED", "platform": "FITBIT",
                       "device": {"displayName": "Sense 2"}},
        "sleep": {
            "interval": {"startTime": iso(start), "endTime": iso(BASE)},
            "sleepType": "MAIN",
            "sleepStages": [
                {"stage": "LIGHT", "interval": {"startTime": iso(start),
                                                "endTime": iso(start + timedelta(hours=3))}},
                {"stage": "DEEP", "interval": {"startTime": iso(start + timedelta(hours=3)),
                                               "endTime": iso(start + timedelta(hours=5))}},
                {"stage": "REM", "interval": {"startTime": iso(start + timedelta(hours=5)),
                                              "endTime": iso(BASE)}},
            ],
        },
    }]


def respiratory_points() -> list[dict]:
    # An UNMODELED type: must still store the full payload via raw_json/data_json.
    return [{
        "name": "users/me/dataTypes/dailyRespiratoryRate/dataPoints/seed-rr-1",
        "dataSource": {"platform": "FITBIT"},
        "dailyRespiratoryRate": {"sampleTime": iso(BASE), "breathsPerMinute": 14.3,
                            "confidence": "HIGH", "futureFieldGoogleMightAdd": [1, 2, 3]},
    }]


def main() -> None:
    jobs = [
        ("live_activity", "steps", steps_points(180)),       # 3h of minutely steps
        ("heart", "heartRate", heart_points(180)),           # 3h of minutely HR
        ("body", "weight", weight_points()),
        ("body", "bloodGlucose", glucose_points()),
        ("sleep", "sleep", sleep_points()),
        ("daily_summaries", "dailyRespiratoryRate", respiratory_points()),
    ]
    print("Seeding dummy data through fitlit.storage ...\n")
    for fetcher, data_type, points in jobs:
        n = storage.store(fetcher, data_type, points, NOW)
        print(f"  {fetcher:>16}.{data_type:<18} -> stored {n:>4} points "
              f"({storage.db_path_for_fetcher(fetcher)})")
    print("\nStored row counts (storage.stats):")
    import json
    print(json.dumps(storage.stats(), indent=2))


if __name__ == "__main__":
    main()
