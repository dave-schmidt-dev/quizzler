"""Enforce justified file-size limits for source and test files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


DEFAULT_MAX_LINES = 500
DEFAULT_EXCEPTIONS_PATH = ".file-size-exceptions"
CHECKED_SUFFIXES = (".swift", ".py", ".js", ".mjs", ".sh")
EXCLUDED_PREFIXES = ("app/vendor/", "node_modules/", "question-packs/")


def count_lines(path: Path) -> int:
    """Return the line count of *path* without decoding its contents."""
    contents = path.read_bytes()
    if not contents:
        return 0
    return contents.count(b"\n") + int(not contents.endswith(b"\n"))


def load_exceptions(path: Path, *, strict: bool) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """Load exception entries from *path* and return any format errors."""
    if not path.exists():
        return {}, []

    exceptions: dict[str, tuple[int, str]] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=2)
        if len(fields) < 3 or not fields[2].strip():
            if strict:
                errors.append(
                    f"{path}:{line_number}: exception entry needs a positive cap and reason"
                )
            continue
        entry_path, cap_text, reason = fields
        try:
            cap = int(cap_text)
        except ValueError:
            if strict:
                errors.append(f"{path}:{line_number}: cap must be a positive integer")
            continue
        if cap <= 0:
            if strict:
                errors.append(f"{path}:{line_number}: cap must be a positive integer")
            continue
        exceptions[entry_path] = (cap, reason)
    return exceptions, errors


def is_checked_path(file_name: str) -> bool:
    """Return whether *file_name* is a source path subject to the limit."""
    normalized = file_name.replace("\\", "/")
    return (
        normalized.endswith(CHECKED_SUFFIXES)
        and not normalized.startswith(EXCLUDED_PREFIXES)
        and ".xcodeproj/" not in normalized
    )


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    """Parse command-line *arguments* for the file-size checker."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument("--exceptions", default=DEFAULT_EXCEPTIONS_PATH)
    parser.add_argument("--baseline")
    parser.add_argument("files", metavar="FILE", nargs="+")
    return parser.parse_args(arguments)


def validate_grandfathered_caps(
    exceptions: dict[str, tuple[int, str]], baseline_path: Path | None
) -> list[str]:
    """Return errors when grandfathered caps exceed those in *baseline_path*."""
    if baseline_path is None:
        return []
    baseline, _ = load_exceptions(baseline_path, strict=False)
    errors: list[str] = []
    for entry_path, (cap, reason) in exceptions.items():
        if not reason.startswith("grandfathered"):
            continue
        baseline_entry = baseline.get(entry_path)
        if baseline_entry is None or cap > baseline_entry[0]:
            errors.append(
                f"{entry_path}: grandfathered exception is new or its cap increased"
            )
    return errors


def main(arguments: list[str] | None = None) -> int:
    """Check files named by *arguments* and return a process exit status."""
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    exceptions, errors = load_exceptions(Path(options.exceptions), strict=True)
    if options.max_lines <= 0:
        errors.append("--max-lines must be a positive integer")
    baseline_path = Path(options.baseline) if options.baseline else None
    errors.extend(validate_grandfathered_caps(exceptions, baseline_path))

    for file_name in options.files:
        path = Path(file_name)
        if not path.exists() or not is_checked_path(file_name):
            continue
        line_count = count_lines(path)
        exception = exceptions.get(file_name)
        exception_cap = exception[0] if exception is not None else None
        if line_count <= options.max_lines:
            if exception_cap is not None:
                print(
                    f"{file_name}: {line_count} lines is at or under "
                    f"{options.max_lines}; remove its exception from {options.exceptions}"
                )
        elif exception_cap is None or line_count > exception_cap:
            errors.append(
                f"{file_name}: {line_count} lines exceeds {options.max_lines}; split it or "
                f"add a justified entry to {options.exceptions}"
            )

    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
