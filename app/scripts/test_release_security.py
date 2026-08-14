#!/usr/bin/env python3
"""Security contracts for TestFlight orchestration."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_testflight_workflow import FakeProvider  # noqa: E402
from testflight_workflow import BWS_MARKER, WorkflowError, _write_state, main, run_workflow  # noqa: E402


class ReleaseSecurityTests(unittest.TestCase):
    def test_provider_secret_is_sanitized_to_fixed_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider = FakeProvider(Path(temporary), failure="runtime")
            with self.assertRaisesRegex(WorkflowError, "provider-operation-failed"):
                run_workflow(provider, state_path=Path(temporary) / "state.json", attended=True, on_status=lambda _: None)

    def test_default_cli_never_prints_sensitive_provider_output(self) -> None:
        inherited_marker = os.environ.get(BWS_MARKER)
        with patch.dict(os.environ):
            os.environ.pop(BWS_MARKER, None)
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                self.assertEqual(main(["--attended", "--state", "/tmp/quizzler-testflight-safe-state.json"]), 2)
            self.assertIsNone(os.environ.get(BWS_MARKER))
        self.assertEqual(os.environ.get(BWS_MARKER), inherited_marker)
        self.assertIn("BLOCKED bws-consumer-boundary-required", captured.getvalue())
        self.assertNotIn("token", captured.getvalue().lower())
        self.assertNotIn("bws-secret-exec", captured.getvalue())

    def test_sensitive_state_fields_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(WorkflowError, "testflight-state-sensitive-data"):
                _write_state(
                    Path(temporary) / "state.json",
                    {"formatVersion": "1.0.0", "identity": {"token": "nope"}},
                )

    def test_state_is_private_and_never_contains_provider_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            _write_state(path, {"formatVersion": "1.0.0", "identity": {"candidateId": "candidate-1"}, "stage": "prepared", "archive": {"sha256": "a" * 64}, "ipa": {"sha256": "b" * 64}, "ascBuildId": None})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"(?i)token|secret|password|credential|api[_-]?key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
