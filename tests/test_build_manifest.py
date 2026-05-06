"""Unit tests for ``scripts/build_manifest.py``.

Exercises the manifest builder against throw-away fixture trees so the real
``question-packs/`` directory is never touched. The script is imported by path
(``scripts/`` isn't a package), then ``PACKS_DIR``/``MANIFEST`` are patched.

Run from the project root (independent of the Playwright suite)::

    python3 -m unittest tests.test_build_manifest -v

Playwright suite still runs via ``npx playwright test``.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_manifest.py"


_spec = importlib.util.spec_from_file_location("build_manifest", SCRIPT_PATH)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)


def write_pack(course_dir: Path, name: str, **payload) -> Path:
    payload.setdefault("title", name.replace(".json", ""))
    payload.setdefault("notes", "")
    payload.setdefault("questions", [{"q": "x"}])
    p = course_dir / name
    p.write_text(json.dumps(payload))
    return p


class _Base(unittest.TestCase):
    """Provides a temp dir with PACKS_DIR/MANIFEST patched on the module."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.packs_dir = self.tmp_path / "question-packs"
        self.packs_dir.mkdir()
        self.manifest_path = self.packs_dir / "manifest.json"
        self._patches = [
            patch.object(bm, "PACKS_DIR", self.packs_dir),
            patch.object(bm, "MANIFEST", self.manifest_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def run_build(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bm.build()
        manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        return rc, manifest, out.getvalue(), err.getvalue()


class CourseMetaTests(_Base):
    def test_derives_metadata_when_course_json_missing(self):
        course = self.packs_dir / "itn213"
        course.mkdir()
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        c = manifest["courses"][0]
        self.assertEqual(c["id"], "itn213")
        self.assertEqual(c["name"], "ITN213")  # folder name uppercased
        self.assertEqual(c["description"], "")
        self.assertNotIn("sort_order", c)  # stripped from output

    def test_uses_course_json_when_present(self):
        course = self.packs_dir / "itn213"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({
            "id": "itn-213", "name": "Networking II",
            "description": "Routing.", "sort_order": 5}))
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        c = manifest["courses"][0]
        self.assertEqual((c["id"], c["name"], c["description"]),
                         ("itn-213", "Networking II", "Routing."))
        self.assertNotIn("sort_order", c)

    def test_malformed_course_json_falls_back_to_defaults(self):
        course = self.packs_dir / "broken"
        course.mkdir()
        (course / "_course.json").write_text("{ this is not json")
        write_pack(course, "mod1.json")
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertIn("invalid JSON", err)
        c = manifest["courses"][0]
        self.assertEqual((c["id"], c["name"], c["description"]),
                         ("broken", "BROKEN", ""))


class PackBehaviorTests(_Base):
    def test_malformed_pack_json_skipped_with_warning(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "bad.json").write_text("{ not json")
        write_pack(course, "good.json")
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertIn("skipping", err)
        self.assertIn("bad.json", err)
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["good.json"])

    def test_natural_sort_filename_ordering(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        for name in ["mod10.json", "mod2.json", "mod1.json", "mod11.json", "mod9.json"]:
            write_pack(course, name)
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["mod1.json", "mod2.json", "mod9.json",
                                 "mod10.json", "mod11.json"])

    def test_notes_length_warning_threshold(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        # Boundary: exactly MAX_NOTES_LENGTH should NOT warn (strict >).
        write_pack(course, "ok.json", notes="x" * bm.MAX_NOTES_LENGTH)
        write_pack(course, "long.json", notes="x" * (bm.MAX_NOTES_LENGTH + 1))
        rc, _, _, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertIn("long.json", err)
        self.assertNotIn("ok.json", err)
        self.assertIn(str(bm.MAX_NOTES_LENGTH + 1), err)

    def test_sequential_coupling_phrase_warns(self):
        """Quizzler randomizes question order, so prompts that reference earlier
        questions break for the user. The build script should warn on common
        sequential-coupling phrases (see AUTHORING.md quality rule #11)."""
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "ok.json", questions=[
            {"id": "q1", "prompt": "A bank wants real-time fraud detection. Which service?"},
        ])
        write_pack(course, "bad.json", questions=[
            {"id": "q1", "prompt": "A bank wants real-time fraud detection."},
            {"id": "q2", "prompt": "Same fraud-detection scenario: which database?"},
            {"id": "q3", "prompt": "In the previous question, what was the answer?"},
            {"id": "q4", "prompt": "As discussed earlier, which tier applies?"},
        ])
        rc, _, _, err = self.run_build()
        self.assertEqual(rc, 0)
        # Each red-flag phrase should produce one warning, all referencing bad.json.
        self.assertIn("bad.json q2", err)
        self.assertIn("bad.json q3", err)
        self.assertIn("bad.json q4", err)
        self.assertIn("sequential-coupling", err)
        self.assertNotIn("ok.json", err)

    def test_course_json_not_treated_as_pack(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({"name": "Course One"}))
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["mod1.json"])

    def test_question_count_and_title_defaults(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json", title="Module One", notes="hi",
                   questions=[{"q": 1}, {"q": 2}, {"q": 3}])
        # Pack with no title field falls back to file stem.
        (course / "mod2.json").write_text(json.dumps({"questions": []}))
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        m = {x["file"]: x for x in manifest["courses"][0]["modules"]}
        self.assertEqual(
            (m["mod1.json"]["title"], m["mod1.json"]["description"], m["mod1.json"]["questionCount"]),
            ("Module One", "hi", 3))
        self.assertEqual((m["mod2.json"]["title"], m["mod2.json"]["questionCount"]),
                         ("mod2", 0))


class FolderFilteringTests(_Base):
    def test_dot_and_underscore_folders_are_skipped(self):
        for name in [".hidden", "_archive", "_DS_skip", "visible"]:
            d = self.packs_dir / name
            d.mkdir()
            write_pack(d, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual([c["id"] for c in manifest["courses"]], ["visible"])

    def test_course_with_no_valid_packs_is_skipped(self):
        (self.packs_dir / "empty").mkdir()
        only_bad = self.packs_dir / "onlybad"
        only_bad.mkdir()
        (only_bad / "broken.json").write_text("{ not json")
        good = self.packs_dir / "good"
        good.mkdir()
        write_pack(good, "mod1.json")
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual([c["id"] for c in manifest["courses"]], ["good"])
        self.assertIn("empty", err)
        self.assertIn("onlybad", err)

    def test_empty_packs_dir_writes_empty_manifest(self):
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["courses"], [])
        self.assertIn("generated_at", manifest)

    def test_missing_packs_dir_returns_error(self):
        bogus = self.tmp_path / "does-not-exist"
        with patch.object(bm, "PACKS_DIR", bogus), \
             patch.object(bm, "MANIFEST", bogus / "manifest.json"):
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = bm.build()
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", err.getvalue())

    def test_non_directory_entries_ignored(self):
        # Stray top-level files (README, manifest.json itself) must not crash.
        (self.packs_dir / "README.md").write_text("hi")
        (self.packs_dir / "stray.json").write_text(json.dumps({"questions": []}))
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual([c["id"] for c in manifest["courses"]], ["c1"])


class SortOrderingTests(_Base):
    def _make_course(self, folder, course_meta=None):
        d = self.packs_dir / folder
        d.mkdir()
        if course_meta is not None:
            (d / "_course.json").write_text(json.dumps(course_meta))
        write_pack(d, "mod1.json")

    def test_mixed_sort_order_orders_courses_correctly(self):
        # Mix of explicit sort_order and default (100). 100-ties break by name.lower().
        self._make_course("alpha", {"name": "Alpha", "sort_order": 50})
        self._make_course("bravo")  # default 100, name -> "BRAVO"
        self._make_course("charlie", {"name": "Charlie", "sort_order": 10})
        self._make_course("delta", {"name": "Aardvark"})  # default 100
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        # charlie(10), alpha(50), then "Aardvark" < "BRAVO" at 100.
        self.assertEqual([c["name"] for c in manifest["courses"]],
                         ["Charlie", "Alpha", "Aardvark", "BRAVO"])


if __name__ == "__main__":
    unittest.main()
