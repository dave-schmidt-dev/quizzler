"""Enforce the repository's source and test file-size policy."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from pathlib import Path
import subprocess
import sys


DEFAULT_TARGET = 500
DEFAULT_MAX_LINES = 800
DEFAULT_EXCEPTIONS_PATH = ".file-size-exceptions"
CHECKED_SUFFIXES = (".swift", ".py", ".js", ".mjs", ".sh")
EXCLUDED_PREFIXES = ("app/vendor/", "node_modules/", "question-packs/")


def count_lines(contents: bytes) -> int:
    """Return the line count in *contents*, including a final partial line."""
    if not contents:
        return 0
    return contents.count(b"\n") + int(not contents.endswith(b"\n"))


def parse_exceptions(contents: str, source: str) -> tuple[dict[str, str], list[str]]:
    """Parse exception entries and return valid entries plus format errors."""
    exceptions: dict[str, str] = {}
    errors: list[str] = []
    seen_paths: set[str] = set()
    for line_number, raw_line in enumerate(contents.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=1)
        entry_path = fields[0]
        if entry_path in seen_paths:
            errors.append(f"{source}:{line_number}: duplicate exception path {entry_path}")
            continue
        seen_paths.add(entry_path)
        if len(fields) == 1 or not fields[1].strip():
            errors.append(f"{source}:{line_number}: exception entry needs a reason")
            continue
        reason = fields[1]
        if reason.split(maxsplit=1)[0].isdigit():
            errors.append(
                f"{source}:{line_number}: line caps are no longer supported; remove the cap"
            )
            continue
        exceptions[entry_path] = reason
    return exceptions, errors


def is_checked_path(file_name: str) -> bool:
    """Return whether *file_name* is a source path subject to the policy."""
    normalized = file_name.replace("\\", "/")
    return (
        normalized.endswith(CHECKED_SUFFIXES)
        and not normalized.startswith(EXCLUDED_PREFIXES)
        and ".xcodeproj/" not in normalized
    )


def run_git(arguments: list[str]) -> bytes:
    """Return stdout from a Git command in the current repository."""
    return subprocess.run(
        ["git", *arguments], check=True, capture_output=True
    ).stdout


def indexed_files() -> list[str]:
    """Return paths represented by the current Git index."""
    return [
        path.decode(sys.getfilesystemencoding())
        for path in run_git(["ls-files", "-z"]).split(b"\0")
        if path
    ]


def staged_files() -> list[str]:
    """Return paths changed between ``HEAD`` and the current Git index."""
    return [
        path.decode(sys.getfilesystemencoding())
        for path in run_git(["diff", "--cached", "--name-only", "-z"]).split(b"\0")
        if path
    ]


def all_files() -> list[str]:
    """Return tracked and non-ignored working-tree paths."""
    return [
        path.decode(sys.getfilesystemencoding())
        for path in run_git(["ls-files", "-z", "-co", "--exclude-standard"]).split(b"\0")
        if path
    ]


def index_blob(path: str) -> bytes | None:
    """Return *path* from the index, or ``None`` if it is absent."""
    result = subprocess.run(
        ["git", "cat-file", "blob", f":{path}"], capture_output=True
    )
    return result.stdout if result.returncode == 0 else None


def working_tree_blob(path: str) -> bytes | None:
    """Return *path* from the working tree, or ``None`` when it is absent."""
    file_path = Path(path)
    return file_path.read_bytes() if file_path.exists() else None


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    """Parse command-line *arguments* for the file-size checker."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--exceptions", default=DEFAULT_EXCEPTIONS_PATH)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--staged", action="store_true")
    parser.add_argument("files", metavar="FILE", nargs="*")
    options = parser.parse_args(arguments)
    if (not options.all and not options.staged and not options.files) or (
        (options.all or options.staged) and options.files
    ):
        parser.error("specify exactly one of FILE..., --all, or --staged")
    return options


def validate(
    files: Iterable[str],
    get_blob: Callable[[str], bytes | None],
    exceptions: dict[str, str],
    target: int,
    max_lines: int,
    exceptions_path: str,
    legacy_notice_files: set[str] | None = None,
) -> list[str]:
    """Check *files*, printing advisories and returning policy errors."""
    errors: list[str] = []
    for file_name in files:
        if not is_checked_path(file_name):
            continue
        contents = get_blob(file_name)
        if contents is None:
            continue
        line_count = count_lines(contents)
        listed = file_name in exceptions
        if line_count <= target:
            if listed:
                print(
                    f"file-size: {file_name} has {line_count} lines; "
                    f"remove its exception from {exceptions_path}"
                )
        elif line_count <= max_lines:
            print(
                f"file-size: {file_name} has {line_count} lines (target {target}); "
                "split it when a clean seam exists"
            )
            if listed:
                print(
                    f"file-size: {file_name} has {line_count} lines; "
                    f"remove its exception from {exceptions_path}"
                )
        elif not listed:
            errors.append(
                f"file-size: {file_name} has {line_count} lines (ceiling {max_lines}); "
                f"split it or add a justified entry to {exceptions_path}"
            )
        elif (
            legacy_notice_files is not None
            and file_name in legacy_notice_files
            and exceptions[file_name].startswith("legacy ")
        ):
            print(
                f"file-size: {file_name} is a legacy exception ({line_count} lines); "
                "extract a clean seam from it in this piece of work"
            )
    return errors


def main(arguments: list[str] | None = None) -> int:
    """Check files named by *arguments* and return a process exit status."""
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    errors: list[str] = []
    if options.target <= 0:
        errors.append("--target must be a positive integer")
    if options.max_lines <= 0:
        errors.append("--max-lines must be a positive integer")
    if options.target > options.max_lines:
        errors.append("--target cannot exceed --max-lines")

    if options.staged:
        exception_blob = index_blob(options.exceptions)
        exception_text = exception_blob.decode("utf-8") if exception_blob else ""
        files = indexed_files()
        get_blob = index_blob
        legacy_notice_files = set(staged_files())
    else:
        exception_file = Path(options.exceptions)
        exception_text = (
            exception_file.read_text(encoding="utf-8") if exception_file.exists() else ""
        )
        files = all_files() if options.all else options.files
        get_blob = working_tree_blob
        legacy_notice_files = None if options.all else set(options.files)

    exceptions, format_errors = parse_exceptions(exception_text, options.exceptions)
    errors.extend(format_errors)
    if not errors:
        errors.extend(
            validate(
                files,
                get_blob,
                exceptions,
                options.target,
                options.max_lines,
                options.exceptions,
                legacy_notice_files,
            )
        )
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
