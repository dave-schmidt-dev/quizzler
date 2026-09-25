"""Hermetic coverage for release-fixture hygiene tools."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import collect_release_temp as collector


ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_release_temp_hygiene.py"


class ReleaseTempHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp_root = Path(self.temporary.name)

    def run_checker(self, code: str) -> subprocess.CompletedProcess[str]:
        """Run the checker with a command that shares this hermetic TMPDIR."""
        environment = {**os.environ, "TMPDIR": str(self.temp_root)}
        return subprocess.run(
            [sys.executable, str(CHECKER), "--", sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_checker_accepts_a_clean_command(self) -> None:
        result = self.run_checker("raise SystemExit(0)")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_checker_rejects_a_successful_command_that_leaks(self) -> None:
        result = self.run_checker(
            "import os; from pathlib import Path; "
            "Path(os.environ['TMPDIR'], 'quizzler-release-success').mkdir()"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("release temp hygiene leaked", result.stderr)

    def test_checker_rejects_a_failing_command_that_leaks(self) -> None:
        result = self.run_checker(
            "import os; from pathlib import Path; "
            "Path(os.environ['TMPDIR'], 'quizzler-release-failure').mkdir(); "
            "raise SystemExit(7)"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("quizzler-release-failure", result.stderr)

    def test_collector_dry_run_reports_directories_without_removing_them(self) -> None:
        candidate = self.temp_root / "quizzler-release-dry-run"
        candidate.mkdir()
        (candidate / "payload").write_bytes(b"bytes")
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.temp_root)}, clear=False):
            with mock.patch.object(collector, "open_paths", return_value=set()):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = collector.main([])
        self.assertEqual(status, 0)
        self.assertTrue(candidate.exists())
        self.assertIn("dry-run: 1 candidate(s),", output.getvalue())
        self.assertIn("bytes", output.getvalue())

    def test_collector_rejects_a_top_level_symlink_outside_temp_root(self) -> None:
        outside_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temporary.cleanup)
        outside = Path(outside_temporary.name)
        (self.temp_root / "quizzler-release-link").symlink_to(outside, target_is_directory=True)
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.temp_root)}, clear=False):
            self.assertEqual(collector.release_directories(collector.effective_temp_root()), [])
        self.assertTrue(outside.exists())

    def test_collector_skips_held_open_directory_using_canonical_path(self) -> None:
        candidate = self.temp_root / "quizzler-release-held"
        candidate.mkdir()
        held_file = candidate / "active"
        held_file.touch()
        lsof = subprocess.CompletedProcess(
            ["lsof"], 0, stdout=f"n{held_file.resolve()}\n", stderr=""
        )
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.temp_root)}, clear=False):
            with mock.patch.object(collector.subprocess, "run", return_value=lsof):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = collector.main([])
        self.assertEqual(status, 0)
        self.assertTrue(candidate.exists())
        self.assertIn("dry-run: 0 candidate(s), 0 bytes; skipped 1 held-open", output.getvalue())

    def test_collector_fails_closed_when_lsof_is_unavailable(self) -> None:
        candidate = self.temp_root / "quizzler-release-no-lsof"
        candidate.mkdir()
        with mock.patch.dict(os.environ, {"TMPDIR": str(self.temp_root)}, clear=False):
            with mock.patch.object(collector.subprocess, "run", side_effect=OSError("missing")):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    status = collector.main([])
        self.assertEqual(status, 1)
        self.assertTrue(candidate.exists())
        self.assertIn("no candidates were removed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
