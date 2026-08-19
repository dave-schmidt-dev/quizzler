#!/usr/bin/env python3
"""Static Release-source isolation checks (no Xcode or signing required)."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "project.yml"


def release_source_inventory(project: Path, source_root: Path) -> tuple[list[str], list[str]]:
    """Resolve the Release source set and exclusions like XcodeGen does.

    This deliberately walks real source files in a temporary project fixture;
    a matching YAML token with no source file cannot satisfy this check.
    """
    spec = yaml.safe_load(project.read_text(encoding="utf-8"))
    target = spec["targets"]["QuizzleriOS"]
    settings = target.get("settings", {})
    patterns: list[str] = []
    for block in (settings.get("base", {}), settings.get("configs", {}).get("Release", {})):
        value = block.get("EXCLUDED_SOURCE_FILE_NAMES", "")
        patterns.extend(str(value).split())
    candidates: list[str] = []
    for source in target.get("sources", []):
        base = source_root / source
        if base.is_dir():
            candidates.extend(str(p.relative_to(source_root)) for p in base.rglob("*") if p.is_file())
    included = [p for p in candidates if not any(fnmatch.fnmatch(Path(p).name, pattern) for pattern in patterns)]
    excluded = [p for p in candidates if p not in included]
    return sorted(included), sorted(excluded)


class FixtureIsolationTests(unittest.TestCase):
    def test_actual_failure_injection_source_is_excluded_from_release(self):
        included, excluded = release_source_inventory(PROJECT, ROOT)
        self.assertNotIn("QuizzleriOS/FailureInjection.swift", included)
        self.assertIn("QuizzleriOS/FailureInjection.swift", excluded)

    def test_seeded_preview_questions_are_excluded_from_release(self):
        """The demo questions must not be compilable into a shipped app.

        They were once the entire course a tester saw (walkthrough finding 1).
        Keeping them in a `*Fixture*` file makes the existing Release exclusion
        do the work; this asserts the file still has a name that triggers it.
        """
        included, excluded = release_source_inventory(PROJECT, ROOT)
        self.assertNotIn("QuizzleriOS/TestingSupport/StudyPreviewFixture.swift", included)
        self.assertIn("QuizzleriOS/TestingSupport/StudyPreviewFixture.swift", excluded)

    def test_release_declares_fixture_exclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp) / "project"
            source_root.mkdir()
            (source_root / "QuizzleriOS").mkdir()
            for name in ("Real.swift", "UITestFixture.swift", "FailureInjection.swift", "TestOnly.swift"):
                (source_root / "QuizzleriOS" / name).write_text(name, encoding="utf-8")
            included, excluded = release_source_inventory(PROJECT, source_root)
        self.assertIn("QuizzleriOS/Real.swift", included)
        self.assertNotIn("QuizzleriOS/UITestFixture.swift", included)
        self.assertNotIn("QuizzleriOS/FailureInjection.swift", included)
        self.assertNotIn("QuizzleriOS/TestOnly.swift", included)
        self.assertEqual(len(excluded), 3)

    def test_release_fixture_names_are_explicitly_forbidden(self):
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp) / "project"
            source_root.mkdir()
            (source_root / "QuizzleriOS").mkdir()
            (source_root / "QuizzleriOS" / "Fixture.swift").write_text("fixture", encoding="utf-8")
            (source_root / "QuizzleriOS" / "FailureInjection.swift").write_text("failure", encoding="utf-8")
            included, excluded = release_source_inventory(PROJECT, source_root)
        self.assertEqual(included, [])
        self.assertEqual(set(excluded), {"QuizzleriOS/Fixture.swift", "QuizzleriOS/FailureInjection.swift"})

if __name__ == "__main__":
    unittest.main()
