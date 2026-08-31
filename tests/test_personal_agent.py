"""The web-enabled harness runner behind every personal task."""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from personal import agent

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


def envelope(**overrides: object) -> dict:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 12_000,
        "total_cost_usd": 0.31,
        "model": "claude-sonnet-5",
        "result": '{"name":"Somewhere"}',
        "structured_output": {"name": "Somewhere"},
        "usage": {"server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 1}},
        "modelUsage": {
            "claude-haiku-4-5": {"webSearchRequests": 1, "webFetchRequests": 0},
        },
    }
    base.update(overrides)
    return base


class Completed:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class ToolCountTests(unittest.TestCase):
    def test_counts_come_from_both_places_the_harness_reports_them(self) -> None:
        self.assertEqual((3, 1), agent._tool_counts(envelope()))

    def test_a_run_with_no_tool_use_counts_zero(self) -> None:
        self.assertEqual(
            (0, 0), agent._tool_counts(envelope(usage={}, modelUsage={}))
        )

    def test_an_unfamiliar_envelope_shape_does_not_raise(self) -> None:
        self.assertEqual(
            (0, 0), agent._tool_counts({"usage": "?", "modelUsage": []})
        )


class StructuredOutputTests(unittest.TestCase):
    def test_structured_output_is_preferred(self) -> None:
        value = agent._structured(
            envelope(structured_output={"name": "A"}, result='{"name":"B"}')
        )
        self.assertEqual({"name": "A"}, value)

    def test_a_json_string_result_is_parsed_when_there_is_no_structured_output(
        self,
    ) -> None:
        payload = envelope(result='{"name":"B"}')
        payload.pop("structured_output")
        self.assertEqual({"name": "B"}, agent._structured(payload))

    def test_prose_instead_of_json_is_rejected(self) -> None:
        payload = envelope(result="I could not find one.")
        payload.pop("structured_output")
        with self.assertRaises(agent.PersonalAgentError):
            agent._structured(payload)


class RunTests(unittest.TestCase):
    def call(self, completed, **kwargs):
        with patch("shutil.which", return_value="/usr/bin/claude"), patch(
            "personal.agent.subprocess.run", return_value=completed
        ) as runner:
            result = agent.run("find one", SCHEMA, **kwargs)
        return result, runner.call_args[0][0]

    def test_a_successful_run_reports_its_searches(self) -> None:
        result, _ = self.call(Completed(json.dumps(envelope())))
        self.assertEqual({"name": "Somewhere"}, result.data)
        self.assertEqual(3, result.web_searches)
        self.assertEqual(1, result.web_fetches)
        self.assertEqual(0.31, result.cost_usd)

    def test_the_web_tools_are_granted_and_nothing_else_is(self) -> None:
        _, command = self.call(Completed(json.dumps(envelope())))
        self.assertEqual("WebSearch,WebFetch", command[command.index("--tools") + 1])
        self.assertEqual(
            "WebSearch,WebFetch", command[command.index("--allowedTools") + 1]
        )

    def test_no_user_or_project_configuration_is_loaded(self) -> None:
        _, command = self.call(Completed(json.dumps(envelope())))
        self.assertEqual("", command[command.index("--setting-sources") + 1])
        self.assertIn("--strict-mcp-config", command)
        self.assertIn("--disable-slash-commands", command)
        self.assertIn("--no-session-persistence", command)
        # --bare refuses OAuth and would break the daemon's session.
        self.assertNotIn("--bare", command)

    def test_the_requested_schema_is_passed_through(self) -> None:
        _, command = self.call(Completed(json.dumps(envelope())))
        self.assertEqual(
            SCHEMA, json.loads(command[command.index("--json-schema") + 1])
        )

    def test_a_system_prompt_is_appended_when_given(self) -> None:
        _, command = self.call(
            Completed(json.dumps(envelope())), system_prompt="be careful"
        )
        self.assertEqual(
            "be careful", command[command.index("--append-system-prompt") + 1]
        )

    def test_an_error_envelope_is_surfaced(self) -> None:
        with self.assertRaisesRegex(agent.PersonalAgentError, "reported an error"):
            self.call(
                Completed(json.dumps(envelope(is_error=True, result="rate limited")))
            )

    def test_a_non_zero_exit_is_surfaced_with_its_last_line(self) -> None:
        with self.assertRaisesRegex(agent.PersonalAgentError, "not authenticated"):
            self.call(Completed("", returncode=1, stderr="not authenticated"))

    def test_non_json_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(agent.PersonalAgentError, "non-JSON"):
            self.call(Completed("thinking about it..."))

    def test_empty_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(agent.PersonalAgentError, "no output"):
            self.call(Completed("   "))

    def test_oversized_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(agent.PersonalAgentError, "size limit"):
            self.call(Completed(json.dumps(envelope())), max_output_chars=10)

    def test_a_timeout_is_reported_as_one(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/claude"), patch(
            "personal.agent.subprocess.run",
            side_effect=subprocess.TimeoutExpired("claude", 60),
        ):
            with self.assertRaisesRegex(agent.PersonalAgentError, "timed out"):
                agent.run("find one", SCHEMA)

    def test_a_missing_cli_is_reported_before_anything_runs(self) -> None:
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(agent.PersonalAgentError, "not installed"):
                agent.run("find one", SCHEMA)

    def test_another_harness_is_refused_rather_than_silently_substituted(self) -> None:
        with patch("fitlit.config.HARNESS", "codex"):
            with self.assertRaisesRegex(agent.PersonalAgentError, "HARNESS=claude"):
                agent.run("find one", SCHEMA)


class EnvironmentTests(unittest.TestCase):
    def test_push_credentials_are_stripped_but_the_oauth_home_survives(self) -> None:
        source = {
            "PATH": "/usr/bin",
            "HOME": "/home/owner",
            "GITHUB_TOKEN": "secret",
            "GH_TOKEN": "secret",
            "FITLIT_GMAIL_TO": "you@example.com",
        }
        with patch("os.environ", source):
            environment = agent._environment()
        self.assertEqual("/home/owner", environment["HOME"])
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("FITLIT_GMAIL_TO", environment)


if __name__ == "__main__":
    unittest.main()
