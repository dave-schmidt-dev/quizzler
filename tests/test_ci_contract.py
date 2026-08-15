"""Static, secret-free checks for the hosted validation workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/validate.yml"


class CIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.job = cls.workflow["jobs"]["validate"]
        cls.steps = cls.job["steps"]

    def test_uses_pinned_public_runner_and_read_only_permissions(self):
        self.assertEqual(self.job["runs-on"], "macos-26")
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_checks_reviewed_pins_and_release_cli_capabilities(self):
        self.assertIn("app/.xcode-version", self.source)
        self.assertIn("app/release-config.toml", self.source)
        self.assertIn("xcrun simctl list runtimes", self.source)
        self.assertIn("DEVELOPER_DIR", self.source)
        self.assertIn("preferred_xcode", self.source)
        self.assertIn("altool", self.source)
        self.assertIn("cktool", self.source)
        self.assertIn("deliberately re-pin", self.source)

    def test_runs_graph_fingerprint_and_both_authoritative_gates(self):
        for command in (
            "swift package --package-path app/QuizzlerKit describe --type json",
            "app/QuizzlerKit/Package.swift",
            "xcodebuild -project app/Quizzler.xcodeproj",
            "shasum -a 256",
            "./app/test-gate.sh",
            "npm test",
        ):
            self.assertIn(command, self.source)
        self.assertIn("SWIFT_VERSION = 6.0", self.source)
        self.assertIn("semantic package/Xcode target and dependency parity verified", self.source)
        self.assertIn("dependencies", self.source)
        self.assertIn("quizzler-xcode-semantic-settings.txt", self.source)
        self.assertIn("Release-path Xcode tests", self.source)
        self.assertIn("xcodebuild test", self.source)
        self.assertIn("-configuration Release", self.source)
        self.assertIn("CODE_SIGNING_ALLOWED=NO", self.source)
        self.assertIn("-resultBundlePath", self.source)
        self.assertIn("quizzler-*.xcresult", self.source)

    def test_lint_dead_code_and_logs_are_explicit(self):
        self.assertIn("swiftlint lint --config app/.swiftlint.yml --strict", self.source)
        self.assertIn("periphery scan --config app/.periphery.yml --strict", self.source)
        self.assertIn("actions/upload-artifact@v4", self.source)
        self.assertIn("if: always()", self.source)
        self.assertIn("if-no-files-found: error", self.source)

    def test_root_swiftlint_config_delegates_to_app_config(self):
        config = yaml.safe_load((ROOT / ".swiftlint.yml").read_text(encoding="utf-8"))
        self.assertEqual(config, {"child_config": "app/.swiftlint.yml"})

    def test_periphery_config_retains_serialization_and_only_runtime_hooks(self):
        config = yaml.safe_load((ROOT / "app/.periphery.yml").read_text(encoding="utf-8"))
        self.assertTrue(config["retain_codable_properties"])
        self.assertEqual(
            config["retain_files"],
            [
                "app/QuizzleriOS/FailureInjection.swift",
                "app/QuizzleriOS/TestingSupport/UITestFixture.swift",
            ],
        )
        self.assertNotIn("index_exclude", config)
        self.assertNotIn("report_exclude", config)

    def test_cache_paths_exclude_credentials_and_secret_transports(self):
        cache_block = self.source[self.source.index("actions/cache@v4") : self.source.index("- name: Verify reviewed")]
        self.assertNotRegex(cache_block, re.compile(r"\.env|\.aws|\.ssh|keychain|bws|secret", re.I))
        self.assertNotRegex(self.source, re.compile(r"bws-run|bws-get|Authorization:|API_KEY|PRIVATE_KEY", re.I))
        self.assertNotIn("cloudkit", self.source.lower())


if __name__ == "__main__":
    unittest.main()
