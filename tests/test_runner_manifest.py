"""Task 4.1 manifest and native-gate contract checks.

This is intentionally a small, dependency-free executable manifest.  It
guards the two places where a new test can otherwise become invisible: the
JavaScript Python-suite bridge and the native aggregate gate.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
PACKAGE = ROOT / "package.json"
GATE = ROOT / "app" / "test-gate.sh"
XCTESTPLAN = ROOT / "app" / "Quizzler.xctestplan"
APP_SCRIPTS = ROOT / "app" / "scripts"

# An app/scripts suite is only invisible if nothing names it. Each exclusion
# must record why the gate cannot run it, so an unwired suite is a decision
# rather than an oversight.
APP_SCRIPT_EXCLUSIONS: set[str] = set()

APP_SCRIPT_MODULE_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")
LEG_NAMES_RE = re.compile(r"COUNTING_LEG_NAMES=\(([^\n]+)\)")
LEG_FLOORS_RE = re.compile(r"COUNTING_LEG_MINIMUMS=\(([^\n]+)\)")


def on_disk_modules() -> set[str]:
    return {path.stem for path in TESTS.glob("test_*.py")}


def on_disk_app_script_modules() -> set[str]:
    return {path.stem for path in APP_SCRIPTS.glob("test_*.py")}


def gate_named_app_script_modules() -> set[str]:
    return set(APP_SCRIPT_MODULE_RE.findall(GATE.read_text(encoding="utf-8")))


def discovered_modules() -> set[str]:
    """Return modules actually found by unittest discovery."""
    # Running this file directly puts tests/, not the repository root, on
    # sys.path. Match `python -m unittest discover -s tests` so test imports
    # from the scripts package resolve the same way in both entry points.
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    suite = unittest.TestLoader().discover(
        start_dir=str(TESTS), pattern="test_*.py"
    )
    modules: set[str] = set()
    pending = [suite]
    while pending:
        item = pending.pop()
        if isinstance(item, unittest.TestSuite):
            pending.extend(item)
        else:
            modules.add(item.__class__.__module__)
    return modules


class RunnerManifestTests(unittest.TestCase):
    def test_task_45_release_suites_are_wired_into_release_workflow_leg(self):
        named = gate_named_app_script_modules()
        self.assertTrue(
            {
                "test_release_readiness",
                "test_prepare_testflight_candidate",
                "test_device_acceptance",
            }.issubset(named)
        )

    def test_every_python_suite_is_wired(self):
        self.assertEqual(sorted(on_disk_modules() - discovered_modules()), [])

    def test_npm_test_uses_the_same_unittest_discovery_contract(self):
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(
            package["scripts"]["test"],
            "python3 -m unittest discover -s tests -p 'test_*.py' -v",
        )
        self.assertIn("test_migrate_identity_report", discovered_modules())

    def test_no_python_suite_entry_is_phantom(self):
        self.assertEqual(sorted(discovered_modules() - on_disk_modules()), [])

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

    def test_the_gate_runs_its_own_self_check(self):
        """The self-check is only a gate if something runs it.

        It sat unreferenced by any runner, hook or manifest long enough to go
        red and to start rewriting the gate's own test-plan pin on each run.
        """

        source = GATE.read_text(encoding="utf-8")
        self.assertIn("bash app/test-gate-selfcheck.sh", source)
        self.assertTrue((ROOT / "app" / "test-gate-selfcheck.sh").exists())

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

    def test_native_phase_is_headless_and_selects_no_ui_targets(self):
        """`--phase native` runs on every push, so it must drive no app UI."""
        source = GATE.read_text(encoding="utf-8")
        helper = source[source.index("_run_xcodebuild_test_phase() {"):source.index("run_native_phase() {")]
        self.assertIn('only_testing+=("-only-testing:$target")', helper)
        native = source[source.index("run_native_phase() {"):source.index("run_ui_phase() {")]
        self.assertIn("_run_xcodebuild_test_phase", native)
        for target in ("QuizzlerKitTests", "QuizzleriOSTests", "QuizzlerSnapshotTests"):
            self.assertIn(f"    {target}", native)
        self.assertNotIn("QuizzleriOSUITests", native)

    def test_ui_phase_is_milestone_only_and_selects_declared_non_cloudkit_ui_targets(self):
        source = GATE.read_text(encoding="utf-8")
        ui = source[source.index("run_ui_phase() {"):source.index('if [[ "${BASH_SOURCE[0]}" == "$0" ]]')]
        self.assertIn("_run_xcodebuild_test_phase", ui)
        for target in (
            "QuizzleriOSUITests/QuizWorkflowUITests",
            "QuizzleriOSUITests/AccessibilityUITests",
            "QuizzleriOSUITests/ColdLaunchStingUITests",
            "QuizzleriOSUITests/CurriculumLabUITests",
            "QuizzleriOSUITests/StudyPreferencesUITests",
            "QuizzleriOSUITests/InfoPopoverUITests",
        ):
            self.assertIn(f"    {target}", ui)
        for target in ("QuizzlerKitTests", "QuizzleriOSTests", "QuizzlerSnapshotTests"):
            self.assertNotIn(f"    {target}", ui)
        self.assertNotIn("CloudKitDevelopmentProbeTests", ui)
        # Mac Catalyst and review-capture journeys have their own milestone
        # scripts; the gate never names them.
        self.assertNotIn("MacCatalystUITests", source)
        self.assertNotIn("ReviewCaptureUITests", source)
        self.assertIn('"$2" == "ui" ]]; then\n    run_ui_phase', source)
        # The default (no-argument) aggregate gate stays headless.
        default = source[source.index('  validate_pinned_inputs\n  validate_counting_leg_declarations'):]
        self.assertNotIn("run_ui_phase", default)
        self.assertNotIn("run_accessibility_quick", default)
        self.assertNotIn("QuizzleriOSUITests", default)

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
