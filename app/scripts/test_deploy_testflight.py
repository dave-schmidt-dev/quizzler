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
        self.assertIn('"${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', command.read_text(encoding="utf-8"))
        self.assertIn("quizzler-testflight-upload -- --attended", command.read_text(encoding="utf-8"))
        self.assertIn('CENTRAL_ROOT="$ROOT/../apple_developer"', command.read_text(encoding="utf-8"))

    def test_release_status_and_release_testflight_wrap_the_central_cli(self) -> None:
        # Content-only: this module is exercised by the release-workflow gate
        # leg against a tracked-objects-only tree, and the moment a real
        # candidate is ever prepared for quizzler, release-status's output
        # changes. Actual fail-closed execution is verified manually and
        # reported, not baked into this permanent assertion.
        for name in ("release-status", "release-testflight"):
            path = ROOT / "app" / name
            source = path.read_text(encoding="utf-8")
            self.assertIn("release_tools", source)
            self.assertIn('CENTRAL_ROOT="$ROOT/../apple_developer"', source)
            self.assertIn('--adapter "$ADAPTER"', source)

    def test_unmarked_entry_uses_only_the_fixed_bws_consumer(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        source = command.read_text(encoding="utf-8")
        self.assertIn('exec /opt/homebrew/bin/python3 "${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', source)
        self.assertIn('quizzler-testflight-upload -- --attended', source)
        self.assertNotIn('exec bws-secret-exec ', source)

    def test_marked_entry_wraps_the_central_release_tools_cli(self) -> None:
        source = (ROOT / "app" / "deploy-testflight").read_text(encoding="utf-8")
        self.assertIn('QUIZZLER_TESTFLIGHT_BWS_CONSUMER', source)
        self.assertIn('PYTHONPATH="$CENTRAL_ROOT" /usr/bin/python3 -m release_tools testflight', source)
        self.assertIn('--adapter "$ADAPTER" --repository "$ROOT"', source)
        self.assertNotIn("testflight_workflow.py", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
