#!/usr/bin/env python3
"""Reject direct Xcode builds and caller-owned DerivedData paths in active files."""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT_SHELL = re.compile(r"(?<![\w/])(?:/usr/bin/)?xcodebuild\s+(?:-|build\b|test\b|archive\b|clean\b|\\|['\"]?\$\{)")
DIRECT_PYTHON = re.compile(r"(?m)\[\s*['\"](?:/usr/bin/)?xcodebuild['\"]")
DERIVED_FLAG = re.compile(r"-derivedDataPath(?:\b|=)")
COPYABLE_INLINE = re.compile(r"^\s*(?:[-*]\s+)?`[^`]+`\s*$")
FIXED_LOG = re.compile(r"/tmp/quizzler-[A-Za-z0-9_.-]+\.log\b")
HISTORICAL = re.compile(r"\b(?:formerly|previously|historical|used to|prior versions?)\b", re.I)


def active_paths(root: Path) -> list[Path]:
    """Find current scripts and docs without scanning tests or build evidence."""
    paths = list(root.glob("*.md"))
    paths.extend(root.glob("Makefile*"))
    paths.extend((root / ".githooks").rglob("*.sh"))
    paths.extend((root / ".githooks").glob("pre-*"))
    paths.extend((root / "docs").rglob("*.md"))
    paths.extend((root / "question-packs").rglob("*.md"))
    for directory in (root, root / "app"):
        paths.extend(directory.glob("*.sh"))
        paths.extend(directory.glob("*.py"))
    for directory in (root / "scripts", root / "app" / "scripts"):
        paths.extend(directory.rglob("*.sh"))
        paths.extend(directory.rglob("*.py"))
    fixture_builder = root / "app" / "scripts" / "test_artifact_metadata.py"
    historical_docs = {"HISTORY.md", "TASKS.md", "handoff.md"}
    active: set[Path] = set()
    for path in paths:
        if not path.is_file() or path.name == "check_build_paths.py":
            continue
        if path.suffix == ".md" and path.name in historical_docs:
            continue
        if path.suffix == ".py" and path.name.startswith(("test_", "test-")) and path != fixture_builder:
            continue
        if any(part in {".build", "build", ".logs", "tests"} for part in path.relative_to(root).parts):
            continue
        active.add(path)
    return sorted(active)


def violations(path: Path) -> list[str]:
    """Return policy violations from one owned active source or document."""
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    in_code = False
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if path.suffix == ".md" and stripped.startswith("```"):
            in_code = not in_code
            continue
        if stripped.startswith("#"):
            continue
        # Inline log paths are actionable guidance; plain historical prose is not.
        if FIXED_LOG.search(line) and not HISTORICAL.search(line) and (
            path.suffix != ".md" or in_code or "`" in line
        ):
            problems.append(f"{path}:{number}: fixed /tmp Quizzler log path")
        if path.suffix == ".md" and not in_code and not COPYABLE_INLINE.match(line) and "`xcodebuild " not in line:
            continue
        if DIRECT_SHELL.search(line) or DIRECT_PYTHON.search(line):
            problems.append(f"{path}:{number}: direct xcodebuild invocation")
        if DERIVED_FLAG.search(line):
            problems.append(f"{path}:{number}: caller -derivedDataPath")
    return problems


def main() -> int:
    problems = [problem for path in active_paths(ROOT) for problem in violations(path)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("build paths: owned calls use xcb and its DerivedData")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
