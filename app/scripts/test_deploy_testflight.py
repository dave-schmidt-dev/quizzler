#!/usr/bin/env python3
"""Public command contract and aggregate Task 4.4 quick check."""

from __future__ import annotations

import subprocess
import sys
import os
import tempfile
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
        self.assertIn("bws-secret-exec quizzler-testflight-upload -- --attended", command.read_text(encoding="utf-8"))
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

    def test_marker_reentry_uses_only_the_fixed_bws_consumer(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "bws-secret-exec"
            fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
            fake.chmod(0o700)
            environment = {**os.environ, "PATH": f"{temporary}:{os.environ['PATH']}"}
            environment.pop("QUIZZLER_TESTFLIGHT_BWS_CONSUMER", None)
            result = subprocess.run([str(command), "--attended"], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "quizzler-testflight-upload -- --attended\n")
        self.assertEqual(result.stderr, "")

    def test_matching_marker_uses_project_python_and_reaches_provider_without_live_work(self) -> None:
        environment = {**os.environ, "QUIZZLER_TESTFLIGHT_BWS_CONSUMER": "quizzler-testflight-upload"}
        result = subprocess.run([str(ROOT / "app" / "deploy-testflight"), "--attended"], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, r"(?m)^BLOCKED (?:immutable-readiness-missing|candidate-working-tree-dirty|fixed-command-failed)$")
        self.assertIn("STATUS readiness-verification-started", result.stderr)
        self.assertNotIn("STATUS full-gate-started", result.stderr)
        self.assertNotIn("STATUS archive-started", result.stderr)
        self.assertNotIn("project-python-invalid", result.stderr)
        self.assertNotIn("No module named 'tomllib'", result.stderr)
        self.assertNotIn("bws-secret-exec", result.stderr)
        if "BLOCKED fixed-command-failed" in result.stderr:
            self.assertIn("STATUS immutable-readiness-started", result.stderr)
            self.assertNotIn("STATUS immutable-readiness-complete", result.stderr)
            self.assertNotIn("STATUS git-revision-started", result.stderr)
        elif "BLOCKED candidate-working-tree-dirty" in result.stderr:
            self.assertIn("STATUS immutable-readiness-complete", result.stderr)
            self.assertIn("STATUS git-candidate-cleanliness-complete", result.stderr)
        else:
            self.assertNotIn("STATUS immutable-readiness-started", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
