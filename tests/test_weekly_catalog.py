from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fitlit import weekly_catalog
from fitlit.gmail_service import (
    Notification,
    NotificationStore,
    _weekly_candidate,
    dispatch,
    preview_weekly,
)
from fitlit.gmail_templates import report, weekly_report

PACIFIC = ZoneInfo("America/Los_Angeles")


def _days(start: date, values: list[float], key: str = "value") -> list[dict]:
    return [
        {"day": (start + timedelta(days=index)).isoformat(), key: value}
        for index, value in enumerate(values)
    ]


def sample_catalog() -> dict:
    start = date(2026, 7, 13)
    end = date(2026, 7, 19)
    prior = start - timedelta(days=7)
    activity = [
        *[
            {
                "day": (prior + timedelta(days=index)).isoformat(),
                "steps": 8_000,
                "calories_out": 2_400,
            }
            for index in range(7)
        ],
        *[
            {
                "day": (start + timedelta(days=index)).isoformat(),
                "steps": 10_000 + index * 500,
                "calories_out": 2_600 + index * 20,
            }
            for index in range(7)
        ],
    ]
    sleep = [
        *[
            {
                "day": (prior + timedelta(days=index)).isoformat(),
                "hours_asleep": 7.0,
                "efficiency_pct": 90.0,
                "bedtime": "23:00",
            }
            for index in range(7)
        ],
        *[
            {
                "day": (start + timedelta(days=index)).isoformat(),
                "hours_asleep": 7.5,
                "efficiency_pct": 92.0,
                "bedtime": "23:15",
            }
            for index in range(7)
        ],
    ]
    sessions = [
        {
            "id": "session-1",
            "day": "2026-07-15",
            "start": "6:00 PM",
            "type": "Workout",
            "name": "Strength",
            "duration_min": 60,
            "active_duration_min": 55,
            "calories": 500,
            "distance_km": 0.0,
            "steps": 0,
            "active_zone_minutes": 35,
            "avg_hr": 128,
            "is_training": True,
            "quality_flags": [],
        },
        {
            "id": "session-2",
            "day": "2026-07-16",
            "start": "7:00 PM",
            "type": "Walking",
            "name": "Bad <record>",
            "duration_min": 1,
            "active_duration_min": 180,
            "calories": 1_400,
            "distance_km": 8.0,
            "steps": 10_000,
            "active_zone_minutes": 0,
            "avg_hr": 60,
            "is_training": False,
            "quality_flags": ["active duration exceeds elapsed window"],
        },
    ]
    hrv = [
        *_days(prior, [60] * 7),
        *_days(start, [50, 62, 63, 64, 65, 66, 67]),
    ]
    resting_hr = [
        *_days(prior, [60] * 7),
        *_days(start, [65, 60, 60, 60, 60, 60, 60]),
    ]
    oxygen = [
        *[
            {"day": (prior + timedelta(days=index)).isoformat(), "value": 96.0, "lower": 94.0}
            for index in range(7)
        ],
        *[
            {"day": (start + timedelta(days=index)).isoformat(), "value": 97.0, "lower": 95.0}
            for index in range(7)
        ],
    ]
    respiratory = [
        *_days(prior, [14.0] * 7),
        *_days(start, [13.5] * 7),
    ]
    return weekly_catalog.summarize(
        start,
        end,
        activity=activity,
        sessions=sessions,
        sleep=sleep,
        hrv=hrv,
        resting_hr=resting_hr,
        oxygen=oxygen,
        respiratory=respiratory,
    )


class WeeklyCatalogTests(unittest.TestCase):
    def test_delivery_window_is_sunday_evening_with_monday_retry(self) -> None:
        sunday_early = datetime(2026, 7, 19, 19, 59, tzinfo=PACIFIC)
        sunday_due = datetime(2026, 7, 19, 20, 0, tzinfo=PACIFIC)
        monday_retry = datetime(2026, 7, 20, 11, 59, tzinfo=PACIFIC)
        monday_expired = datetime(2026, 7, 20, 12, 0, tzinfo=PACIFIC)
        self.assertIsNone(weekly_catalog.delivery_week(sunday_early))
        self.assertEqual(
            (date(2026, 7, 13), date(2026, 7, 19)),
            weekly_catalog.delivery_week(sunday_due),
        )
        self.assertEqual(
            (date(2026, 7, 13), date(2026, 7, 19)),
            weekly_catalog.delivery_week(monday_retry),
        )
        self.assertIsNone(weekly_catalog.delivery_week(monday_expired))

    def test_flagged_sessions_remain_visible_but_do_not_distort_totals(self) -> None:
        catalog = sample_catalog()
        self.assertEqual(2, catalog["training"]["sessions"])
        self.assertEqual(1, catalog["training"]["trusted_sessions"])
        self.assertEqual(1, catalog["training"]["training_sessions"])
        self.assertEqual(500, catalog["training"]["exercise_calories"])
        self.assertEqual(60, catalog["training"]["duration_min"])
        self.assertEqual(1, len(catalog["training"]["quality_flags"]))

    def test_overlapping_sleep_records_use_longest_sleep_opportunity(self) -> None:
        catalog = sample_catalog()
        start = date.fromisoformat(catalog["week"]["start"])
        duplicate = {
            "day": start.isoformat(),
            "hours_asleep": 1.75,
            "efficiency_pct": 78.0,
            "bedtime": "13:40",
        }
        base = sample_catalog()
        sleep = [
            *[
                {
                    "day": (start - timedelta(days=7) + timedelta(days=index)).isoformat(),
                    "hours_asleep": 7.0,
                    "efficiency_pct": 90.0,
                    "bedtime": "23:00",
                }
                for index in range(7)
            ],
            *[
                {
                    "day": (start + timedelta(days=index)).isoformat(),
                    "hours_asleep": 7.5,
                    "efficiency_pct": 92.0,
                    "bedtime": "23:15",
                }
                for index in range(7)
            ],
            duplicate,
        ]
        rebuilt = weekly_catalog.summarize(
            start,
            date.fromisoformat(catalog["week"]["end"]),
            activity=[
                {
                    "day": row["day"],
                    "steps": row["steps"],
                    "calories_out": row["calories_out"],
                }
                for row in [
                    *[
                        {
                            "day": (start - timedelta(days=7) + timedelta(days=index)).isoformat(),
                            "steps": 8_000,
                            "calories_out": 2_400,
                        }
                        for index in range(7)
                    ],
                    *[
                        {
                            "day": (start + timedelta(days=index)).isoformat(),
                            "steps": 10_000,
                            "calories_out": 2_600,
                        }
                        for index in range(7)
                    ],
                ]
            ],
            sessions=[],
            sleep=sleep,
            hrv=[],
            resting_hr=[],
            oxygen=[],
            respiratory=[],
        )
        self.assertEqual(7, rebuilt["sleep"]["nights"])
        self.assertEqual(7.5, rebuilt["sleep"]["avg_hours"])
        self.assertEqual(0.0, rebuilt["sleep"]["sleep_debt_hours"])
        self.assertEqual(7.5, rebuilt["daily"][0]["sleep_hours"])

    def test_recovery_trends_and_strain_proxy_use_prior_week(self) -> None:
        catalog = sample_catalog()
        self.assertEqual(1, len(catalog["recovery"]["strain_flag_days"]))
        self.assertGreater(catalog["recovery"]["hrv_change_pct"], 0)
        self.assertEqual(1.0, catalog["recovery"]["spo2_change_points"])
        self.assertEqual(-0.5, catalog["recovery"]["respiratory_change"])
        self.assertEqual(7, catalog["coverage"]["oxygen_days"])

    def test_missing_optional_vitals_render_as_unavailable(self) -> None:
        catalog = sample_catalog()
        catalog["recovery"].update({
            "avg_spo2_pct": None,
            "lowest_spo2_bound_pct": None,
            "avg_respiratory_rate": None,
        })
        rendered = weekly_report(catalog)
        self.assertIn("Blood oxygen", rendered.text)
        self.assertIn("—", rendered.text)
        self.assertIn("No lower-bound data", rendered.text)

    def test_weekly_report_is_detailed_and_escapes_session_names(self) -> None:
        rendered = weekly_report(sample_catalog())
        self.assertIn("Seven-day rhythm", rendered.html)
        self.assertIn("Workout catalog", rendered.html)
        self.assertIn("Recovery and physiology", rendered.html)
        self.assertIn("Next-week focus", rendered.html)
        self.assertIn("Bad &lt;record&gt;", rendered.html)
        self.assertNotIn("Bad <record>", rendered.html)
        self.assertIn("RECOVERY AND PHYSIOLOGY", rendered.text)

    def test_ai_payload_is_shallow_and_identifier_free(self) -> None:
        payload = weekly_catalog.ai_payload(sample_catalog())
        self.assertLessEqual(len(payload), 30)
        self.assertNotIn("sessions", payload)
        self.assertNotIn("week", payload)
        self.assertNotIn("name", payload)


class WeeklyDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.temp.name) / "notifications.db")
        self.now = datetime(2026, 7, 19, 20, 0, tzinfo=PACIFIC)
        self.sent = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_candidate_uses_immutable_week_key_and_is_sent_once(self) -> None:
        catalog = sample_catalog()
        with patch("fitlit.gmail_service.weekly_catalog.build", return_value=catalog):
            item = _weekly_candidate(self.now)
        self.assertIsNotNone(item)
        self.assertEqual("weekly:2026-07-19", item.event_key)
        self.assertEqual("weekly", item.kind)
        self.assertTrue(item.mandatory)

        def sender(subject: str, text: str, html: str) -> str:
            self.sent.append(subject)
            return "weekly-message"

        with patch("fitlit.gmail_service.ai_insights.generate", return_value=None):
            dispatch([item, item], self.store, self.now, sender=sender)
        self.assertEqual(1, len(self.sent))

    def test_preview_writes_html_without_touching_ledger(self) -> None:
        catalog = sample_catalog()
        output = Path(self.temp.name) / "weekly.html"
        with patch("fitlit.gmail_service.weekly_catalog.build", return_value=catalog):
            result = preview_weekly(self.now, output)
        self.assertTrue(output.exists())
        self.assertIn("Weekly performance catalog", output.read_text())
        self.assertEqual(0, self.store.attempted_today("2026-07-19"))
        self.assertEqual(str(output), result["html_path"])

    def test_full_sunday_cap_retries_same_catalog_on_monday(self) -> None:
        fillers = []
        for index in range(5):
            fillers.append(Notification(
                event_key=f"filler:{index}",
                kind="test",
                report=report(
                    subject=f"Filler {index}",
                    kicker="Test",
                    title="Filler",
                    subtitle="",
                    metrics=[],
                    details=[],
                ),
                mandatory=True,
            ))

        def sender(subject: str, text: str, html: str) -> str:
            self.sent.append(subject)
            return f"message-{len(self.sent)}"

        dispatch(fillers, self.store, self.now, sender=sender)
        catalog = sample_catalog()
        with patch("fitlit.gmail_service.weekly_catalog.build", return_value=catalog):
            sunday = _weekly_candidate(self.now)
        sunday_result = dispatch([sunday], self.store, self.now, sender=sender)
        self.assertEqual([], sunday_result["sent"])

        monday = datetime(2026, 7, 20, 8, 0, tzinfo=PACIFIC)
        with patch("fitlit.gmail_service.weekly_catalog.build", return_value=catalog):
            retry = _weekly_candidate(monday)
        monday_result = dispatch([retry], self.store, monday, sender=sender)
        self.assertEqual("weekly:2026-07-19", retry.event_key)
        self.assertEqual(1, len(monday_result["sent"]))


if __name__ == "__main__":
    unittest.main()
