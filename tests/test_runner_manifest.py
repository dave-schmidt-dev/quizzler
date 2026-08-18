"""Task 4.1 manifest and native-gate contract checks.

This is intentionally a small, dependency-free executable manifest.  It
guards the two places where a new test can otherwise become invisible: the
JavaScript Python-suite bridge and the native aggregate gate.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PACKAGE = ROOT / "package.json"
PYTHON_SPEC = TESTS / "python-suites.spec.js"
GATE = ROOT / "app" / "test-gate.sh"
XCTESTPLAN = ROOT / "app" / "Quizzler.xctestplan"
APP_SCRIPTS = ROOT / "app" / "scripts"

# An app/scripts suite is only invisible if nothing names it. Each exclusion
# must record why the gate cannot run it, so an unwired suite is a decision
# rather than an oversight.
APP_SCRIPT_EXCLUSIONS: set[str] = set()

MODULE_RE = re.compile(r"tests\.test_[A-Za-z0-9_]+")
APP_SCRIPT_MODULE_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")
LEG_NAMES_RE = re.compile(r"COUNTING_LEG_NAMES=\(([^\n]+)\)")
LEG_FLOORS_RE = re.compile(r"COUNTING_LEG_MINIMUMS=\(([^\n]+)\)")


def on_disk_modules() -> set[str]:
    return {f"tests.{path.stem}" for path in TESTS.glob("test_*.py")}


def on_disk_app_script_modules() -> set[str]:
    return {path.stem for path in APP_SCRIPTS.glob("test_*.py")}


def gate_named_app_script_modules() -> set[str]:
    return set(APP_SCRIPT_MODULE_RE.findall(GATE.read_text(encoding="utf-8")))


def wired_modules() -> set[str]:
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    return set(MODULE_RE.findall(PYTHON_SPEC.read_text(encoding="utf-8"))) | set(
        MODULE_RE.findall(package["scripts"]["test"])
    )


class RunnerManifestTests(unittest.TestCase):
    def test_every_python_suite_is_wired(self):
        self.assertEqual(sorted(on_disk_modules() - wired_modules()), [])

    def test_no_python_suite_entry_is_phantom(self):
        self.assertEqual(sorted(wired_modules() - on_disk_modules()), [])

    def test_every_app_script_suite_is_named_by_the_gate_or_excluded(self):
        unwired = on_disk_app_script_modules() - gate_named_app_script_modules()
        self.assertEqual(
            sorted(unwired - APP_SCRIPT_EXCLUSIONS),
            [],
            "app/scripts suites exist that no gate leg runs; wire them or record an exclusion",
        )

    def test_no_app_script_exclusion_is_stale(self):
        on_disk = on_disk_app_script_modules()
        self.assertEqual(sorted(APP_SCRIPT_EXCLUSIONS - on_disk), [])
        # An excluded suite that the gate also names is a contradiction.
        self.assertEqual(sorted(APP_SCRIPT_EXCLUSIONS & gate_named_app_script_modules()), [])

    def test_native_gate_declares_positive_floors_for_each_leg(self):
        source = GATE.read_text(encoding="utf-8")
        names_match = LEG_NAMES_RE.search(source)
        floors_match = LEG_FLOORS_RE.search(source)
        self.assertIsNotNone(names_match)
        self.assertIsNotNone(floors_match)
        names = re.findall(r'"([^"]+)"', names_match.group(1))
        floors = [int(value) for value in re.findall(r"\d+", floors_match.group(1))]
        self.assertGreaterEqual(len(names), 1)
        self.assertEqual(len(names), len(floors))
        self.assertTrue(all(floor > 0 for floor in floors))
        self.assertIn("runner-manifest", names)

    def test_native_phase_selects_only_declared_non_cloudkit_ui_targets(self):
        source = GATE.read_text(encoding="utf-8")
        native = source[source.index("run_native_phase()"):source.index('if [[ "${BASH_SOURCE[0]}" == "$0" ]]')]
        for target in (
            "QuizzlerKitTests",
            "QuizzleriOSTests",
            "QuizzlerSnapshotTests",
            "QuizzleriOSUITests/QuizWorkflowUITests",
            "QuizzleriOSUITests/AccessibilityUITests",
        ):
            self.assertIn(f"-only-testing:{target}", native)
        self.assertNotIn("-only-testing:QuizzleriOSUITests/CloudKitDevelopmentProbeTests", native)

    def test_sync_phase_has_a_bounded_convergence_suite_contract(self):
        source = GATE.read_text(encoding="utf-8")
        sync = source[source.index("run_sync_phase()"):source.index("ACCESSIBILITY_TEST_CASE_COUNT")]
        for suite in (
            "CloudProgressRepositoryTests",
            "CloudKitMappingTests",
            "ProgressMergeTests",
            "SyncRecoveryTests",
            "MigrationReconciliationTests",
        ):
            self.assertIn(suite, source)
        self.assertIn("--filter", sync)
        self.assertIn("SYNC_TEST_MINIMUM", sync)
        self.assertNotIn("CloudKitDevelopmentProbeTests", sync)

    def test_contract_phase_requires_attended_signed_probe_and_exact_target(self):
        source = GATE.read_text(encoding="utf-8")
        self.assertIn("QUIZZLER_DEVELOPMENT_PROBE_RUN=1", source)
        self.assertIn("QUIZZLER_DEVELOPMENT_PROBE_DESTINATION", source)
        self.assertIn("QUIZZLER_DEVELOPMENT_PROBE_XCTESTRUN", source)
        self.assertIn("QUIZZLER_DEVELOPMENT_PROBE_SIGNED_APP", source)
        self.assertIn("QUIZZLER_DEVELOPMENT_PROBE_XCRESULT", source)
        self.assertIn("test-without-building", source)
        self.assertIn("-only-testing:QuizzleriOSUITests/CloudKitDevelopmentProbeTests", source)
        self.assertIn("bind_development_probe_xctestrun.py", source)
        self.assertIn('QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE:-', source)
        self.assertIn('QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE_RECOVERY:-', source)

    def test_default_gate_does_not_recreate_lint_or_post_tool_hooks(self):
        source = GATE.read_text(encoding="utf-8")
        default_start = source.index('if [[ "${BASH_SOURCE[0]}" == "$0" ]]')
        default = source[default_start:]
        self.assertNotIn("swiftlint", default)
        self.assertNotIn("periphery", default)
        self.assertNotIn("post-tool", default)

    def test_xctestplan_has_one_configuration_and_all_native_targets(self):
        plan = json.loads(XCTESTPLAN.read_text(encoding="utf-8"))
        self.assertEqual(len(plan["configurations"]), 1)
        self.assertEqual(
            sorted(target["target"]["name"] for target in plan["testTargets"]),
            ["QuizzlerKitTests", "QuizzlerSnapshotTests", "QuizzleriOSTests", "QuizzleriOSUITests"],
        )


if __name__ == "__main__":
    unittest.main()
