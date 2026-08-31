"""The CLI that systemd, cron, and the owner all call."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fitlit.journal import PACIFIC
from personal import runner, store
from personal.tasks import coffee


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, value in (
            ("PERSONAL_DB", root / "personal.db"),
            ("PERSONAL_LOCK", root / "personal.lock"),
        ):
            patcher = patch(f"personal.config.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def invoke(self, *argv: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = runner.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def test_list_reports_the_registered_tasks(self) -> None:
        code, payload = self.invoke("list")
        self.assertEqual(0, code)
        self.assertEqual(["coffee"], [task["name"] for task in payload["tasks"]])

    def test_status_reports_configuration_and_the_blocked_list(self) -> None:
        code, payload = self.invoke("status", "coffee")
        self.assertEqual(0, code)
        self.assertTrue(payload["coffee"]["enabled"])
        self.assertEqual([], payload["coffee"]["blocked"])
        self.assertEqual(9, payload["coffee"]["send_hour_pacific"])

    def test_feedback_is_recorded_and_echoed_back(self) -> None:
        code, payload = self.invoke(
            "feedback", "Victrola Coffee", "disliked", "--note", "too loud"
        )
        self.assertEqual(0, code)
        self.assertEqual("disliked", payload["recorded"]["sentiment"])

        connection = store.connect()
        self.addCleanup(connection.close)
        self.assertEqual(
            [{"shop": "Victrola Coffee", "sentiment": "disliked", "note": "too loud"}],
            store.preferences(connection),
        )

    def test_blocking_a_shop_shows_up_immediately(self) -> None:
        _, payload = self.invoke("feedback", "Loud Place", "blocked")
        self.assertEqual(["Loud Place"], payload["blocked_now"])

    def test_an_unknown_sentiment_is_refused_by_the_parser(self) -> None:
        with self.assertRaises(SystemExit):
            runner.main(["feedback", "Somewhere", "meh"])

    def test_an_unknown_task_is_refused_by_the_parser(self) -> None:
        with self.assertRaises(SystemExit):
            runner.main(["run", "laundry"])

    def test_run_passes_its_flags_through_to_the_task(self) -> None:
        result = coffee.CoffeeResult(status="dry-run", day="2026-08-31")
        with patch("personal.tasks.coffee.run", return_value=result) as task:
            code, payload = self.invoke("run", "coffee", "--dry-run", "--force")
        self.assertEqual(0, code)
        self.assertEqual("dry-run", payload["status"])
        self.assertEqual("coffee", payload["task"])
        options = task.call_args.kwargs
        self.assertTrue(options["dry_run"])
        self.assertTrue(options["force"])
        self.assertTrue(options["send"])

    def test_no_send_is_forwarded_as_send_false(self) -> None:
        result = coffee.CoffeeResult(status="recorded", day="2026-08-31")
        with patch("personal.tasks.coffee.run", return_value=result) as task:
            self.invoke("run", "coffee", "--no-send")
        self.assertFalse(task.call_args.kwargs["send"])

    def test_a_naive_now_override_is_read_as_pacific(self) -> None:
        result = coffee.CoffeeResult(status="dry-run", day="2026-08-31")
        with patch("personal.tasks.coffee.run", return_value=result) as task:
            self.invoke("run", "coffee", "--now", "2026-08-31T09:30:00", "--dry-run")
        moment = task.call_args.kwargs["now"]
        self.assertEqual(PACIFIC, moment.tzinfo)
        self.assertEqual(date(2026, 8, 31), moment.date())

    def test_a_failed_task_exits_non_zero_so_systemd_notices(self) -> None:
        result = coffee.CoffeeResult(
            status="failed", day="2026-08-31", detail="claude timed out"
        )
        with patch("personal.tasks.coffee.run", return_value=result):
            code, payload = self.invoke("run", "coffee")
        self.assertEqual(1, code)
        self.assertEqual("claude timed out", payload["detail"])

    def test_an_already_sent_day_is_not_an_error(self) -> None:
        result = coffee.CoffeeResult(status="already-sent", day="2026-08-31")
        with patch("personal.tasks.coffee.run", return_value=result):
            code, _ = self.invoke("run", "coffee")
        self.assertEqual(0, code)

    def test_a_concurrent_run_backs_off_instead_of_double_sending(self) -> None:
        held = runner._lock()
        self.addCleanup(runner._unlock, held)
        with patch("personal.tasks.coffee.run") as task:
            code, payload = self.invoke("run", "coffee")
        task.assert_not_called()
        self.assertEqual(0, code)
        self.assertEqual("busy", payload["status"])

    def test_history_reads_back_what_was_recorded(self) -> None:
        connection = store.connect()
        store.record_recommendation(
            connection,
            date(2026, 8, 30),
            {
                "name": "Eastlake Coffee",
                "neighborhood": "Eastlake",
                "address": "2245 Eastlake Ave E",
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=x",
                "drive_minutes": 7,
                "noise_level": "quiet",
                "hours_today": "7:00 AM – 5:00 PM",
            },
        )
        connection.close()
        code, payload = self.invoke("history", "coffee", "--limit", "5")
        self.assertEqual(0, code)
        self.assertEqual("Eastlake Coffee", payload["coffee"][0]["name"])


if __name__ == "__main__":
    unittest.main()
