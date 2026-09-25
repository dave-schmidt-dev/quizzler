"""Tests for the pre-commit file-size checker."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_file_size.py"


def run_check(tmp_path: Path, *arguments: str, env: dict[str, str] | None = None):
    """Run the checker in *tmp_path* with *arguments*."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def write_lines(path: Path, line_count: int) -> None:
    """Write *line_count* newline-terminated lines to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n" * line_count)


class FileSizeCheckerTests(unittest.TestCase):
    """Exercise target, ceiling, and exception format behavior."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.tmp_path = Path(self.tempdir.name)

    def test_target_and_ceiling_boundaries(self) -> None:
        for line_count, returncode in ((500, 0), (501, 0), (800, 0), (801, 1)):
            with self.subTest(line_count=line_count):
                path = self.tmp_path / f"size_{line_count}.py"
                write_lines(path, line_count)
                result = run_check(self.tmp_path, path.name)
                self.assertEqual(result.returncode, returncode, result.stderr)
                if line_count == 500:
                    self.assertEqual(result.stdout, "")
                if line_count in (501, 800):
                    self.assertIn(f"target 500", result.stdout)
                if line_count == 801:
                    self.assertIn(path.name, result.stderr)
                    self.assertIn("801 lines", result.stderr)

    def test_listed_ceiling_violation_passes_and_small_entry_is_removable(self) -> None:
        write_lines(self.tmp_path / "large.py", 801)
        write_lines(self.tmp_path / "small.py", 10)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py generated protocol bridge\nsmall.py generated protocol bridge\n",
            encoding="utf-8",
        )

        large = run_check(self.tmp_path, "large.py")
        small = run_check(self.tmp_path, "small.py")

        self.assertEqual(large.returncode, 0, large.stderr)
        self.assertEqual(small.returncode, 0, small.stderr)
        self.assertIn("remove its exception", small.stdout)

    def test_file_mode_prints_legacy_exception_notice(self) -> None:
        write_lines(self.tmp_path / "legacy.py", 801)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "legacy.py legacy generated protocol bridge\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "legacy.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "file-size: legacy.py is a legacy exception (801 lines); "
            "extract a clean seam from it in this piece of work\n",
        )

    def test_invalid_exception_formats_fail_without_named_files(self) -> None:
        cases = {
            "reason-less": "missing.py\n",
            "legacy-cap": "capped.py 900 generated output\n",
            "duplicate": "same.py a reason\nsame.py another reason\n",
        }
        for name, contents in cases.items():
            with self.subTest(name=name):
                (self.tmp_path / ".file-size-exceptions").write_text(contents, encoding="utf-8")
                result = run_check(self.tmp_path, "missing.py")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr)
                if name == "legacy-cap":
                    self.assertIn("line caps are no longer supported; remove the cap", result.stderr)

    def test_final_line_without_newline_counts(self) -> None:
        (self.tmp_path / "partial.py").write_bytes(b"\n" * 500 + b"x")
        result = run_check(self.tmp_path, "partial.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("501 lines", result.stdout)

    def test_unchecked_and_excluded_paths_pass(self) -> None:
        write_lines(self.tmp_path / "large.json", 801)
        write_lines(self.tmp_path / "app/vendor/large.swift", 801)
        result = run_check(self.tmp_path, "large.json", "app/vendor/large.swift")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_mode_exits_two(self) -> None:
        result = run_check(self.tmp_path)
        self.assertEqual(result.returncode, 2)

    def test_target_above_ceiling_fails(self) -> None:
        write_lines(self.tmp_path / "small.py", 10)
        result = run_check(self.tmp_path, "--target", "900", "small.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--target cannot exceed --max-lines", result.stderr)


class GitModeTests(unittest.TestCase):
    """Exercise working-tree and index object modes."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = Path(self.tempdir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "file-size@example.invalid")
        self.git("config", "user.name", "File Size Test")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "seed.txt")
        self.git("commit", "-qm", "seed")

    def git(self, *arguments: str, env: dict[str, str] | None = None) -> None:
        subprocess.run(["git", *arguments], cwd=self.repo, check=True, env=env)

    def stage(self, relative_path: str) -> None:
        self.git("add", relative_path)

    def commit_big_file_with_exception(self) -> None:
        write_lines(self.repo / "big.py", 801)
        (self.repo / ".file-size-exceptions").write_text(
            "big.py generated protocol bridge\n", encoding="utf-8"
        )
        self.stage("big.py")
        self.stage(".file-size-exceptions")
        self.git("commit", "-qm", "add big fixture")

    def test_staged_801_line_file_fails(self) -> None:
        write_lines(self.repo / "staged.py", 801)
        self.stage("staged.py")
        result = run_check(self.repo, "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("staged.py", result.stderr)

    def test_staged_800_line_file_ignores_working_tree_growth(self) -> None:
        write_lines(self.repo / "staged.py", 800)
        self.stage("staged.py")
        write_lines(self.repo / "staged.py", 801)
        result = run_check(self.repo, "--staged")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("800 lines", result.stdout)

    def test_untracked_801_line_file_is_not_checked_by_staged_mode(self) -> None:
        write_lines(self.repo / "untracked.py", 801)
        result = run_check(self.repo, "--staged")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_second_index_with_staged_801_line_file_fails(self) -> None:
        write_lines(self.repo / "alternate.py", 801)
        self.stage("alternate.py")
        alternate_index = self.repo / "alternate.index"
        shutil.copy2(self.repo / ".git/index", alternate_index)
        environment = dict(os.environ, GIT_INDEX_FILE=str(alternate_index))
        result = run_check(self.repo, "--staged", env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("alternate.py", result.stderr)

    def test_second_index_selects_staged_legacy_notice(self) -> None:
        self.commit_big_file_with_exception()
        write_lines(self.repo / "big.py", 802)
        (self.repo / ".file-size-exceptions").write_text(
            "big.py legacy generated protocol bridge\n", encoding="utf-8"
        )
        self.stage("big.py")
        self.stage(".file-size-exceptions")
        alternate_index = self.repo / "alternate.index"
        shutil.copy2(self.repo / ".git/index", alternate_index)
        self.git("reset", "-q", "HEAD")
        environment = dict(os.environ, GIT_INDEX_FILE=str(alternate_index))
        result = run_check(self.repo, "--staged", env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("big.py is a legacy exception (802 lines)", result.stdout)

    def test_staged_and_file_modes_cannot_be_combined(self) -> None:
        result = run_check(self.repo, "--staged", "seed.txt")
        self.assertEqual(result.returncode, 2)

    def test_staged_exception_removal_checks_every_indexed_file(self) -> None:
        self.commit_big_file_with_exception()
        (self.repo / ".file-size-exceptions").write_text("", encoding="utf-8")
        self.stage(".file-size-exceptions")
        result = run_check(self.repo, "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("big.py", result.stderr)

    def test_staged_exception_deletion_checks_every_indexed_file(self) -> None:
        self.commit_big_file_with_exception()
        self.git("rm", ".file-size-exceptions")
        result = run_check(self.repo, "--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("big.py", result.stderr)

    def test_staged_legacy_exception_file_prints_notice(self) -> None:
        self.commit_big_file_with_exception()
        write_lines(self.repo / "big.py", 802)
        (self.repo / ".file-size-exceptions").write_text(
            "big.py legacy generated protocol bridge\n", encoding="utf-8"
        )
        self.stage("big.py")
        self.stage(".file-size-exceptions")

        result = run_check(self.repo, "--staged")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "file-size: big.py is a legacy exception (802 lines); "
            "extract a clean seam from it in this piece of work\n",
        )

    def test_staged_exception_change_suppresses_unstaged_legacy_notice(self) -> None:
        self.commit_big_file_with_exception()
        (self.repo / ".file-size-exceptions").write_text(
            "big.py legacy generated protocol bridge\n", encoding="utf-8"
        )
        self.stage(".file-size-exceptions")

        result = run_check(self.repo, "--staged")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_all_suppresses_legacy_exception_notice(self) -> None:
        write_lines(self.repo / "big.py", 801)
        (self.repo / ".file-size-exceptions").write_text(
            "big.py legacy generated protocol bridge\n", encoding="utf-8"
        )

        result = run_check(self.repo, "--all")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_nonlegacy_exception_reason_suppresses_notice(self) -> None:
        write_lines(self.repo / "big.py", 801)
        (self.repo / ".file-size-exceptions").write_text(
            "big.py generated protocol bridge\n", encoding="utf-8"
        )
        self.stage("big.py")
        self.stage(".file-size-exceptions")

        result = run_check(self.repo, "--staged")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_all_checks_untracked_nonignored_files_but_skips_ignored_ones(self) -> None:
        write_lines(self.repo / "untracked.py", 801)
        result = run_check(self.repo, "--all")
        self.assertEqual(result.returncode, 1)
        self.assertIn("untracked.py", result.stderr)
        (self.repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        write_lines(self.repo / "ignored.py", 801)
        (self.repo / "untracked.py").unlink()
        result = run_check(self.repo, "--all")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
