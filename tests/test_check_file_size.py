"""Tests for the pre-commit file-size checker."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_file_size.py"


def run_check(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the checker in *tmp_path* with *arguments*."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def write_lines(path: Path, line_count: int) -> None:
    """Write *line_count* newline-terminated lines to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n" * line_count)


class FileSizeCheckerTests(unittest.TestCase):
    """Exercise file-size checks and exception validation in a temporary tree."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.tmp_path = Path(self.tempdir.name)

    def test_exactly_500_lines_passes(self) -> None:
        write_lines(self.tmp_path / "limit.py", 500)

        result = run_check(self.tmp_path, "limit.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stdout)
        self.assertFalse(result.stderr)

    def test_501_lines_fails_and_names_file(self) -> None:
        write_lines(self.tmp_path / "too_large.py", 501)

        result = run_check(self.tmp_path, "too_large.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("too_large.py", result.stderr)

    def test_listed_file_within_its_cap_passes(self) -> None:
        write_lines(self.tmp_path / "generated.py", 501)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "generated.py 600 generated code\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "generated.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)

    def test_listed_file_over_its_cap_fails(self) -> None:
        write_lines(self.tmp_path / "generated.py", 601)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "generated.py 600 generated code\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "generated.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("generated.py", result.stderr)

    def test_entry_with_no_reason_fails(self) -> None:
        (self.tmp_path / ".file-size-exceptions").write_text(
            "generated.py 600\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "missing.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("reason", result.stderr)

    def test_non_integer_cap_fails(self) -> None:
        (self.tmp_path / ".file-size-exceptions").write_text(
            "generated.py many generated code\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "missing.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("positive integer", result.stderr)

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        write_lines(self.tmp_path / "generated.py", 501)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "\n# retained generated file\n\ngenerated.py 600 generated code\n",
            encoding="utf-8",
        )

        result = run_check(self.tmp_path, "generated.py")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonexistent_file_argument_is_skipped(self) -> None:
        result = run_check(self.tmp_path, "missing.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stdout)
        self.assertFalse(result.stderr)

    def test_max_lines_overrides_default(self) -> None:
        write_lines(self.tmp_path / "small.py", 2)

        result = run_check(self.tmp_path, "--max-lines", "1", "small.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("exceeds 1", result.stderr)

    def test_final_line_without_trailing_newline_is_counted(self) -> None:
        (self.tmp_path / "no_final_newline.py").write_bytes(b"\n" * 500 + b"x")

        result = run_check(self.tmp_path, "no_final_newline.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("501 lines", result.stderr)

    def test_removable_exception_note_is_non_failing(self) -> None:
        write_lines(self.tmp_path / "smaller.py", 500)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "smaller.py 600 generated code\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "smaller.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("remove its exception", result.stdout)
        self.assertFalse(result.stderr)

    def test_unchecked_suffix_passes(self) -> None:
        write_lines(self.tmp_path / "large.json", 600)

        result = run_check(self.tmp_path, "large.json")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_excluded_vendor_path_passes(self) -> None:
        write_lines(self.tmp_path / "app/vendor/x.swift", 600)

        result = run_check(self.tmp_path, "app/vendor/x.swift")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exceptions_file_is_validated_when_named_alone(self) -> None:
        (self.tmp_path / ".file-size-exceptions").write_text("bad.py 600\n", encoding="utf-8")

        result = run_check(self.tmp_path, ".file-size-exceptions")

        self.assertEqual(result.returncode, 1)
        self.assertIn("reason", result.stderr)

    def test_mjs_file_over_limit_fails(self) -> None:
        write_lines(self.tmp_path / "large.mjs", 501)

        result = run_check(self.tmp_path, "large.mjs")

        self.assertEqual(result.returncode, 1)
        self.assertIn("large.mjs", result.stderr)

    def test_raised_grandfathered_cap_fails_against_baseline(self) -> None:
        write_lines(self.tmp_path / "large.py", 600)
        (self.tmp_path / "baseline").write_text(
            "large.py 600 grandfathered original\n", encoding="utf-8"
        )
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py 601 grandfathered raised\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "--baseline", "baseline", "large.py")

        self.assertEqual(result.returncode, 1)
        self.assertIn("large.py", result.stderr)

    def test_shrunk_grandfathered_file_with_lower_cap_passes(self) -> None:
        write_lines(self.tmp_path / "large.py", 590)
        (self.tmp_path / "baseline").write_text(
            "large.py 600 grandfathered original\n", encoding="utf-8"
        )
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py 590 grandfathered shrunk\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "--baseline", "baseline", "large.py")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_grandfathered_entry_fails_against_baseline(self) -> None:
        write_lines(self.tmp_path / "large.py", 600)
        write_lines(self.tmp_path / "another.py", 600)
        (self.tmp_path / "baseline").write_text(
            "large.py 600 grandfathered original\n", encoding="utf-8"
        )
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py 600 grandfathered original\n"
            "another.py 600 grandfathered new\n",
            encoding="utf-8",
        )

        result = run_check(
            self.tmp_path, "--baseline", "baseline", "large.py", "another.py"
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("another.py", result.stderr)

    def test_new_non_grandfathered_entry_passes_against_baseline(self) -> None:
        write_lines(self.tmp_path / "large.py", 600)
        write_lines(self.tmp_path / "another.py", 600)
        (self.tmp_path / "baseline").write_text(
            "large.py 600 grandfathered original\n", encoding="utf-8"
        )
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py 600 grandfathered original\n"
            "another.py 600 generated artifact\n",
            encoding="utf-8",
        )

        result = run_check(
            self.tmp_path, "--baseline", "baseline", "large.py", "another.py"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_raised_grandfathered_cap_passes_without_baseline(self) -> None:
        write_lines(self.tmp_path / "large.py", 600)
        (self.tmp_path / ".file-size-exceptions").write_text(
            "large.py 601 grandfathered raised\n", encoding="utf-8"
        )

        result = run_check(self.tmp_path, "large.py")

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
