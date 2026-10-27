"""Unit tests for ``scripts/build_manifest.py``: course and pack discovery.

Run from the project root::

    python3 -m unittest tests.test_build_manifest -v

"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests.build_manifest_fixtures import _Base, bm, write_pack


class CourseMetaTests(_Base):
    def test_derives_metadata_when_course_json_missing(self):
        course = self.packs_dir / "democourse"
        course.mkdir()
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        c = manifest["courses"][0]
        self.assertEqual(c["id"], "democourse")
        self.assertEqual(c["name"], "DEMOCOURSE")  # folder name uppercased
        self.assertEqual(c["description"], "")
        self.assertNotIn("sort_order", c)  # stripped from output

    def test_uses_course_json_when_present(self):
        course = self.packs_dir / "democourse"
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

    def test_malformed_course_json_refuses_the_course(self):
        course = self.packs_dir / "broken"
        course.mkdir()
        (course / "_course.json").write_text("{ this is not json")
        write_pack(course, "mod1.json")
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["courses"], [])
        self.assertIn("refusing to install", err)

    def test_non_dict_course_json_refuses_the_course_without_raising(self):
        course = self.packs_dir / "broken"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps([1, 2, 3]))
        write_pack(course, "mod1.json")
        self.assertIsNone(bm.read_course_meta(course))
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["courses"], [])
        self.assertIn("must contain a JSON object", err)


class CourseSizeGuardTests(_Base):
    """Course-level workload limits prevent oversized exam banks."""

    def _write_question_count(self, count: int):
        course = self.packs_dir / "oversized"
        course.mkdir()
        write_pack(course, "mod1.json", questions=[{"q": index} for index in range(count)])

    def test_hard_ceiling_blocks_manifest_even_when_lint_is_skipped(self):
        self._write_question_count(bm.COURSE_QUESTION_HARD_MAX + 1)
        rc, manifest, out, err = self.run_build()
        self.assertEqual(rc, 1)
        self.assertEqual(manifest, {})
        self.assertIn("hard ceiling", err)
        self.assertIn(str(bm.COURSE_QUESTION_HARD_MAX), err)

    def test_hard_ceiling_cannot_be_bypassed_by_no_strict(self):
        self._write_question_count(bm.COURSE_QUESTION_HARD_MAX + 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(strict=False, lint=False)
        self.assertEqual(rc, 1)
        # The course-size ceiling aborts before the manifest stage entirely, so
        # unlike a pack-level gate failure there is no manifest at all.
        self.assertEqual(manifest, {})
        self.assertIn("explicit preview override", err.getvalue())

    def test_explicit_preview_override_is_the_only_oversize_escape_hatch(self):
        self._write_question_count(bm.COURSE_QUESTION_HARD_MAX + 1)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(
                strict=False,
                lint=False,
                allow_course_size_preview=True,
            )
        self.assertEqual(rc, 0)
        self.assertTrue(manifest)
        self.assertIn("hard ceiling", err.getvalue())

    def test_soft_threshold_warns_but_allows_manifest(self):
        self._write_question_count(bm.COURSE_QUESTION_SOFT_MAX + 1)
        rc, manifest, out, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertTrue(manifest)
        self.assertIn("advisory planning threshold", err)

    def test_declared_target_warns_without_leaking_runtime_metadata(self):
        course = self.packs_dir / "targeted"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({
            "id": "targeted",
            "name": "Targeted",
            "question_budget": {"target": 2},
        }))
        write_pack(course, "mod1.json", questions=[{"q": 1}, {"q": 2}, {"q": 3}])
        rc, manifest, _, err = self.run_build()
        self.assertEqual(rc, 0)
        self.assertIn("declared planning target of 2", err)
        self.assertNotIn("question_budget", json.dumps(manifest))


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

    def test_null_questions_count_as_zero(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json", questions=None)
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["courses"][0]["modules"][0]["questionCount"], 0)


class FolderFilteringTests(_Base):
    def test_dot_and_underscore_folders_are_skipped(self):
        for name in [".hidden", "_archive", "_DS_skip", "visible"]:
            d = self.packs_dir / name
            d.mkdir()
            write_pack(d, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual([c["id"] for c in manifest["courses"]], ["visible"])

    def test_underscore_and_dot_files_inside_a_course_are_not_packs(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        for name in ["mod1.json", "_scratch.json", ".hidden.json"]:
            write_pack(course, name)
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual([m["file"] for m in manifest["courses"][0]["modules"]], ["mod1.json"])

    def test_manifest_json_inside_a_course_is_a_pack(self):
        # pack_discovery's rule: only `_`/`.` prefixes are exempt. The native
        # bundler ships a course-level manifest.json, so the gate must see it.
        course = self.packs_dir / "c1"
        course.mkdir()
        for name in ["mod1.json", "manifest.json"]:
            write_pack(course, name)
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual(
            sorted(m["file"] for m in manifest["courses"][0]["modules"]),
            ["manifest.json", "mod1.json"],
        )

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

    def test_empty_packs_dir_returns_empty_manifest(self):
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["courses"], [])
        self.assertIn("generated_at", manifest)

    def test_missing_packs_dir_returns_error(self):
        bogus = self.tmp_path / "does-not-exist"
        with patch.object(bm, "PACKS_DIR", bogus):
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc, manifest = bm.build()
        self.assertEqual(rc, 1)
        self.assertEqual(manifest, {})
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


class MalformedPackStructureTests(_Base):
    """CR-1: a pack whose root is valid JSON but not a dict must be skipped,
    not crash the build. read_pack_meta returns None and the build continues."""

    def test_non_dict_root_pack_is_skipped_not_crashed(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        # A JSON array is valid JSON but not an object — this is the CR-1 crash.
        (course / "bad_root.json").write_text("[]")
        write_pack(course, "good.json")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(lint=False)
        self.assertEqual(rc, 0)
        self.assertIn("skipping", err.getvalue())
        self.assertIn("bad_root.json", err.getvalue())
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["good.json"])

    def test_null_root_pack_is_skipped_not_crashed(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "null_root.json").write_text("null")
        write_pack(course, "good.json")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(lint=False)
        self.assertEqual(rc, 0)
        self.assertIn("skipping", err.getvalue())
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["good.json"])

    def test_non_dict_question_and_non_list_questions_do_not_crash_prelint(self):
        # CR-1 residual: read_pack_meta runs BEFORE the lint pass and iterates
        # `questions`, calling q.get("prompt"). A valid dict root with a non-dict
        # question entry, or a non-list `questions`, must not crash build().
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "bad_q.json").write_text('{"questions": [123]}')
        (course / "bad_qs.json").write_text('{"questions": "notalist"}')
        write_pack(course, "good.json")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(lint=False)  # must not raise AttributeError/TypeError
        self.assertEqual(rc, 0)
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertIn("good.json", files)


if __name__ == "__main__":
    unittest.main()
