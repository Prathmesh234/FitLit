from __future__ import annotations

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

    def test_privacy_scanner_detects_common_oauth_and_pat_formats(self) -> None:
        values = "\n".join(
            (
                "gh" + "p_" + ("a" * 30),
                "GOC" + "SPX-" + ("b" * 24),
                "1" + "//" + ("c" * 30),
                "ya" + "29." + ("d" * 30),
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
            },
            kinds,
        )


if __name__ == "__main__":
    unittest.main()
