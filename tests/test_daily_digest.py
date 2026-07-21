from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fitlit import daily_digest
from fitlit.daily_templates import day_report, sleep_report
from fitlit.gmail_service import _evening_fill, _sleep_candidate
from fitlit.gmail_templates import append_ai_insight

PACIFIC = ZoneInfo("America/Los_Angeles")
DAY = date(2026, 7, 20)


def sleep_row(
    wake_day: date,
    *,
    record_id: str,
    asleep: int,
    in_bed: int,
    efficiency: float,
) -> dict:
    end = datetime(wake_day.year, wake_day.month, wake_day.day, 7, 0, tzinfo=PACIFIC)
    start = end - timedelta(hours=8)
    return {
        "record_id": record_id,
        "day": wake_day.isoformat(),
        "start": start,
        "end": end,
        "bedtime": "23:00",
        "wake": "07:00",
        "minutes_asleep": asleep,
        "minutes_in_bed": in_bed,
        "selection_minutes": in_bed,
        "hours_asleep": round(asleep / 60, 2),
        "efficiency_pct": efficiency,
        "awake_min": in_bed - asleep,
        "latency_min": 12,
        "stages": {"deep": 80, "rem": 95, "light": asleep - 175, "awake": in_bed - asleep},
    }


def sleep_digest() -> dict:
    current = sleep_row(DAY, record_id="primary", asleep=430, in_bed=470, efficiency=91.5)
    return {
        "date": daily_digest.date_context(DAY),
        "sleep": current,
        "baseline": {
            "nights": 6,
            "avg_hours": 7.0,
            "avg_efficiency_pct": 90.0,
            "duration_delta_hours": 0.17,
            "bedtime_consistency_min": 24,
        },
        "recovery": {
            "hrv_ms": 51.2,
            "hrv_baseline_ms": 49.0,
            "hrv_delta_pct": 4.5,
            "resting_hr_bpm": 58.0,
            "resting_hr_baseline_bpm": 59.0,
            "resting_hr_delta_bpm": -1.0,
            "spo2_pct": None,
            "spo2_lower_pct": None,
            "spo2_baseline_pct": None,
            "respiratory_rate": None,
            "respiratory_baseline": None,
            "strain_flag": False,
        },
        "observations": [
            "Sleep duration was 0.17 hours above the prior seven-night average.",
            "Deep plus REM sleep contributed 175 minutes.",
        ],
        "priority": "Preserve the same sleep window.",
        "coverage": {
            "sleep_baseline_nights": 6,
            "hrv_baseline_days": 6,
            "resting_hr_baseline_days": 6,
            "oxygen_baseline_days": 0,
            "respiratory_baseline_days": 0,
        },
    }


def day_digest() -> dict:
    sleep = sleep_digest()
    return {
        "date": daily_digest.date_context(DAY),
        "activity": {
            "day": DAY.isoformat(),
            "steps": 12_345,
            "calories_out": 2_650,
            "seven_day_avg_steps": 9_500,
            "step_goal_pct": 123,
        },
        "movement": {
            "hours": [{"hour": hour, "steps": hour * 10} for hour in range(24)],
            "blocks": [
                {"label": label, "start_hour": index * 3, "steps": (index + 1) * 300}
                for index, label in enumerate(("12a", "3a", "6a", "9a", "12p", "3p", "6p", "9p"))
            ],
            "peak_hour": {"hour": 20, "steps": 1_500},
        },
        "training": {
            "formal_records": 1,
            "trusted_records": 1,
            "workout_minutes": 45,
            "exercise_calories": 420,
            "active_zone_minutes": 35,
            "sessions": [{
                "name": "<script>alert(1)</script>",
                "duration_min": 45,
                "calories": 420,
                "quality_flags": [],
            }],
        },
        "sleep": sleep,
        "recovery": sleep["recovery"],
        "weight": {"avg7_lb": 175.4, "trend_lb": -0.8, "readings": 7},
        "facts": ["This was a <strong>top-three</strong> step day."],
        "observations": ["Steps finished 2,845 above the seven-day average."],
        "coverage": {
            "activity_days": 14,
            "hourly_step_samples": 15,
            "formal_workouts": 1,
            "sleep_available": True,
        },
    }


class DailyDigestTests(unittest.TestCase):
    def test_sleep_uses_longest_opportunity_and_computes_baseline(self) -> None:
        rows = [
            sleep_row(DAY, record_id="short", asleep=180, in_bed=200, efficiency=90),
            sleep_row(DAY, record_id="primary", asleep=430, in_bed=470, efficiency=91.5),
            sleep_row(DAY.replace(day=19), record_id="prior-1", asleep=420, in_bed=460, efficiency=91.3),
            sleep_row(DAY.replace(day=18), record_id="prior-2", asleep=390, in_bed=450, efficiency=86.7),
        ]
        with (
            patch("fitlit.daily_digest._sleep_rows", return_value=rows),
            patch("fitlit.daily_digest._daily_vital", return_value=(None, None, 0)),
            patch("fitlit.daily_digest._oxygen", return_value=(None, None, 0)),
        ):
            digest = daily_digest.build_sleep(DAY)
        self.assertEqual("primary", digest["sleep"]["record_id"])
        self.assertEqual(2, digest["baseline"]["nights"])
        self.assertAlmostEqual(6.75, digest["baseline"]["avg_hours"])
        self.assertEqual(175, digest["sleep"]["stages"]["deep"] + digest["sleep"]["stages"]["rem"])

    def test_sleep_rows_group_by_pacific_wake_date_not_recorded_offset(self) -> None:
        summary = {
            "summary": {
                "minutesAsleep": 420,
                "minutesInSleepPeriod": 460,
                "stagesSummary": [],
            },
        }
        rows = [{
            "name": "travel-record",
            "start_time": "2026-07-20T16:30:00+09:00",
            "end_time": "2026-07-21T00:30:00+09:00",
            "data_json": json.dumps(summary),
        }]
        with patch("fitlit.daily_digest._query", return_value=rows):
            normalized = daily_digest._sleep_rows(DAY, DAY)
        self.assertEqual(1, len(normalized))
        self.assertEqual(DAY.isoformat(), normalized[0]["day"])

    def test_incomplete_sleep_summary_stays_missing_and_renders(self) -> None:
        rows = [{
            "name": "partial",
            "start_time": "2026-07-20T06:00:00Z",
            "end_time": "2026-07-20T14:00:00Z",
            "data_json": "{}",
        }]
        with patch("fitlit.daily_digest._query", return_value=rows):
            normalized = daily_digest._sleep_rows(DAY, DAY)
        self.assertIsNone(normalized[0]["hours_asleep"])
        self.assertEqual(480, normalized[0]["selection_minutes"])
        with (
            patch("fitlit.daily_digest._sleep_rows", return_value=normalized),
            patch("fitlit.daily_digest._daily_vital", return_value=(None, None, 0)),
            patch("fitlit.daily_digest._oxygen", return_value=(None, None, 0)),
        ):
            digest = daily_digest.build_sleep(DAY)
        rendered = sleep_report(digest)
        self.assertIn("sleep summary pending", rendered.subject)
        self.assertIn("Stage data unavailable", rendered.html)

    def test_missing_optional_vitals_render_as_missing(self) -> None:
        rendered = sleep_report(sleep_digest())
        self.assertIn("SpO₂", rendered.html)
        self.assertIn("Respiration", rendered.html)
        self.assertNotIn("None", rendered.text)

    def test_date_context_includes_calendar_position(self) -> None:
        context = daily_digest.date_context(DAY)
        self.assertEqual("Monday, July 20, 2026", context["full"])
        self.assertEqual(201, context["day_of_year"])
        self.assertEqual(30, context["iso_week"])
        self.assertEqual(164, context["days_remaining"])

    def test_daily_facts_include_rank_peak_and_workout_share(self) -> None:
        activity = {"steps": 12_000, "calories_out": 2_400}
        recent = [
            {"day": f"2026-07-{index:02d}", "steps": steps}
            for index, steps in enumerate((8_000, 12_000, 10_000), start=18)
        ]
        hours = [{"hour": hour, "steps": 2_400 if hour == 20 else 0} for hour in range(24)]
        sessions = [{
            "quality_flags": [],
            "calories": 480,
            "duration_min": 45,
            "active_zone_minutes": 30,
        }]
        facts = daily_digest._daily_facts(DAY, activity, recent, hours, sessions, None)
        self.assertIn("#1 step day", facts[0])
        self.assertIn("20% of the day", facts[1])
        self.assertIn("20% of total energy output", facts[2])

    def test_templates_escape_record_names_and_facts(self) -> None:
        rendered = day_report(day_digest())
        self.assertNotIn("<script>", rendered.html)
        self.assertIn("&lt;script&gt;", rendered.html)
        self.assertNotIn("<strong>top-three</strong>", rendered.html)
        enriched = append_ai_insight(
            rendered,
            headline="Steady day",
            observations=("Movement stayed distributed.",),
            confidence=0.8,
            provider="copilot",
        )
        self.assertIn("Steady day", enriched.html)

    def test_missing_activity_is_not_reported_as_zero(self) -> None:
        digest = day_digest()
        digest["activity"]["steps"] = None
        digest["activity"]["calories_out"] = None
        digest["activity"]["step_goal_pct"] = None
        rendered = day_report(digest)
        self.assertIn("steps unavailable", rendered.subject)
        self.assertIn("energy unavailable", rendered.subject)
        self.assertNotIn("0 steps", rendered.text)

    def test_sleep_candidate_keeps_record_event_key_and_safe_payload(self) -> None:
        digest = sleep_digest()
        now = datetime(2026, 7, 20, 9, 0, tzinfo=PACIFIC)
        with patch("fitlit.gmail_service.daily_digest.build_sleep", return_value=digest):
            notification = _sleep_candidate(now)
        self.assertEqual("sleep:primary", notification.event_key)
        self.assertEqual("sleep", notification.ai_payload["report_type"])
        self.assertNotIn("record_id", notification.ai_payload)

    def test_evening_report_is_a_daily_candidate_not_a_minimum_fill(self) -> None:
        now = datetime(2026, 7, 20, 20, 0, tzinfo=PACIFIC)
        digest = day_digest()
        with patch("fitlit.gmail_service.daily_digest.build_day", return_value=digest):
            notification = _evening_fill(now)
        self.assertTrue(notification.mandatory)
        self.assertIsNone(notification.send_if_below)
        self.assertEqual("daily-fill:2026-07-20", notification.event_key)
        self.assertIn("FitLit Daily | Jul 20", notification.report.subject)


if __name__ == "__main__":
    unittest.main()
