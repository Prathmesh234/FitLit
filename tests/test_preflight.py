from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import preflight


class PreflightHarnessTests(unittest.TestCase):
    def test_blank_non_copilot_models_use_provider_defaults(self) -> None:
        for harness in ("codex", "claude", "opencode"):
            dotenv = {
                "HARNESS": harness,
                "FITLIT_TELEGRAM_ENABLED": "true",
                "FITLIT_TELEGRAM_BOT_TOKEN": "123456:" + ("a" * 30),
                "FITLIT_TELEGRAM_TRUSTED_USER_ID": "123456789",
                f"FITLIT_EMAIL_AGENT_{harness.upper()}_MODEL": "",
                f"FITLIT_TELEGRAM_{harness.upper()}_MODEL": "",
            }
            with (
                self.subTest(harness=harness),
                patch.dict("os.environ", {}, clear=True),
                patch.object(preflight, "_dotenv", return_value=dotenv),
                patch.object(preflight.shutil, "which", return_value="/bin/tool"),
            ):
                result = preflight.collect()
            self.assertIsNone(result["gmail_poll"]["email_agent"]["model"])
            self.assertIsNone(result["telegram"]["model"])
            self.assertTrue(result["telegram"]["model_valid"])
            self.assertTrue(result["telegram"]["ready"])

    def test_copilot_keeps_independent_telegram_default(self) -> None:
        dotenv = {
            "HARNESS": "copilot",
            "FITLIT_TELEGRAM_ENABLED": "true",
            "FITLIT_TELEGRAM_BOT_TOKEN": "123456:" + ("a" * 30),
            "FITLIT_TELEGRAM_TRUSTED_USER_ID": "123456789",
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(preflight, "_dotenv", return_value=dotenv),
            patch.object(preflight.shutil, "which", return_value="/bin/copilot"),
        ):
            result = preflight.collect()
        self.assertEqual(
            "gpt-5.6-sol",
            result["gmail_poll"]["email_agent"]["model"],
        )
        self.assertEqual("gpt-5.6-terra", result["telegram"]["model"])
        self.assertTrue(result["telegram"]["model_valid"])


if __name__ == "__main__":
    unittest.main()
