#!/usr/bin/env python3
"""Public command contract and aggregate Task 4.4 quick check."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_release_restart import ReleaseRestartTests  # noqa: F401,E402
from test_release_security import ReleaseSecurityTests  # noqa: F401,E402
from test_testflight_workflow import TestFlightWorkflowTests  # noqa: F401,E402


ROOT = Path(__file__).resolve().parents[2]


class DeployTestFlightCommandTests(unittest.TestCase):
    def test_only_public_command_requires_attended_flag_and_emits_no_secret(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        denied = subprocess.run([str(command)], text=True, capture_output=True, check=False)
        self.assertEqual(denied.returncode, 64)
        self.assertIn("explicit --attended invocation is required", denied.stderr)
        self.assertEqual(denied.stdout, "")
        self.assertNotIn("release-testflight", command.read_text(encoding="utf-8"))
        self.assertIn('"${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', command.read_text(encoding="utf-8"))
        self.assertIn("quizzler-testflight-upload -- --attended", command.read_text(encoding="utf-8"))
        self.assertIn('PROJECT_PYTHON="/opt/homebrew/bin/python3"', command.read_text(encoding="utf-8"))

    def test_legacy_operator_paths_are_retired(self) -> None:
        for name in ("release-status", "release-testflight"):
            result = subprocess.run([str(ROOT / "app" / name)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 64)
            self.assertEqual(result.stdout, "")
            self.assertIn("retired", result.stderr)
        status = (ROOT / "app" / "release-status").read_text(encoding="utf-8")
        self.assertIn("prepare-testflight-candidate", status)
        self.assertNotIn("apple_developer", status)

    def test_unmarked_entry_uses_only_the_fixed_bws_consumer(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        source = command.read_text(encoding="utf-8")
        self.assertIn('exec /opt/homebrew/bin/python3 "${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', source)
        self.assertIn('quizzler-testflight-upload -- --attended', source)
        self.assertNotIn('exec bws-secret-exec ', source)

    def test_marked_entry_uses_project_python_and_fixed_workflow(self) -> None:
        source = (ROOT / "app" / "deploy-testflight").read_text(encoding="utf-8")
        self.assertIn('PROJECT_PYTHON="/opt/homebrew/bin/python3"', source)
        self.assertIn('"$SCRIPT_DIR/scripts/testflight_workflow.py" --attended', source)
        self.assertIn('QUIZZLER_TESTFLIGHT_BWS_CONSUMER', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
