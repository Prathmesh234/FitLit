from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fitlit import gmail_inbox
from scripts import install_services, privacy_scan

ROOT = Path(__file__).resolve().parent.parent


class BuildPrivacyTests(unittest.TestCase):
    def test_docker_context_is_deny_all_with_private_data_excluded(self) -> None:
        rules = [
            line.strip()
            for line in (ROOT / ".dockerignore").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual("**", rules[0])
        self.assertIn("!/data/fitbit_endpoints.yaml", rules)
        self.assertNotIn("!/data/**", rules)

    def test_dockerfile_never_copies_the_working_tree(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertNotIn("COPY . .", dockerfile)
        self.assertIn("COPY fitlit ./fitlit", dockerfile)
        self.assertIn(
            "COPY data/fitbit_endpoints.yaml ./data/fitbit_endpoints.yaml",
            dockerfile,
        )


class RuntimePrivacyTests(unittest.TestCase):
    def test_new_gmail_ledger_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gmail-inbox.db"
            gmail_inbox.InboxStore(path)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_cloud_example_does_not_enable_public_ingress(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertNotIn("--ingress " + "external", readme)
        self.assertIn("--ingress " + "internal", readme)
        self.assertNotIn("docker run -p " + "8000:8000", readme)

    def test_installer_hardens_existing_private_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            nested = root / "nested"
            nested.mkdir(parents=True, mode=0o755)
            private_file = nested / "gmail-inbox.db"
            private_file.write_text("metadata")
            private_file.chmod(0o644)
            with patch.object(install_services, "PRIVATE_PATHS", (root,)):
                install_services.harden_private_paths()
            self.assertEqual(0o700, root.stat().st_mode & 0o777)
            self.assertEqual(0o700, nested.stat().st_mode & 0o777)
            self.assertEqual(0o600, private_file.stat().st_mode & 0o777)

    def test_installer_requires_configured_telegram_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "FITLIT_TELEGRAM_BOT_TOKEN=\n"
                "FITLIT_TELEGRAM_TRUSTED_USER_ID=123\n"
            )
            with (
                patch.object(install_services, "ROOT", root),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertFalse(
                    install_services._env_configured(
                        "FITLIT_TELEGRAM_BOT_TOKEN"
                    )
                )
                self.assertTrue(
                    install_services._env_configured(
                        "FITLIT_TELEGRAM_TRUSTED_USER_ID"
                    )
                )
                self.assertFalse(install_services._telegram_ready())

    def test_installer_removes_legacy_whatsapp_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root / "data" / "state" / "whatsapp-auth"
            auth.mkdir(parents=True)
            (auth / "creds.json").write_text("private")
            ledger = root / "data" / "state" / "whatsapp-ledger.json"
            ledger.write_text("{}")
            with patch.object(install_services, "ROOT", root):
                self.assertTrue(
                    install_services.remove_legacy_whatsapp_state()
                )
            self.assertFalse(auth.exists())
            self.assertFalse(ledger.exists())

    def test_privacy_scanner_detects_common_oauth_and_pat_formats(self) -> None:
        values = "\n".join(
            (
                "gh" + "p_" + ("a" * 30),
                "GOC" + "SPX-" + ("b" * 24),
                "1" + "//" + ("c" * 30),
                "ya" + "29." + ("d" * 30),
                "+" + "1" + ("2" * 10),
                ("1" * 9) + ":" + ("e" * 35),
            )
        )
        kinds = {
            finding.rsplit(": ", 1)[-1]
            for finding in privacy_scan._scan_text("fixture", values)
        }
        self.assertEqual(
            {
                "github-token",
                "google-client-secret",
                "google-refresh-token",
                "google-access-token",
                "e164-phone",
                "telegram-bot-token",
            },
            kinds,
        )

    def test_privacy_scanner_ignores_systemd_templated_unit_names(self) -> None:
        # "fitlit-personal@coffee.service" is shaped like an address; every
        # personal task adds more references to one, so a false positive here
        # would make the public-release gate permanently noisy.
        findings = privacy_scan._scan_text(
            "fixture",
            "Unit=fitlit-personal@coffee.service\n"
            "systemctl status fitlit-personal@errands.timer\n",
        )
        self.assertEqual([], findings)

    def test_privacy_scanner_still_reports_a_real_address(self) -> None:
        # Assembled rather than written out: a literal address here would be
        # reported by the very scanner this file is testing.
        address = "owner" + "@" + "somewhere.dev"
        findings = privacy_scan._scan_text("fixture", f"{address}\n")
        self.assertEqual(["fixture:1: email"], findings)

    def test_privacy_scanner_includes_untracked_public_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "new.txt").write_text("+" + "1" + ("2" * 10))
            with (
                patch.object(privacy_scan, "ROOT", root),
                patch.object(privacy_scan, "_git", return_value="new.txt\n"),
            ):
                findings = privacy_scan.scan_current()
        self.assertEqual(["new.txt:1: e164-phone"], findings)


if __name__ == "__main__":
    unittest.main()
