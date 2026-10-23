#!/usr/bin/env python3
"""Contract checks for the verify-once release path (gate legs and release checkout).

Static checks tie the adapter's ``gateLegs`` to the legs ``app/test-gate.sh`` runs and to
the wrappers that enter the release checkout. One behavior check proves the gate runs
every leg and names every failure. No simulator, Xcode build or UI is touched.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
ADAPTER = json.loads((ROOT / ".release" / "release-adapter.json").read_text(encoding="utf-8"))
GATE = (APP / "test-gate.sh").read_text(encoding="utf-8")
LEGS_LIB = APP / "test-gate-legs.sh"
LEGS = ADAPTER["gateLegs"]["legs"]
UI_LEGS = {"ui-phase", "accessibility"}
COPIED = tuple(path.rstrip("/") + "/" for path in ADAPTER["releaseCheckout"]["copyPaths"])
TEMPLATE = "release_tools/templates/release_checkout.sh"


def _run_gate_legs_body() -> str:
    start = GATE.index("run_gate_legs() {")
    return GATE[start : GATE.index("\n}\n", start)]


def _checkout_files() -> set[str]:
    """Every file a fresh checkout has, plus untracked work that is not ignored."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {name for name in listed.split("\0") if name}


class GateLegDeclarationTests(unittest.TestCase):
    def test_release_checkout_copies_the_untracked_inputs_the_gate_needs(self) -> None:
        # Course packs are bundled into the release build and tests/test_cloudkit_migration.py
        # reads the local inventory at import (runner-manifest imports it); both are gitignored.
        self.assertEqual(
            ADAPTER["releaseCheckout"],
            {"copyPaths": [".state/progress-source-inventory.json", "question-packs"]},
        )
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("question-packs/*/", ignored)
        self.assertIn(".state/*", ignored)
        self.assertNotIn("setupArgv", ADAPTER["releaseCheckout"])

    def test_declared_legs_are_exactly_the_legs_the_gate_runs(self) -> None:
        body = _run_gate_legs_body()
        run = set(re.findall(r"run_(?:gate|counting)_leg ([a-z][a-z0-9-]*)", body))
        self.assertEqual(run, set(LEGS))
        self.assertEqual({name for name, leg in LEGS.items() if leg["kind"] == "ui"}, UI_LEGS)
        counting = re.findall(r"COUNTING_LEG_NAMES=\(([^\n]+)\)", GATE)[0]
        for name in re.findall(r'"([^"]+)"', counting):
            self.assertIn(f"run_counting_leg {name} ", body)
            self.assertEqual(LEGS[name]["kind"], "headless")

    def test_phase_native_is_the_same_leg_the_aggregate_gate_runs(self) -> None:
        library = LEGS_LIB.read_text(encoding="utf-8")
        self.assertIn("run_gate_leg native-phase", library)
        self.assertIn("run_native_leg\n    exit $?", GATE)

    def test_every_input_glob_matches_files_a_fresh_checkout_has(self) -> None:
        declared = ADAPTER["gateLegs"]
        patterns = set(declared["commonInputs"])
        for leg in LEGS.values():
            patterns.update(leg["inputs"])
        for pattern in sorted(patterns):
            with self.subTest(pattern):
                matched = list(ROOT.glob(pattern))
                self.assertTrue(matched, "glob matches nothing")
                allowed = _checkout_files()
                for path in matched:
                    relative = path.relative_to(ROOT).as_posix()
                    # Declared owner-local inputs are copied into the release checkout, so
                    # they are present there even though git ignores them.
                    if path.is_file() and not relative.startswith(COPIED):
                        self.assertIn(relative, allowed, "glob matches an ignored file")

    def test_no_leg_argv_carries_an_absolute_or_per_worktree_path(self) -> None:
        for line in _run_gate_legs_body().splitlines():
            if "run_gate_leg " in line or "run_counting_leg " in line:
                self.assertNotIn("$GATE_ROOT", line)
                self.assertNotIn("$PWD", line)
                self.assertNotIn("/Users/", line)
                self.assertNotIn("$HOME", line)
        self.assertNotIn("/Users/dave", GATE)
        self.assertNotIn("/Users/dave", (APP / "test-gate-selfcheck.sh").read_text(encoding="utf-8"))

    def test_screen_seizing_legs_run_only_in_ui_verify_mode(self) -> None:
        body = _run_gate_legs_body()
        for name in UI_LEGS:
            self.assertEqual(LEGS[name]["kind"], "ui")
            self.assertIn(f"run_gate_leg {name} ", body)
        library = LEGS_LIB.read_text(encoding="utf-8")
        self.assertIn("ui-verify          ui legs only", library)
        default = GATE[GATE.index("  validate_pinned_inputs\n  validate_counting_leg_declarations") :]
        self.assertNotIn("run_ui_phase", default)
        self.assertNotIn("run_accessibility_quick", default)


class WrapperTests(unittest.TestCase):
    def _source(self, name: str) -> str:
        return (APP / name).read_text(encoding="utf-8")

    def test_wrappers_enter_the_release_checkout_with_the_right_mode(self) -> None:
        expected = {"release-testflight": "refresh", "release-status": "stay", "deploy-testflight": "stay", "verify-release-ui": "stay"}
        for name, mode in expected.items():
            with self.subTest(name):
                source = self._source(name)
                self.assertIn(TEMPLATE, source)
                self.assertRegex(source, rf'release_checkout_exec "\$CENTRAL_ROOT" \.release/release-adapter\.json app/{name} {mode} "\$@"')

    def test_deploy_hops_into_the_checkout_only_after_the_broker(self) -> None:
        source = self._source("deploy-testflight")
        self.assertLess(source.index("bws-secret-exec.py"), source.index("release_checkout_exec"))
        self.assertLess(source.index("release_checkout_exec"), source.index("-m release_tools testflight"))

    def test_verify_release_ui_is_executable_and_runs_the_ui_gate_mode_only(self) -> None:
        path = APP / "verify-release-ui"
        self.assertTrue(path.stat().st_mode & 0o111)
        source = self._source("verify-release-ui")
        self.assertIn("QUIZZLER_GATE_MODE=ui-verify", source)
        self.assertNotIn("xcodebuild", source)
        self.assertNotIn("--phase ui", source)

    def test_wrapper_arguments_stay_closed(self) -> None:
        for name in ("release-status", "verify-release-ui"):
            source = self._source(name)
            self.assertIn("$# -eq 0", source)


class GateCollectionTests(unittest.TestCase):
    def test_every_leg_runs_and_every_failure_is_named(self) -> None:
        script = f"""
set -euo pipefail
GATE_ROOT={ROOT}
source {LEGS_LIB}
gate_leg() {{ echo "ran $1"; [[ "$1" != b && "$1" != d ]]; }}
gate_legs_finish() {{ return 0; }}
GATE_LEGS_MODE=release
run_gate_leg a -- true
run_gate_leg b -- true
run_gate_leg c -- true
run_gate_leg d -- true
gate_legs_conclude
"""
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.split(), ["ran", "a", "ran", "b", "ran", "c", "ran", "d"])
        self.assertIn("2 gate leg(s) failed: b d", result.stderr)

    def test_a_reused_counting_leg_still_counts_as_complete(self) -> None:
        script = f"""
set -euo pipefail
GATE_ROOT={ROOT}
source {LEGS_LIB}
gate_leg() {{ echo "reused $1"; }}
COUNTING_LEG_RUN_COUNT=0
run_counting_leg swift-contract true
run_counting_leg fixture-isolation true
echo "count=$COUNTING_LEG_RUN_COUNT"
"""
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("count=2", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
