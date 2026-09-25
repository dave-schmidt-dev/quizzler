#!/usr/bin/env python3
"""Report, or explicitly remove, stale top-level Quizzler release fixtures."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


PREFIX = "quizzler-release"


def effective_temp_root() -> Path:
    """Return the canonical directory used by the current process for temp files."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).resolve(strict=True)


def is_descendant(path: Path, root: Path) -> bool:
    """Return whether canonical ``path`` is strictly contained by canonical ``root``."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def release_directories(root: Path) -> list[Path]:
    """Find real, direct-child release directories without following symlinks."""
    candidates: list[Path] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.startswith(PREFIX) or entry.is_symlink():
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            candidate = Path(entry.path).resolve(strict=True)
            if is_descendant(candidate, root):
                candidates.append(candidate)
    return sorted(candidates)


def directory_bytes(directory: Path) -> int:
    """Sum metadata sizes below ``directory`` without opening file contents."""
    total = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            metadata = entry.stat(follow_symlinks=False)
            total += metadata.st_size
            if stat.S_ISDIR(metadata.st_mode) and not entry.is_symlink():
                total += directory_bytes(Path(entry.path))
    return total


def open_paths() -> set[Path]:
    """Return canonical paths reported by lsof, or fail closed when unavailable."""
    try:
        result = subprocess.run(
            ["lsof", "-F", "n"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        raise RuntimeError(f"lsof cannot be queried: {error}") from error
    if result.returncode != 0:
        raise RuntimeError(f"lsof cannot be queried (exit {result.returncode})")
    return {
        Path(line[1:]).resolve(strict=False)
        for line in result.stdout.splitlines()
        if line.startswith("n/")
    }


def is_held_open(candidate: Path, paths: set[Path]) -> bool:
    """Return whether lsof reported an open canonical path within ``candidate``."""
    return any(is_descendant(path, candidate) or path == candidate for path in paths)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="remove safe, closed candidates")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print dry-run totals, and remove only explicitly approved safe candidates."""
    args = parse_args(argv)
    try:
        root = effective_temp_root()
        candidates = release_directories(root)
    except OSError as error:
        print(f"FAIL: cannot inspect release temp directories: {error}", file=sys.stderr)
        return 1

    try:
        paths = open_paths()
    except RuntimeError as error:
        print(f"FAIL: {error}; no candidates were removed", file=sys.stderr)
        return 1

    held = [candidate for candidate in candidates if is_held_open(candidate, paths)]
    eligible = [candidate for candidate in candidates if candidate not in held]
    try:
        total = sum(directory_bytes(candidate) for candidate in eligible)
    except OSError as error:
        print(f"FAIL: cannot measure release temp directories: {error}", file=sys.stderr)
        return 1
    mode = "apply" if args.apply else "dry-run"
    print(f"{mode}: {len(eligible)} candidate(s), {total} bytes; skipped {len(held)} held-open")
    if not args.apply:
        return 0

    failed = False
    for candidate in eligible:
        # Re-resolve immediately before removal so a changed path cannot escape
        # the canonical temp root between discovery and apply.
        try:
            current = candidate.resolve(strict=True)
            if not is_descendant(current, root):
                print(f"skipped outside temp root: {candidate}", file=sys.stderr)
                failed = True
            elif is_held_open(current, paths):
                print(f"skipped held-open: {current}")
            else:
                shutil.rmtree(current)
                print(f"removed: {current}")
        except OSError as error:
            print(f"FAIL: could not remove {candidate}: {error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
