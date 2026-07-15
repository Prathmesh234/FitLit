from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fitlit import ai_insights, gmail_auth, gmail_client
from fitlit.gmail_service import (
    Notification,
    NotificationStore,
    _dated_subject,
    build_candidates,
    dispatch,
)
from fitlit.gmail_templates import report

PACIFIC = ZoneInfo("America/Los_Angeles")


def candidate(index: int, *, mandatory: bool = True, send_if_below: int | None = None) -> Notification:
    rendered = report(
        subject=f"Subject {index}",
        kicker="Test",
        title=f"Report {index}",
        subtitle="Numbers",
        metrics=[],
        details=[],
    )
    return Notification(
        event_key=f"event:{index}",
        kind="test",
        report=rendered,
        mandatory=mandatory,
        send_if_below=send_if_below,
    )


class GmailPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.temp.name) / "notifications.db")
        self.now = datetime(2026, 7, 14, 20, 0, tzinfo=PACIFIC)
        self.sent = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sender(self, subject: str, text: str, html: str) -> str:
        self.sent.append(subject)
        return f"message-{len(self.sent)}"

    def test_hard_daily_cap_is_five(self) -> None:
        with patch("fitlit.config.GMAIL_DAILY_MAX", 5):
            result = dispatch(
                [candidate(index) for index in range(6)],
                self.store,
                self.now,
                sender=self.sender,
            )
        self.assertEqual(5, len(result["sent"]))
        self.assertEqual(5, self.store.sent_today("2026-07-14"))

    def test_duplicate_event_is_sent_once(self) -> None:
        item = candidate(1)
        dispatch([item], self.store, self.now, sender=self.sender)
        dispatch([item], self.store, self.now, sender=self.sender)
        self.assertEqual(["Subject 1"], self.sent)

    def test_nonmandatory_notices_reserve_final_slot(self) -> None:
        items = [candidate(0)] + [
            candidate(index, mandatory=False) for index in range(1, 6)
        ]
        result = dispatch(items, self.store, self.now, sender=self.sender)
        self.assertEqual(4, len(result["sent"]))
        self.assertEqual(4, self.store.sent_today("2026-07-14"))

    def test_evening_fill_skips_after_daily_minimum(self) -> None:
        first = [candidate(1), candidate(2)]
        dispatch(first, self.store, self.now, sender=self.sender)
        fill = candidate(3, send_if_below=2)
        result = dispatch([fill], self.store, self.now, sender=self.sender)
        self.assertEqual([], result["sent"])
        self.assertEqual(2, self.store.sent_today("2026-07-14"))

    def test_sleep_candidate_suppresses_morning_fallback(self) -> None:
        sleep = candidate(9)
        sleep = Notification(
            event_key=sleep.event_key,
            kind="sleep",
            report=sleep.report,
            mandatory=True,
        )
        with (
            patch("fitlit.gmail_service._sleep_candidate", return_value=sleep),
            patch("fitlit.gmail_service._formal_workout_candidates", return_value=[]),
            patch("fitlit.gmail_service._inferred_workout_candidate", return_value=None),
            patch("fitlit.gmail_service._step_milestone_candidate", return_value=None),
            patch("fitlit.gmail_service._heart_signal_candidate", return_value=None),
            patch("fitlit.config.GMAIL_MORNING_FALLBACK_HOUR", 12),
            patch("fitlit.config.GMAIL_EVENING_FILL_HOUR", 23),
        ):
            items = build_candidates(self.now, self.store)
        self.assertEqual(["sleep"], [item.kind for item in items])

    def test_overlapping_workout_sources_are_deduplicated(self) -> None:
        first = candidate(20)
        first = Notification(
            event_key="exercise:immutable-id",
            kind="workout",
            report=first.report,
            mandatory=True,
            window_start=self.now - timedelta(hours=1),
            window_end=self.now,
        )
        second = candidate(21)
        second = Notification(
            event_key="inferred-workout:peak",
            kind="workout",
            report=second.report,
            mandatory=True,
            window_start=self.now - timedelta(minutes=50),
            window_end=self.now + timedelta(minutes=5),
        )
        dispatch([first, second], self.store, self.now, sender=self.sender)
        self.assertEqual(["Subject 20"], self.sent)

    def test_gmail_consent_url_contains_state(self) -> None:
        with patch("fitlit.config.OAUTH_CLIENT_ID", "client"):
            url = gmail_auth.build_consent_url("expected-state")
        self.assertIn("state=expected-state", url)

    def test_retryable_api_failure_releases_reservation(self) -> None:
        item = candidate(30)

        def fail(subject: str, text: str, html: str) -> str:
            raise gmail_client.GmailSendError("API still enabling", retryable=True)

        dispatch([item], self.store, self.now, sender=fail)
        result = dispatch([item], self.store, self.now, sender=self.sender)
        self.assertEqual(1, len(result["sent"]))

    def test_sleep_and_workout_subjects_share_dated_format(self) -> None:
        self.assertEqual(
            "FitLit Sleep | Jul 14 | 7.03h · 95%",
            _dated_subject("Sleep", self.now, "7.03h · 95%"),
        )
        self.assertEqual(
            "FitLit Workout | Jul 14 | 85 min · 118 avg BPM",
            _dated_subject("Workout", self.now, "85 min · 118 avg BPM"),
        )

    def test_ai_enrichment_runs_only_after_reservation(self) -> None:
        item = candidate(40)
        item = Notification(
            event_key=item.event_key,
            kind=item.kind,
            report=item.report,
            mandatory=True,
            ai_payload={"report_type": "sleep", "hours_asleep": 7.2},
        )
        insight = ai_insights.AIInsight(
            headline="Stable sleep window",
            observations=("7.2 hours were recorded.",),
            confidence=0.8,
            provider="copilot",
        )
        messages = []

        def sender(subject: str, text: str, html: str) -> str:
            messages.append((text, html))
            return "message-ai"

        with patch("fitlit.gmail_service.ai_insights.generate", return_value=insight) as generate:
            dispatch([item, item], self.store, self.now, sender=sender)
        generate.assert_called_once_with(item.ai_payload)
        self.assertIn("Stable sleep window", messages[0][0])
        self.assertIn("confidence 80%", messages[0][1])

    def test_ai_failure_keeps_deterministic_report(self) -> None:
        item = candidate(41)
        item = Notification(
            event_key=item.event_key,
            kind=item.kind,
            report=item.report,
            mandatory=True,
            ai_payload={"report_type": "workout", "duration_min": 45},
        )
        sent_bodies = []

        def sender(subject: str, text: str, html: str) -> str:
            sent_bodies.append(text)
            return "message-fallback"

        with patch("fitlit.gmail_service.ai_insights.generate", return_value=None):
            result = dispatch([item], self.store, self.now, sender=sender)
        self.assertEqual(1, len(result["sent"]))
        self.assertNotIn("AI observations", sent_bodies[0])


class AIInsightTests(unittest.TestCase):
    def test_parser_rejects_extra_fields_and_long_observations(self) -> None:
        with self.assertRaises(ai_insights.AIInsightError):
            ai_insights.parse_response(
                '{"headline":"ok","observations":["fine"],"confidence":0.8,"extra":1}',
                "copilot",
            )
        with self.assertRaises(ai_insights.AIInsightError):
            ai_insights.parse_response(
                '{"headline":"ok","observations":["' + ("x" * 141) + '"],"confidence":0.8}',
                "codex",
            )

    def test_payload_rejects_nested_or_identifying_values(self) -> None:
        with self.assertRaises(ai_insights.AIInsightError):
            ai_insights.sanitize_payload({"metrics": {"hours": 7}})
        with self.assertRaises(ai_insights.AIInsightError):
            ai_insights.sanitize_payload({"email": "person@example.com"})

    def test_environment_strips_application_secrets(self) -> None:
        clean = ai_insights.minimal_environment({
            "PATH": "/bin",
            "HOME": "/tmp/home",
            "GITHUB_TOKEN": "provider-token",
            "GMAIL_REFRESH_TOKEN": "private",
            "FITLIT_GMAIL_TO": "person@example.com",
            "GOOGLE_HEALTH_CLIENT_SECRET": "private",
        })
        self.assertEqual(
            {"PATH": "/bin", "HOME": "/tmp/home", "GITHUB_TOKEN": "provider-token"},
            clean,
        )

    def test_timeout_becomes_provider_error(self) -> None:
        with patch(
            "fitlit.ai_insights.subprocess.run",
            side_effect=__import__("subprocess").TimeoutExpired("copilot", 1),
        ):
            with self.assertRaises(ai_insights.AIInsightError):
                ai_insights._run(["copilot"], Path(tempfile.gettempdir()))

    def test_codex_adapter_uses_ephemeral_read_only_schema_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("fitlit.ai_insights._run", return_value="{}") as run:
                ai_insights._codex("prompt", Path(directory))
        command = run.call_args.args[0]
        self.assertIn("--ephemeral", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--output-schema", command)

    def test_claude_adapter_disables_tools_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("fitlit.ai_insights._run", return_value="{}") as run:
                ai_insights._claude("prompt", Path(directory))
        command = run.call_args.args[0]
        self.assertIn("--bare", command)
        self.assertEqual("", command[command.index("--tools") + 1])
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--json-schema", command)


if __name__ == "__main__":
    unittest.main()
