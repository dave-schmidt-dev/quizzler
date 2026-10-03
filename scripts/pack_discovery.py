#!/usr/bin/env python3
"""The one rule for which files under ``question-packs/`` are installable packs.

An installable pack is ``question-packs/<course>/<name>.json`` where neither the
course directory nor the file name starts with ``_`` or ``.``. The underscore
prefix marks archive, staging and course-metadata entries (``_archive/``,
``_course.json``); the dot prefix covers ``.DS_Store`` and friends. The rule is
a prefix test, not an allowlist, so a newly installed course is picked up
without editing any caller. Any other ``.json`` in a course directory (including
``manifest.json``) is a pack: the native bundler ships every such file, so every
gate must cover it.

Stdlib only and import-free, because the git hooks run this file straight out of
the staged or pushed object set (see ``.githooks/lib/installable-packs.sh``):

    printf '%s\\n' question-packs/c/a.json question-packs/c/_course.json | \\
        python3 scripts/pack_discovery.py      # prints the first path only
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

PACKS_DIRNAME = "question-packs"
IGNORED_PREFIXES = ("_", ".")


def is_installable_course_dir(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith(IGNORED_PREFIXES)


def is_installable_pack_file(path: Path) -> bool:
    return path.is_file() and path.name.endswith(".json") and not path.name.startswith(IGNORED_PREFIXES)


def is_installable_pack_path(relpath: str) -> bool:
    """Apply the same rule to a repo-relative POSIX path (git's view of a file)."""
    parts = PurePosixPath(relpath).parts
    if len(parts) != 3 or parts[0] != PACKS_DIRNAME:
        return False
    _, course, name = parts
    return (
        name.endswith(".json")
        and not course.startswith(IGNORED_PREFIXES)
        and not name.startswith(IGNORED_PREFIXES)
    )


def iter_courses(packs_root: Path) -> Iterator[Path]:
    """Yield installable course directories, sorted by name."""
    if packs_root.is_dir():
        yield from sorted((p for p in packs_root.iterdir() if is_installable_course_dir(p)), key=lambda p: p.name)


def iter_course_packs(course_dir: Path) -> Iterator[Path]:
    """Yield the installable pack files directly inside one course, sorted by name."""
    if course_dir.is_dir():
        yield from sorted((p for p in course_dir.iterdir() if is_installable_pack_file(p)), key=lambda p: p.name)


def iter_installable_packs(packs_root: Path) -> Iterator[Path]:
    """Yield every installable pack under ``packs_root``, course by course."""
    for course_dir in iter_courses(packs_root):
        yield from iter_course_packs(course_dir)


def main() -> int:
    """Filter newline-separated repo-relative paths on stdin to the installable packs."""
    for line in sys.stdin.read().splitlines():
        if is_installable_pack_path(line):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
