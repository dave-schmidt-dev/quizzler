"""Manifest quality-gate tests for ``scripts/build_manifest.py``."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.build_manifest_fixtures import _Base, bm, write_pack


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
            rc, manifest = bm.build(**kw)
        return rc, manifest, out.getvalue(), err.getvalue()

    def _course_with(self, *questions, certify: bool = True):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(
            course,
            "mod1.json",
            questions=[dict(q) for q in questions],
            certify=certify,
        )

    def test_clean_pack_is_silent(self):
        self._course_with(self.CLEAN_Q)
        rc, _, out, err = self._build(lint=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("lint:", err)
        self.assertNotIn("lint:", out)  # no "(lint: ...)" suffix when clean
        self.assertFalse(self.lint_log.exists())

    def test_build_carries_parsed_pack_through_lint_gate_and_manifest(self):
        self._course_with(self.CLEAN_Q)
        pack_path = self.packs_dir / "c1" / "mod1.json"
        original_read_text = Path.read_text
        pack_reads = []

        def tracking_read_text(path, *args, **kwargs):
            if path == pack_path:
                pack_reads.append(path)
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=tracking_read_text):
            rc, manifest, _, _ = self._build(lint=True)

        self.assertEqual(rc, 0)
        self.assertEqual(pack_reads, [pack_path])
        self.assertNotIn("_pack_data", manifest["courses"][0]["modules"][0])

    def test_critical_prints_one_line_and_logs_detail(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        self._course_with(dirty)
        # Lenient mode isolates the quiet-display behavior from the strict abort.
        rc, _, out, err = self._build(lint=True, verbose=False, strict=False)
        self.assertEqual(rc, 0)  # lenient mode still installs the pack
        self.assertIn("lint:", err)
        self.assertIn("mod1.json", err)
        self.assertIn("1 critical", err)
        self.assertNotIn("L12", err)  # detail NOT enumerated to stderr in quiet mode
        self.assertTrue(self.lint_log.exists())
        self.assertIn("L12", self.lint_log.read_text())  # detail lives in the log
        self.assertIn("see", out)  # summary points at the log

    def test_warning_only_is_not_enumerated_at_startup(self):
        warn_q = dict(self.CLEAN_Q)
        warn_q.pop("difficulty")  # one L12 warning, no critical; keep topic for blueprint
        self._course_with(warn_q)
        rc, _, out, err = self._build(lint=True, verbose=False)
        self.assertEqual(rc, 0)
        self.assertNotIn("lint:", err)  # warnings never surface per-pack at launch
        self.assertIn("warning", out)   # but they are counted in the summary
        self.assertTrue(self.lint_log.exists())

    def test_lint_log_parent_dir_created_on_demand(self):
        """A missing .logs/ parent is created before the log is written."""
        nested = self.tmp_path / ".logs" / "quizzler-lint.log"
        self._log_patch.stop()
        self._log_patch = patch.object(bm, "LINT_LOG", nested)
        self._log_patch.start()
        self.assertFalse(nested.parent.exists())
        warn_q = dict(self.CLEAN_Q)
        warn_q.pop("difficulty")
        self._course_with(warn_q)
        rc, _, _, _ = self._build(lint=True, verbose=False)
        self.assertEqual(rc, 0)
        self.assertTrue(nested.exists())
        self.assertIn("L12", nested.read_text())

    def test_verbose_enumerates_findings_inline(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        # Lenient mode isolates the verbose-display behavior from the strict abort.
        rc, _, _, err = self._build(lint=True, verbose=True, strict=False)
        self.assertEqual(rc, 0)
        self.assertIn("L12", err)  # full enumeration printed inline

    def test_strict_aborts_on_critical(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, manifest, _, err = self._build(lint=True, strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        # A failed strict build yields an EMPTY manifest rather than an implicit
        # "keep whatever was installed before" — declining to revoke would keep
        # serving the packs that just failed. See StrictExclusionTests.
        self.assertEqual(manifest["courses"], [])

    def test_strict_aborts_on_uncertified_pack(self):
        """Install gate: missing/stale certification aborts strict build."""
        self._course_with(self.CLEAN_Q, certify=False)
        rc, manifest, _, err = self._build(lint=True, strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("install gate", err)
        self.assertIn("certification missing or stale", err)
        self.assertIn("strict mode", err)
        # A failed strict build yields an EMPTY manifest rather than an implicit
        # "keep whatever was installed before" — declining to revoke would keep
        # serving the packs that just failed. See StrictExclusionTests.
        self.assertEqual(manifest["courses"], [])

    def test_uncertified_pack_warns_in_no_strict(self):
        self._course_with(self.CLEAN_Q, certify=False)
        rc, manifest, _, err = self._build(lint=True, strict=False)
        self.assertEqual(rc, 0)
        self.assertIn("warn: install gate", err)
        self.assertIn("certification missing or stale", err)
        self.assertTrue(manifest)

    def test_malformed_course_metadata_still_runs_pack_install_gate(self):
        course = self.packs_dir / "broken"
        course.mkdir()
        (course / "_course.json").write_text("{ this is not json")
        write_pack(course, "mod1.json", questions=[dict(self.CLEAN_Q)], certify=False)

        rc, manifest, _, err = self._build(lint=True, strict=True)

        self.assertEqual(rc, 1)
        self.assertIn("install gate: question-packs/broken/mod1.json", err)
        self.assertIn("certification missing or stale", err)
        self.assertIn("1 install gate failure", manifest["revoked"]["reason"])

    def test_default_is_strict_blocks_on_critical(self):
        # strict is the DEFAULT: a critical aborts the build with an explicitly
        # empty manifest, so a broken pack never reaches the app launch. No strict kwarg.
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        # A failed strict build yields an EMPTY manifest rather than an implicit
        # "keep whatever was installed before" — declining to revoke would keep
        # serving the packs that just failed. See StrictExclusionTests.
        self.assertEqual(manifest["courses"], [])

    def test_warning_only_does_not_block_even_when_strict(self):
        # "Block on any gate failing" is scoped to criticals at build time —
        # advisory warnings are summarized, never aborted on, even by default.
        warn_q = dict(self.CLEAN_Q)
        warn_q.pop("difficulty")  # L12 warning only, no critical; keep topic for blueprint
        self._course_with(warn_q)
        rc, manifest, _, _ = self._build(lint=True)  # default strict
        self.assertEqual(rc, 0)
        self.assertTrue(manifest)

    def test_lint_false_skips_quality_pass(self):
        dirty = dict(self.CLEAN_Q)
        dirty.pop("explanation")
        self._course_with(dirty)
        rc, _, out, err = self._build(lint=False)
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
        write_pack(course, "mod1.json", questions=[dirty], certify=True)
        rc, manifest, _, err = self._build(lint=True)  # default strict
        self.assertEqual(rc, 1)
        self.assertIn("strict mode", err)
        # A failed strict build yields an EMPTY manifest rather than an implicit
        # "keep whatever was installed before" — declining to revoke would keep
        # serving the packs that just failed. See StrictExclusionTests.
        self.assertEqual(manifest["courses"], [])


class StrictExclusionTests(LintGateTests):
    """A strict failure must exclude the FAILING PACK, not abort the whole build.

    Two regressions are locked out here.

    1. build() returned 1 and wrote nothing, so the previous manifest.json
       stayed on disk and the app kept serving every pack it listed — including
       the ones that had just failed the gate. "Refuse to install" has to mean
       "uninstall what no longer qualifies".
    2. The fix for (1) revoked *everything*, so a pack that passed every gate
       could not install while an unrelated pack was broken. The only way to run
       during authoring was --no-strict, which reinstalls the broken pack too —
       i.e. the gate was off exactly when it mattered most.
    """

    DIRTY_Q = {
        "id": "q1", "type": "true_false", "topic": "math",
        "difficulty": "easy", "prompt": "According to the chapter, 2+2 is 4.",
        "answer": True, "explanation": "Two plus two is four.",
    }

    def _course_with(self, *questions, certify: bool = True):
        """Same as the parent, but re-writable so a test can degrade a pack in place."""
        course = self.packs_dir / "c1"
        course.mkdir(exist_ok=True)
        write_pack(course, "mod1.json",
                   questions=[dict(q) for q in questions], certify=certify)

    def _second_course(self, *questions, certify: bool = True):
        course = self.packs_dir / "c2"
        course.mkdir(exist_ok=True)
        write_pack(course, "mod1.json",
                   questions=[dict(q) for q in questions], certify=certify)

    def test_a_passing_pack_still_installs_when_a_sibling_fails(self):
        """The requirement is per pack. One bad pack must not block a good one."""
        self._course_with(self.CLEAN_Q)
        self._second_course(self.DIRTY_Q)

        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 2, "partial install is its own exit code")
        self.assertIn("c2/mod1.json", err)

        installed = {c["id"] for c in manifest["courses"]}
        self.assertEqual(installed, {"c1"},
                         "the clean course must install; the failing one must not")
        self.assertIs(manifest["strict_gate"], True,
                      "a partial install is still a gated artifact")
        self.assertEqual(manifest["revoked"]["revoked_packs"], ["c2/mod1.json"])

    def test_only_the_failing_pack_is_dropped_from_a_mixed_course(self):
        """Exclusion is per pack, so a course keeps its packs that passed."""
        course = self.packs_dir / "c1"
        course.mkdir(exist_ok=True)
        write_pack(course, "good.json",
                   questions=[dict(self.CLEAN_Q)], certify=True)
        write_pack(course, "bad.json",
                   questions=[dict(self.DIRTY_Q)], certify=True)

        rc, manifest, _, _ = self._build(lint=True)
        self.assertEqual(rc, 2)

        files = [m["file"] for c in manifest["courses"] for m in c["modules"]]
        self.assertEqual(files, ["good.json"])

    def test_strict_failure_revokes_a_previously_installed_manifest(self):
        # 1. A clean pack installs normally.
        self._course_with(self.CLEAN_Q)
        rc, installed, _, _ = self._build(lint=True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(installed["courses"]), 1)

        # 2. The pack degrades below the bar. Nothing is left to install, so the
        #    previously-installed course must be gone from the manifest.
        self._course_with(self.DIRTY_Q)
        rc, revoked, _, err = self._build(lint=True)
        self.assertEqual(rc, 1, "nothing installed is a hard failure, not a partial")
        self.assertIn("EXCLUDED", err)

        self.assertEqual(revoked["courses"], [],
                         "the previously-installed course must not survive")
        self.assertIn("c1/mod1.json", revoked["revoked"]["revoked_packs"])

    def test_empty_manifest_is_returned_when_nothing_was_installed(self):
        """An explicit empty manifest beats none: the app can explain it."""
        self._course_with(self.DIRTY_Q)
        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 1)
        self.assertIn("EXCLUDED", err)
        self.assertEqual(manifest["courses"], [])
        self.assertIs(manifest["strict_gate"], True)

    def test_no_strict_build_installs_everything_and_flags_nothing(self):
        self._course_with(self.CLEAN_Q)
        self._build(lint=True)
        self._course_with(self.DIRTY_Q)
        rc, manifest, _, _ = self._build(lint=True, strict=False)
        self.assertEqual(rc, 0)
        self.assertNotIn("revoked", manifest)
        self.assertEqual(len(manifest["courses"]), 1)

    def test_exclusion_survives_a_course_id_folder_mismatch(self):
        """Exclusion is keyed by folder, but the manifest is keyed by declared id.

        The lint pass walks folders on disk while `courses` carries _course.json's
        `id`. Matching the two by id silently failed to drop the failing pack
        whenever they differed — the same mismatch that once let a whole course
        skip linting. Here the good and bad packs are in DIFFERENT folders whose
        declared ids are swapped, so an id-keyed prune would drop the wrong one.
        """
        # These fixtures declare a _course.json, so L27 applies: the taxonomy
        # must be present and cited, and each question must name a declared
        # area. Without it the clean pack would fail lint too and the test would
        # stop isolating the folder-vs-id mismatch it exists to check.
        syllabus = {
            "source": {"kind": "none", "title": "fixture"},
            "areas": [{"id": "a1", "name": "Area One"}],
        }
        clean_q = dict(self.CLEAN_Q, exam_area="a1")
        dirty_q = dict(self.DIRTY_Q, exam_area="a1")

        good = self.packs_dir / "folder-a"
        good.mkdir()
        (good / "_course.json").write_text(
            json.dumps({"id": "folder-b", "syllabus": syllabus}))
        write_pack(good, "mod1.json", questions=[clean_q], certify=True)

        bad = self.packs_dir / "folder-b"
        bad.mkdir()
        (bad / "_course.json").write_text(
            json.dumps({"id": "folder-a", "syllabus": syllabus}))
        write_pack(bad, "mod1.json", questions=[dirty_q], certify=True)

        rc, manifest, _, _ = self._build(lint=True)
        self.assertEqual(rc, 2)
        self.assertEqual([c["id"] for c in manifest["courses"]], ["folder-b"],
                         "the surviving course is the one in folder-a, whose "
                         "declared id is 'folder-b'")
        self.assertEqual(manifest["revoked"]["revoked_packs"], ["folder-b/mod1.json"])

    def test_internal_dir_name_key_does_not_leak_into_the_manifest(self):
        self._course_with(self.CLEAN_Q)
        _, manifest, _, _ = self._build(lint=True)
        for course in manifest["courses"]:
            self.assertNotIn("_dir_name", course)

    def test_manifest_records_which_gate_produced_it(self):
        """`strict_gate` distinguishes a gated install from a --no-strict override."""
        self._course_with(self.CLEAN_Q)
        _, manifest, _, _ = self._build(lint=True)
        self.assertIs(manifest["strict_gate"], True)

    def test_exam_area_taxonomy_reaches_the_manifest(self):
        """Questions reference areas by id, so the app needs the id->name map.

        `_question_budget` is authoring-only and popped before the manifest is
        returned; the syllabus is the opposite and must survive, or per-area
        reporting has nothing to resolve an `exam_area` against.
        """
        syllabus = {
            "source": {"kind": "none", "title": "fixture"},
            "areas": [{"id": "a1", "name": "Area One"}],
        }
        course = self.packs_dir / "c1"
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({
            "id": "c1", "syllabus": syllabus, "question_budget": {"target": 10},
        }))
        write_pack(course, "mod1.json",
                   questions=[dict(self.CLEAN_Q, exam_area="a1")], certify=True)
        _, manifest, _, _ = self._build(lint=True)
        entry = manifest["courses"][0]
        self.assertEqual(entry["syllabus"], syllabus)
        self.assertNotIn("_question_budget", entry)

        self._course_with(self.DIRTY_Q)
        _, manifest, _, _ = self._build(lint=True, strict=False)
        self.assertIs(manifest["strict_gate"], False)

        # lint=False skips the gate entirely, so it is not a gated build either.
        _, manifest, _, _ = self._build(lint=False)
        self.assertIs(manifest["strict_gate"], False)


if __name__ == "__main__":
    unittest.main()
