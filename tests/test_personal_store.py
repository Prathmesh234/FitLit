"""The personal ledger: shop identity, daily reservations, and feedback."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fitlit.journal import PACIFIC
from personal import store


class TemporaryLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "personal.db"
        patcher = patch("personal.config.PERSONAL_DB", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connection = store.connect()
        self.addCleanup(self.connection.close)


class ShopKeyTests(unittest.TestCase):
    def test_business_words_and_punctuation_do_not_change_identity(self) -> None:
        self.assertEqual(
            store.shop_key("Victrola Coffee Roasters"),
            store.shop_key("victrola coffee"),
        )
        self.assertEqual(
            store.shop_key("Milstead & Co."),
            store.shop_key("milstead and co"),
        )

    def test_accents_are_folded(self) -> None:
        self.assertEqual(store.shop_key("Café Ladro"), store.shop_key("Cafe Ladro"))

    def test_distinct_locations_stay_distinct(self) -> None:
        self.assertNotEqual(
            store.shop_key("Espresso Vivace Sidewalk Bar"),
            store.shop_key("Espresso Vivace Capitol Hill"),
        )

    def test_a_name_with_no_usable_characters_is_rejected(self) -> None:
        with self.assertRaises(store.PersonalStoreError):
            store.shop_key("   ")


class ReservationTests(TemporaryLedger):
    def test_a_day_can_only_be_claimed_once(self) -> None:
        day = date(2026, 8, 31)
        self.assertTrue(store.reserve_run(self.connection, "coffee", day))
        self.assertFalse(store.reserve_run(self.connection, "coffee", day))

    def test_a_delivered_day_stays_closed(self) -> None:
        day = date(2026, 8, 31)
        store.reserve_run(self.connection, "coffee", day)
        store.finish_run(
            self.connection, "coffee", day, "sent", message_id="abc123"
        )
        self.assertFalse(store.reserve_run(self.connection, "coffee", day))

    def test_a_failed_day_may_be_retried(self) -> None:
        day = date(2026, 8, 31)
        store.reserve_run(self.connection, "coffee", day)
        store.finish_run(self.connection, "coffee", day, "failed", detail="boom")
        self.assertTrue(store.reserve_run(self.connection, "coffee", day))

    def test_force_reopens_a_delivered_day(self) -> None:
        day = date(2026, 8, 31)
        store.reserve_run(self.connection, "coffee", day)
        store.finish_run(self.connection, "coffee", day, "sent", message_id="x")
        self.assertTrue(
            store.reserve_run(self.connection, "coffee", day, force=True)
        )
        row = store.run_history(self.connection, "coffee")[0]
        self.assertEqual("reserved", row["status"])
        self.assertIsNone(row["message_id"])

    def test_tasks_do_not_share_a_slot(self) -> None:
        day = date(2026, 8, 31)
        self.assertTrue(store.reserve_run(self.connection, "coffee", day))
        self.assertTrue(store.reserve_run(self.connection, "errands", day))

    def test_an_unknown_status_is_rejected(self) -> None:
        day = date(2026, 8, 31)
        store.reserve_run(self.connection, "coffee", day)
        with self.assertRaises(store.PersonalStoreError):
            store.finish_run(self.connection, "coffee", day, "delivered")


def payload(name: str, **overrides: object) -> dict:
    base = {
        "name": name,
        "neighborhood": "Eastlake",
        "address": "2245 Eastlake Ave E, Seattle, WA 98102",
        "google_maps_url": "https://www.google.com/maps/search/?api=1&query=x",
        "drive_minutes": 7,
        "noise_level": "quiet",
        "hours_today": "7:00 AM – 5:00 PM",
    }
    base.update(overrides)
    return base


class RecommendationHistoryTests(TemporaryLedger):
    def test_recent_recommendations_respect_the_repeat_window(self) -> None:
        now = datetime(2026, 8, 31, 9, tzinfo=PACIFIC)
        store.record_recommendation(
            self.connection, date(2026, 8, 30), payload("Fresh Cafe")
        )
        store.record_recommendation(
            self.connection, date(2026, 1, 1), payload("Ancient Cafe")
        )
        names = {
            row["name"]
            for row in store.recent_recommendations(
                self.connection, window_days=30, now=now
            )
        }
        self.assertIn("Fresh Cafe", names)
        self.assertNotIn("Ancient Cafe", names)

    def test_recording_the_same_day_twice_replaces_it(self) -> None:
        day = date(2026, 8, 31)
        store.record_recommendation(self.connection, day, payload("First"))
        store.record_recommendation(self.connection, day, payload("Second"))
        rows = store.recent_recommendations(self.connection, now=datetime(
            2026, 8, 31, 9, tzinfo=PACIFIC
        ))
        self.assertEqual(1, len(rows))
        self.assertEqual("Second", rows[0]["name"])

    def test_last_seen_reports_the_most_recent_day(self) -> None:
        store.record_recommendation(
            self.connection, date(2026, 8, 20), payload("Victrola Coffee")
        )
        store.record_recommendation(
            self.connection, date(2026, 8, 28), payload("Victrola Coffee Roasters")
        )
        self.assertEqual(
            "2026-08-28",
            store.last_seen(self.connection, store.shop_key("victrola")),
        )


class FeedbackTests(TemporaryLedger):
    def test_the_newest_verdict_for_a_shop_wins(self) -> None:
        store.record_feedback(self.connection, "Victrola Coffee", "loved")
        store.record_feedback(
            self.connection, "Victrola Coffee Roasters", "disliked", "too loud"
        )
        preferences = store.preferences(self.connection)
        self.assertEqual(1, len(preferences))
        self.assertEqual("disliked", preferences[0]["sentiment"])
        self.assertEqual("too loud", preferences[0]["note"])

    def test_blocked_shops_are_excluded_from_taste_guidance(self) -> None:
        store.record_feedback(self.connection, "Loud Place", "blocked")
        store.record_feedback(self.connection, "Milstead", "liked")
        self.assertEqual(["Loud Place"], store.blocked_shops(self.connection))
        self.assertEqual(
            {store.shop_key("Loud Place")}, store.blocked_keys(self.connection)
        )
        self.assertEqual(
            ["Milstead"], [row["shop"] for row in store.preferences(self.connection)]
        )

    def test_unblocking_is_just_a_newer_verdict(self) -> None:
        store.record_feedback(self.connection, "Second Chance", "blocked")
        store.record_feedback(self.connection, "Second Chance", "neutral")
        self.assertEqual([], store.blocked_shops(self.connection))

    def test_an_unknown_sentiment_is_rejected(self) -> None:
        with self.assertRaises(store.PersonalStoreError):
            store.record_feedback(self.connection, "Somewhere", "meh")

    def test_an_empty_shop_name_is_rejected(self) -> None:
        with self.assertRaises(store.PersonalStoreError):
            store.record_feedback(self.connection, "  ", "liked")


class LedgerFileTests(TemporaryLedger):
    def test_the_ledger_is_created_private(self) -> None:
        self.assertTrue(self.path.exists())
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    def test_the_schema_is_created_once_and_reused(self) -> None:
        second = store.connect()
        self.addCleanup(second.close)
        tables = {
            row[0]
            for row in second.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertLessEqual(
            {"personal_task_runs", "coffee_recommendations", "coffee_feedback"},
            tables,
        )


if __name__ == "__main__":
    unittest.main()
