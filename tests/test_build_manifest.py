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
        # These tests exercise manifest STRUCTURE (course discovery, sorting,
        # notes/coupling warnings), not pack quality — lint=False keeps the
        # incidental Layer-A output from polluting the captured streams. The
        # lint pass has its own coverage in LintGateTests below.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bm.build(lint=False)
        manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        return rc, manifest, out.getvalue(), err.getvalue()


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
            rc = bm.build(lint=False)
        self.assertEqual(rc, 0)
        self.assertIn("skipping", err.getvalue())
        self.assertIn("bad_root.json", err.getvalue())
        manifest = json.loads(self.manifest_path.read_text())
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertEqual(files, ["good.json"])

    def test_null_root_pack_is_skipped_not_crashed(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "null_root.json").write_text("null")
        write_pack(course, "good.json")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bm.build(lint=False)
        self.assertEqual(rc, 0)
        self.assertIn("skipping", err.getvalue())
        manifest = json.loads(self.manifest_path.read_text())
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
            rc = bm.build(lint=False)  # must not raise AttributeError/TypeError
        self.assertEqual(rc, 0)
        manifest = json.loads(self.manifest_path.read_text())
        files = [m["file"] for m in manifest["courses"][0]["modules"]]
        self.assertIn("good.json", files)


class LintGateTests(_Base):
    """Quiet-startup behavior of the Layer-A pass: criticals surface one line per
    pack, warnings are summarized only, full detail goes to LINT_LOG, and
    --verbose enumerates inline. LINT_LOG is patched to a temp file."""

    # A lint-clean MC question: numeric distractors carry no tokens, so L10 has
    # nothing to assess; explanation/topic/difficulty present satisfy L12.
    CLEAN_Q = {
        "id": "q1", "type": "multiple_choice", "topic": "math",
        "difficulty": "easy", "prompt": "What is 2+2?",
        "options": ["4", "5", "6", "7"], "answer": 0,
        "explanation": "Two plus two is four.",
    }

    def setUp(self):
        super().setUp()
        self.lint_log = self.tmp_path / "lint.log"
        self._log_patch = patch.object(bm, "LINT_LOG", self.lint_log)
        self._log_patch.start()

    def tearDown(self):
        self._log_patch.stop()
        super().tearDown()

    def _build(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = bm.build(**kw)
        return rc, out.getvalue(), err.getvalue()

    def _course_with(self, *questions):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json", questions=[dict(q) for q in questions])

    def test_clean_pack_is_silent(self):
        self._course_with(self.CLEAN_Q)
        rc, out, err = self._build(lint=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("lint:", err)
        self.assertNotIn("lint:", out)  # no "(lint: ...)" suffix when clean
        self.assertFalse(self.lint_log.exists())

    def test_critical_prints_one_line_and_logs_detail(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        self._course_with(dirty)
        # Lenient mode isolates the quiet-display behavior from the strict abort.
        rc, out, err = self._build(lint=True, verbose=False, strict=False)
        self.assertEqual(rc, 0)  # lenient mode still writes the manifest
        self.assertIn("lint:", err)
        self.assertIn("mod1.json", err)
        self.assertIn("1 critical", err)
        self.assertNotIn("L12", err)  # detail NOT enumerated to stderr in quiet mode
        self.assertTrue(self.lint_log.exists())
        self.assertIn("L12", self.lint_log.read_text())  # detail lives in the log
        self.assertIn("see", out)  # summary points at the log

    def test_warning_only_is_not_enumerated_at_startup(self):
        warn_q = dict(self.CLEAN_Q)
        warn_q.pop("topic")
        warn_q.pop("difficulty")  # two L12 warnings, no critical
        self._course_with(warn_q)
        rc, out, err = self._build(lint=True, verbose=False)
        self.assertEqual(rc, 0)
        self.assertNotIn("lint:", err)  # warnings never surface per-pack at launch
        self.assertIn("warning", out)   # but they are counted in the summary
        self.assertTrue(self.lint_log.exists())

    def test_verbose_enumerates_findings_inline(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        # Lenient mode isolates the verbose-display behavior from the strict abort.
        rc, out, err = self._build(lint=True, verbose=True, strict=False)
        self.assertEqual(rc, 0)
        self.assertIn("L12", err)  # full enumeration printed inline

    def test_strict_aborts_on_critical(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, out, err = self._build(lint=True, strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        self.assertFalse(self.manifest_path.exists())

    def test_default_is_strict_blocks_on_critical(self):
        # strict is the DEFAULT: a critical aborts the build with the manifest
        # unwritten, so a broken pack never reaches the app launch. No strict kwarg.
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, out, err = self._build(lint=True)
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        self.assertFalse(self.manifest_path.exists())

    def test_warning_only_does_not_block_even_when_strict(self):
        # "Block on any gate failing" is scoped to criticals at build time —
        # advisory warnings are summarized, never aborted on, even by default.
        warn_q = dict(self.CLEAN_Q)
        warn_q.pop("topic")
        warn_q.pop("difficulty")  # L12 warnings only, no critical
        self._course_with(warn_q)
        rc, out, err = self._build(lint=True)  # default strict
        self.assertEqual(rc, 0)
        self.assertTrue(self.manifest_path.exists())

    def test_lint_false_skips_quality_pass(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, out, err = self._build(lint=False)
        self.assertEqual(rc, 0)
        self.assertNotIn("lint:", err)
        self.assertNotIn("lint:", out)
        self.assertFalse(self.lint_log.exists())

    def test_strict_lints_pack_despite_course_id_mismatch(self):
        # FIX 1: the lint loop must discover packs by walking files on disk, not
        # by matching _course.json's `id` to the folder name. Here the folder is
        # 'sec-plus' but _course.json declares id 'sy0-701'; the old id-keyed
        # lookup yielded [] and silently skipped the pack, letting a Layer-A
        # critical slip past the now-mandatory strict gate. With on-disk
        # discovery the missing-explanation pack IS linted and strict aborts.
        course = self.packs_dir / "sec-plus"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({"id": "sy0-701"}))
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        write_pack(course, "mod1.json", questions=[dirty])
        rc, out, err = self._build(lint=True)  # default strict
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        self.assertFalse(self.manifest_path.exists())


class AtomicWriteTests(_Base):
    """E-27: manifest must be written via a temp-then-rename so a concurrent
    reader never sees a truncated file."""

    def test_manifest_valid_json_and_no_tmp_file_remains(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        # manifest is valid JSON with expected shape
        self.assertIn("courses", manifest)
        # no .tmp file left behind
        tmp = self.manifest_path.with_name(self.manifest_path.name + ".tmp")
        self.assertFalse(tmp.exists(), f".tmp file should not remain: {tmp}")


class StrictDefaultTests(unittest.TestCase):
    """FIX 2: ``QUIZZLER_LINT_STRICT`` opt-out honors common falsey spellings,
    not just the literal "0"."""

    def test_unset_defaults_to_strict(self):
        self.assertIs(bm._strict_default({}), True)

    def test_falsey_spellings_disable_strict(self):
        for v in ("0", "false", "no", "off", ""):
            self.assertIs(
                bm._strict_default({"QUIZZLER_LINT_STRICT": v}), False, msg=repr(v))

    def test_falsey_spellings_are_case_and_whitespace_insensitive(self):
        for v in ("FALSE", " off ", "Off"):
            self.assertIs(
                bm._strict_default({"QUIZZLER_LINT_STRICT": v}), False, msg=repr(v))

    def test_truthy_values_keep_strict(self):
        for v in ("1", "yes"):
            self.assertIs(
                bm._strict_default({"QUIZZLER_LINT_STRICT": v}), True, msg=repr(v))


if __name__ == "__main__":
    unittest.main()
