"""What the conversational agent is told about the personal section."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fitlit.journal import PACIFIC
from personal import context, store

DAY = date(2026, 8, 31)
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=PACIFIC)


def good(**overrides: object) -> dict:
    payload = {
        "name": "Eastlake Coffee + Cafe",
        "neighborhood": "Eastlake",
        "address": "2245 Eastlake Ave E, Seattle, WA 98102",
        "google_maps_url": (
            "https://www.google.com/maps/search/?api=1&query=Eastlake+Coffee"
        ),
        "website": "https://eastlake-coffee.com/",
        "hours_today": "7:00 AM \u2013 5:00 PM",
        "hours_source": "shop's own website, Hours page",
        "drive_minutes": 7,
        "noise_level": "quiet",
        "best_time": "Mid-morning, 9 to 11.",
        "one_liner": "A quiet Eastlake window seat, seven minutes away.",
        "verified_date": DAY.isoformat(),
    }
    payload.update(overrides)
    return payload


class TemporaryLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch(
            "personal.config.PERSONAL_DB", Path(self.directory.name) / "personal.db"
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class AssistantContextTests(TemporaryLedger):
    def test_todays_pick_is_offered_to_the_conversational_agent(self) -> None:
        connection = store.connect()
        store.record_recommendation(connection, DAY, good())
        store.record_feedback(connection, "Loud Place", "blocked")
        store.record_feedback(connection, "Milstead", "loved", "great light")
        connection.close()

        block = context.assistant_context(NOW, DAY)
        self.assertEqual("Eastlake Coffee + Cafe", block["coffee_today"]["name"])
        self.assertEqual(DAY.isoformat(), block["coffee_today"]["sent_on"])
        self.assertEqual(["Loud Place"], block["coffee_blocked"])
        self.assertEqual("Milstead", block["coffee_feedback"][0]["shop"])
        self.assertEqual(9, block["coffee_task"]["send_hour_pacific"])

    def test_the_task_is_described_even_before_the_first_pick(self) -> None:
        store.connect().close()
        block = context.assistant_context(NOW, DAY)
        self.assertIn("coffee_task", block)
        self.assertNotIn("coffee_today", block)

    def test_a_missing_ledger_yields_nothing_rather_than_failing(self) -> None:
        self.assertEqual({}, context.assistant_context(NOW, DAY))

    def test_todays_pick_is_not_repeated_in_the_recent_list(self) -> None:
        connection = store.connect()
        store.record_recommendation(connection, DAY, good())
        store.record_recommendation(
            connection, date(2026, 8, 30), good(name="Victrola Coffee")
        )
        connection.close()
        block = context.assistant_context(NOW, DAY)
        self.assertEqual(
            ["Victrola Coffee"], [row["name"] for row in block["coffee_recent"]]
        )


if __name__ == "__main__":
    unittest.main()
