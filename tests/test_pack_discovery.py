"""Pin the single installable-pack rule in ``scripts/pack_discovery.py``.

Every gate (manifest build, pack lint, native bundler, both git hooks) reads
this rule, so the string form the hooks use and the filesystem walk the Python
scripts use must never disagree.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "pack_discovery.py"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import pack_discovery as pd  # noqa: E402

PATH_CASES = [
    ("question-packs/cissp/round-1.json", True),
    ("question-packs/samples/sample-pack.json", True),
    # In-course manifest files are packs: the native bundler ships them.
    ("question-packs/cissp/manifest.json", True),
    ("question-packs/cissp/manifest.example.json", True),
    # Underscore/dot prefixes mark metadata, archive and OS files.
    ("question-packs/cissp/_course.json", False),
    ("question-packs/cissp/_scratch.json", False),
    ("question-packs/cissp/.hidden.json", False),
    ("question-packs/_archive/old.json", False),
    ("question-packs/.hidden/old.json", False),
    # Only `question-packs/<course>/<file>.json` has the shape of a pack.
    ("question-packs/manifest.json", False),
    ("question-packs/manifest.example.json", False),
    ("question-packs/cissp/sub/deep.json", False),
    ("question-packs/cissp/BUILD_NOTES.md", False),
    ("question-packs/cissp/round-1.json.bak", False),
    ("other/cissp/round-1.json", False),
    ("round-1.json", False),
]


class PathRuleTests(unittest.TestCase):
    def test_path_rule_cases(self) -> None:
        for relpath, expected in PATH_CASES:
            with self.subTest(path=relpath):
                self.assertIs(pd.is_installable_pack_path(relpath), expected)

    def test_stdin_filter_prints_only_installable_paths(self) -> None:
        stdin = "".join(f"{relpath}\n" for relpath, _ in PATH_CASES)
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.splitlines(), [p for p, ok in PATH_CASES if ok])


class FilesystemWalkTests(unittest.TestCase):
    def test_walk_and_path_rule_agree_on_every_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relpath, _ in PATH_CASES:
                target = root / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")
            walked = {p.relative_to(root).as_posix() for p in pd.iter_installable_packs(root / pd.PACKS_DIRNAME)}
        self.assertEqual(walked, {p for p, ok in PATH_CASES if ok})

    def test_hidden_and_underscore_courses_are_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("cissp", "_archive", ".hidden"):
                (root / name).mkdir()
            (root / "stray.json").write_text("{}", encoding="utf-8")
            self.assertEqual([p.name for p in pd.iter_courses(root)], ["cissp"])

    def test_missing_directories_yield_nothing(self) -> None:
        missing = PROJECT_ROOT / "no-such-packs-root"
        self.assertEqual(list(pd.iter_installable_packs(missing)), [])
        self.assertEqual(list(pd.iter_course_packs(missing)), [])

    def test_directory_named_like_a_pack_is_not_a_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            course = Path(tmp) / "cissp"
            (course / "looks-like-a-pack.json").mkdir(parents=True)
            self.assertEqual(list(pd.iter_course_packs(course)), [])


if __name__ == "__main__":
    unittest.main()
