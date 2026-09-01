"""The daily coffee recommendation: validation, duplicate handling, delivery."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fitlit.journal import PACIFIC
from personal import agent, config, emails, store
from personal.tasks import coffee

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
        "open_today": True,
        "hours_today": "7:00 AM – 5:00 PM",
        "hours_source": "shop's own website, Hours page",
        "hours_note": "",
        "drive_minutes": 7,
        "drive_note": "Straight up Eastlake Ave E from South Lake Union.",
        "noise_level": "quiet",
        "noise_evidence": "Reviewers call it calm, with music kept low.",
        "vibe": "A warm neighborhood cafe with window seats and a low hum.",
        "seating": "Cushioned chairs by the windows plus communal tables.",
        "wifi_outlets": "Free wifi, outlets below the seating.",
        "signature_order": "Drip coffee from Lighthouse Roasters.",
        "food_note": "Macrina pastries.",
        "best_time": "Mid-morning, 9 to 11.",
        "why_today": (
            "A seven-minute drive from South Lake Union, open all day, and one "
            "of the few Eastlake rooms quiet enough to actually read in."
        ),
        "one_liner": "A quiet Eastlake window seat, seven minutes away.",
        "verified_date": DAY.isoformat(),
        "search_queries": [
            "quiet coffee shop near South Lake Union Seattle",
            "Eastlake Coffee Cafe Seattle hours",
        ],
        "sources": [
            "https://eastlake-coffee.com/",
            "https://www.yelp.com/biz/eastlake-coffee-cafe-seattle",
        ],
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


class TemporaryLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "personal.db"
        patcher = patch("personal.config.PERSONAL_DB", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        lock = patch(
            "personal.config.PERSONAL_LOCK",
            Path(self.directory.name) / "personal.lock",
        )
        lock.start()
        self.addCleanup(lock.stop)


class ValidationTests(unittest.TestCase):
    def validate(self, payload: dict, *, searches: int = 3, blocked=frozenset()):
        return coffee.validate(
            payload, DAY, blocked=set(blocked), web_searches=searches
        )

    def test_a_well_formed_candidate_passes_and_gains_its_key(self) -> None:
        shop = self.validate(good())
        self.assertEqual(store.shop_key("Eastlake Coffee + Cafe"), shop["shop_key"])
        self.assertEqual(7, shop["drive_minutes"])

    def test_a_run_without_a_web_search_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "no web search"):
            self.validate(good(), searches=0)

    def test_a_shop_closed_today_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "not open today"):
            self.validate(good(open_today=False))

    def test_hours_verified_on_another_day_are_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "verified_date"):
            self.validate(good(verified_date="2026-08-24"))

    def test_a_malformed_verified_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "verified_date"):
            self.validate(good(verified_date="yesterday"))

    def test_a_drive_past_the_ceiling_is_rejected(self) -> None:
        beyond = config.COFFEE_MAX_DRIVE_MINUTES + 1
        with self.assertRaisesRegex(coffee.CoffeeRejected, "ceiling"):
            self.validate(good(drive_minutes=beyond))

    def test_a_drive_at_the_ceiling_is_accepted(self) -> None:
        shop = self.validate(good(drive_minutes=config.COFFEE_MAX_DRIVE_MINUTES))
        self.assertEqual(config.COFFEE_MAX_DRIVE_MINUTES, shop["drive_minutes"])

    def test_a_loud_room_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "noise_level"):
            self.validate(good(noise_level="lively"))

    def test_a_link_that_is_not_google_maps_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "Google Maps"):
            self.validate(good(google_maps_url="https://example.com/cafe"))

    def test_a_short_maps_link_is_accepted(self) -> None:
        self.validate(good(google_maps_url="https://maps.app.goo.gl/abc123"))

    def test_a_single_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "two sources"):
            self.validate(good(sources=["https://eastlake-coffee.com/"]))

    def test_sources_from_one_site_are_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "same site"):
            self.validate(good(sources=[
                "https://eastlake-coffee.com/",
                "https://eastlake-coffee.com/hours",
            ]))

    def test_a_source_that_is_not_a_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "usable URL"):
            self.validate(good(sources=[
                "eastlake-coffee.com",
                "https://www.yelp.com/biz/x",
            ]))

    def test_a_blocked_shop_is_rejected(self) -> None:
        blocked = {store.shop_key("Eastlake Coffee + Cafe")}
        with self.assertRaisesRegex(coffee.CoffeeRejected, "blocked"):
            self.validate(good(), blocked=blocked)

    def test_an_empty_website_is_allowed(self) -> None:
        self.assertEqual("", self.validate(good(website=""))["website"])

    def test_a_broken_website_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "website"):
            self.validate(good(website="not-a-url"))

    def test_a_single_reported_query_is_rejected(self) -> None:
        with self.assertRaisesRegex(coffee.CoffeeRejected, "search queries"):
            self.validate(good(search_queries=["one query"]))


class PromptTests(unittest.TestCase):
    def build(self, **overrides):
        arguments = {
            "recent": [],
            "blocked": [],
            "preferences": [],
            "rejections": [],
        }
        arguments.update(overrides)
        return coffee.build_prompt(DAY, **arguments)

    def test_the_prompt_names_today_and_its_weekday(self) -> None:
        prompt = self.build()
        self.assertIn("2026-08-31", prompt)
        self.assertIn("Monday", prompt)

    def test_the_origin_and_ceiling_are_stated(self) -> None:
        prompt = self.build()
        self.assertIn(config.COFFEE_ORIGIN, prompt)
        self.assertIn(str(config.COFFEE_MAX_DRIVE_MINUTES), prompt)

    def test_history_blocked_and_feedback_reach_the_model(self) -> None:
        prompt = self.build(
            recent=[{
                "name": "Victrola",
                "neighborhood": "Capitol Hill",
                "day": "2026-08-30",
            }],
            blocked=["Loud Place"],
            preferences=[{
                "shop": "Milstead",
                "sentiment": "loved",
                "note": "perfect light",
            }],
        )
        self.assertIn("Victrola (Capitol Hill) — sent 2026-08-30", prompt)
        self.assertIn("Loud Place", prompt)
        self.assertIn("Milstead: loved", prompt)
        self.assertIn("perfect light", prompt)

    def test_an_empty_history_says_so_rather_than_leaving_a_gap(self) -> None:
        self.assertIn("No coffee shop has been recommended yet", self.build())

    def test_earlier_rejections_are_fed_back_in(self) -> None:
        prompt = self.build(rejections=["Somewhere is a 30-minute drive"])
        self.assertIn("Somewhere is a 30-minute drive", prompt)

    def test_google_style_queries_are_demonstrated(self) -> None:
        prompt = self.build()
        self.assertIn("quiet coffee shops near South Lake Union Seattle", prompt)
        self.assertIn("Seattle hours", prompt)


class FakeRun:
    """Stands in for one headless harness call."""

    def __init__(self, data: dict, searches: int = 3) -> None:
        self.data = data
        self.web_searches = searches
        self.web_fetches = 1
        self.model = "claude-sonnet-5"
        self.duration_ms = 1000
        self.cost_usd = 0.0


class RecommendationTests(TemporaryLedger):
    def run_with(self, replies, **kwargs):
        calls = []

        def fake(prompt, schema, **options):
            calls.append(prompt)
            reply = replies[min(len(calls) - 1, len(replies) - 1)]
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("personal.agent.run", side_effect=fake):
            result = coffee.run(now=NOW, **kwargs)
        return result, calls

    def test_a_fresh_shop_is_delivered_and_recorded(self) -> None:
        sent = {}

        def fake_send(subject, text, html, **options):
            sent.update(subject=subject, text=text, html=html, options=options)
            return "gmail-message-id"

        with patch("fitlit.gmail_client.send", side_effect=fake_send):
            result, calls = self.run_with([FakeRun(good())])

        self.assertEqual("sent", result.status)
        self.assertEqual("gmail-message-id", result.message_id)
        self.assertEqual(1, len(calls))
        self.assertIn("Eastlake Coffee + Cafe", sent["subject"])
        self.assertEqual("personal-coffee", sent["options"]["category"])

        connection = store.connect()
        self.addCleanup(connection.close)
        rows = store.recent_recommendations(connection, now=NOW)
        self.assertEqual(["Eastlake Coffee + Cafe"], [row["name"] for row in rows])
        self.assertEqual("sent", store.run_history(connection, "coffee")[0]["status"])

    def test_a_dry_run_writes_nothing_and_sends_nothing(self) -> None:
        with patch("fitlit.gmail_client.send") as sender:
            result, _ = self.run_with([FakeRun(good())], dry_run=True)
        sender.assert_not_called()
        self.assertEqual("dry-run", result.status)

        connection = store.connect()
        self.addCleanup(connection.close)
        self.assertEqual([], store.recent_recommendations(connection, now=NOW))
        self.assertEqual([], store.run_history(connection, "coffee"))

    def test_the_day_is_not_delivered_twice(self) -> None:
        with patch("fitlit.gmail_client.send", return_value="id-1"):
            self.run_with([FakeRun(good())])
        with patch("fitlit.gmail_client.send") as sender:
            result, calls = self.run_with([FakeRun(good())])
        sender.assert_not_called()
        self.assertEqual("already-sent", result.status)
        self.assertEqual([], calls)

    def test_a_repeat_is_retried_before_it_is_accepted(self) -> None:
        connection = store.connect()
        store.record_recommendation(
            connection, date(2026, 8, 25), good(name="Eastlake Coffee + Cafe")
        )
        connection.close()

        with patch("fitlit.gmail_client.send", return_value="id-2"):
            result, calls = self.run_with([FakeRun(good())])

        self.assertEqual("sent", result.status)
        self.assertEqual(config.COFFEE_ATTEMPTS, len(calls))
        self.assertEqual("2026-08-25", result.repeat_of_day)
        # Every retry after the first is told which shop to avoid.
        self.assertIn("already recommended on 2026-08-25", calls[1])

    def test_a_second_attempt_can_recover_from_a_rejection(self) -> None:
        with patch("fitlit.gmail_client.send", return_value="id-3"):
            result, calls = self.run_with([
                FakeRun(good(name="Too Far Cafe", drive_minutes=45)),
                FakeRun(good()),
            ])
        self.assertEqual("sent", result.status)
        self.assertEqual(2, result.attempts)
        self.assertIn("45-minute drive", calls[1])

    def test_every_attempt_failing_records_a_failure_and_mails_no_pick(self) -> None:
        with patch("fitlit.gmail_client.send", return_value="notice-id") as sender:
            result, calls = self.run_with([FakeRun(good(open_today=False))])
        # Only the "no pick" notice goes out — never an unverified shop.
        self.assertEqual(1, sender.call_count)
        self.assertIn("no pick", sender.call_args[0][0])
        self.assertEqual("failed", result.status)
        self.assertEqual(config.COFFEE_ATTEMPTS, len(calls))

        connection = store.connect()
        self.addCleanup(connection.close)
        row = store.run_history(connection, "coffee")[0]
        self.assertEqual("failed", row["status"])
        # A failed day may be retried by a later timer.
        self.assertTrue(store.reserve_run(connection, "coffee", DAY))

    def test_a_failed_morning_still_sends_a_notice(self) -> None:
        sent = {}

        def fake_send(subject, text, html, **options):
            sent.update(subject=subject, text=text)
            return "notice-id"

        with patch("fitlit.gmail_client.send", side_effect=fake_send):
            result, _ = self.run_with([FakeRun(good(open_today=False))])

        self.assertEqual("failed", result.status)
        self.assertTrue(result.notified)
        self.assertIn("no pick", sent["subject"])
        self.assertIn("not open today", sent["text"])

    def test_the_failure_notice_can_be_turned_off(self) -> None:
        with patch("personal.config.COFFEE_NOTIFY_ON_FAILURE", False):
            with patch("fitlit.gmail_client.send") as sender:
                result, _ = self.run_with([FakeRun(good(open_today=False))])
        sender.assert_not_called()
        self.assertFalse(result.notified)

    def test_a_dry_run_never_sends_a_failure_notice(self) -> None:
        with patch("fitlit.gmail_client.send") as sender:
            result, _ = self.run_with(
                [FakeRun(good(open_today=False))], dry_run=True
            )
        sender.assert_not_called()
        self.assertEqual("failed", result.status)

    def test_a_notice_that_cannot_be_sent_does_not_raise(self) -> None:
        with patch("fitlit.gmail_client.send", side_effect=RuntimeError("gmail down")):
            result, _ = self.run_with([FakeRun(good(open_today=False))])
        self.assertEqual("failed", result.status)
        self.assertFalse(result.notified)

    def test_a_harness_error_is_reported_not_raised(self) -> None:
        with patch("fitlit.gmail_client.send", return_value="notice-id"):
            result, _ = self.run_with(
                [agent.PersonalAgentError("claude timed out")]
            )
        self.assertEqual("failed", result.status)
        self.assertIn("timed out", result.detail)

    def test_a_send_failure_is_recorded_without_losing_the_pick(self) -> None:
        with patch("fitlit.gmail_client.send", side_effect=RuntimeError("gmail down")):
            result, _ = self.run_with([FakeRun(good())])
        self.assertEqual("send-failed", result.status)
        self.assertIn("gmail down", result.detail)

        connection = store.connect()
        self.addCleanup(connection.close)
        self.assertEqual(
            ["Eastlake Coffee + Cafe"],
            [row["name"] for row in store.recent_recommendations(connection, now=NOW)],
        )

    def test_no_send_records_without_mailing(self) -> None:
        with patch("fitlit.gmail_client.send") as sender:
            result, _ = self.run_with([FakeRun(good())], send=False)
        sender.assert_not_called()
        self.assertEqual("recorded", result.status)

    def test_a_disabled_task_does_nothing(self) -> None:
        with patch("personal.config.COFFEE_ENABLED", False):
            with patch("personal.agent.run") as runner:
                result = coffee.run(now=NOW)
        runner.assert_not_called()
        self.assertEqual("disabled", result.status)

    def test_blocked_shops_never_reach_the_model_as_candidates(self) -> None:
        connection = store.connect()
        store.record_feedback(connection, "Eastlake Coffee + Cafe", "blocked")
        connection.close()
        with patch("fitlit.gmail_client.send", return_value="notice-id") as sender:
            result, calls = self.run_with([FakeRun(good())])
        self.assertEqual("failed", result.status)
        self.assertIn("no pick", sender.call_args[0][0])
        self.assertIn("Eastlake Coffee + Cafe", calls[0])


class EmailTests(unittest.TestCase):
    def test_the_subject_names_the_shop_its_area_and_the_day(self) -> None:
        report = emails.coffee_report(good(), DAY)
        self.assertIn("Eastlake Coffee + Cafe", report.subject)
        self.assertIn("Eastlake", report.subject)
        self.assertIn("Aug 31", report.subject)

    def test_both_bodies_carry_the_verified_hours(self) -> None:
        report = emails.coffee_report(good(), DAY)
        self.assertIn("7:00 AM", report.text)
        self.assertIn("7:00 AM", report.html)
        self.assertIn(DAY.isoformat(), report.text)

    def test_the_maps_link_is_present_and_the_html_is_a_full_document(self) -> None:
        report = emails.coffee_report(good(), DAY)
        self.assertTrue(report.html.startswith("<!doctype html>"))
        self.assertIn("google.com/maps", report.html)
        self.assertIn("Open in Google Maps", report.html)

    def test_a_repeat_is_disclosed_rather_than_hidden(self) -> None:
        report = emails.coffee_report(good(), DAY, repeat_of_day="2026-06-01")
        self.assertIn("2026-06-01", report.html)
        self.assertIn("2026-06-01", report.text)

    def test_shop_text_is_escaped(self) -> None:
        report = emails.coffee_report(
            good(name="Bean & <script>alert(1)</script>"), DAY
        )
        self.assertNotIn("<script>", report.html)
        self.assertIn("&lt;script&gt;", report.html)

    def test_empty_optional_fields_are_dropped_from_the_detail_table(self) -> None:
        report = emails.coffee_report(good(food_note="", hours_note=""), DAY)
        self.assertNotIn("Food", report.html)
        self.assertNotIn("Hours note", report.html)

    def test_a_missing_website_omits_its_button(self) -> None:
        report = emails.coffee_report(good(website=""), DAY)
        self.assertNotIn("Shop website", report.html)


if __name__ == "__main__":
    unittest.main()
