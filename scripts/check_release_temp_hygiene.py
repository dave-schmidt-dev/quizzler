#!/usr/bin/env python3
"""Reject test commands that leave new top-level release fixtures in TMPDIR."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PREFIX = "quizzler-release"


def effective_temp_root() -> Path:
    """Return the canonical directory used by the current process for temp files."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).resolve(strict=True)


def release_entries(root: Path) -> set[Path]:
    """Return canonical paths for matching entries directly below ``root``."""
    return {
        entry.resolve(strict=False)
        for entry in root.iterdir()
        if entry.name.startswith(PREFIX)
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command to check, after --")
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command.pop(0)
    if not args.command:
        parser.error("a test command is required after --")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the requested command and reject any release fixture it leaks."""
    args = parse_args(argv)
    try:
        root = effective_temp_root()
        before = release_entries(root)
    except OSError as error:
        print(f"FAIL: cannot snapshot release temp root: {error}", file=sys.stderr)
        return 2

    try:
        result = subprocess.run(args.command, check=False)
        command_status = result.returncode
    except OSError as error:
        print(f"FAIL: could not run checked command: {error}", file=sys.stderr)
        command_status = 127

    try:
        leaked = sorted(release_entries(root) - before)
    except OSError as error:
        print(f"FAIL: cannot re-snapshot release temp root: {error}", file=sys.stderr)
        return 2

    if leaked:
        print("FAIL: release temp hygiene leaked:", file=sys.stderr)
        for path in leaked:
            print(f"  {path}", file=sys.stderr)
        return 1
    return command_status


if __name__ == "__main__":
    raise SystemExit(main())
