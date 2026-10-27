"""Manifest distribution and repository-build tests for ``build_manifest.py``."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.build_manifest_fixtures import (
    PROJECT_ROOT,
    _Base,
    bm,
    build_repository_copy,
    without_generated_at,
    write_pack,
)


class AreaDistributionTests(_Base):
    """Course-level weighted-area checks use only packs that survive pruning."""

    CLEAN_Q = {
        "id": "q1", "type": "multiple_choice", "topic": "math",
        "difficulty": "easy", "prompt": "What is 2+2?",
        "options": ["4", "5", "6", "7"], "answer": 0,
        "explanation": "Two plus two is four.",
    }

    SYLLABUS = {
        "source": {"kind": "syllabus", "title": "Fixture syllabus"},
        "areas": [
            {"id": "a1", "name": "Area One", "weight": 50},
            {"id": "a2", "name": "Area Two", "weight": 50},
        ],
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

    def _write_course(self, name: str, packs: dict[str, list[dict]]) -> Path:
        course = self.packs_dir / name
        course.mkdir()
        (course / "_course.json").write_text(json.dumps({
            "id": name, "syllabus": self.SYLLABUS,
        }))
        for filename, questions in packs.items():
            write_pack(course, filename, questions=questions, certify=True)
        return course

    def _questions(self, count: int, *, split: bool = True, dirty: bool = False):
        questions = []
        for index in range(count):
            question = dict(self.CLEAN_Q)
            question.update({
                "id": f"q{index}",
                "prompt": f"Which value is correct for fixture item {index}?",
                "exam_area": (
                    "a1"
                    if (index < count // 2 if split else index < count - 1)
                    else "a2"
                ),
            })
            if dirty:
                question.pop("explanation")
            questions.append(question)
        return questions

    def test_balanced_course_distribution_passes(self):
        self._write_course("balanced", {"mod1.json": self._questions(20)})
        rc, _, _, err = self._build(lint=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("L27-DISTRIBUTION", err)

    def test_skewed_course_distribution_is_critical_and_excluded(self):
        self._write_course(
            "skewed", {"mod1.json": self._questions(20, split=False)}
        )
        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 1)
        self.assertIn("critical L27-DISTRIBUTION", err)
        self.assertEqual(manifest["courses"], [])

    def test_below_floor_has_no_distribution_finding(self):
        self._write_course(
            "small", {"mod1.json": self._questions(19, split=False)}
        )
        rc, _, _, err = self._build(lint=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("L27-DISTRIBUTION", err)

    def test_distribution_failure_excludes_course_but_installs_survivor(self):
        self._write_course("good", {"mod1.json": self._questions(20)})
        self._write_course(
            "bad", {"mod1.json": self._questions(20, split=False)}
        )
        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 2)
        self.assertIn("bad", err)
        self.assertEqual([course["id"] for course in manifest["courses"]], ["good"])

    def test_pruned_pack_does_not_distort_course_distribution(self):
        self._write_course("pruned", {
            "good.json": self._questions(20),
            "bad.json": self._questions(20, split=False, dirty=True),
        })
        rc, manifest, _, err = self._build(lint=True)
        self.assertEqual(rc, 2)
        self.assertNotIn("L27-DISTRIBUTION", err)
        self.assertEqual(
            [module["file"] for module in manifest["courses"][0]["modules"]],
            ["good.json"],
        )


class CourseBlueprintDistributionTests(_Base):
    """course_blueprint_distribution_findings: the course-level counterpart of
    L27 finding 14 (L27-BLUEPRINT-DISTRIBUTION). Every pack is linted with
    include_distribution=False, so this is the ONLY place on the install path
    that evaluates a course's declared coverage_blueprint minimums against
    published syllabus weights — without it, a skewed blueprint installs
    cleanly as long as no single pack crosses the per-pack floor alone.
    """

    SYLLABUS = {
        "source": {"kind": "syllabus", "title": "Fixture syllabus"},
        "areas": [
            {"id": "a1", "name": "Area One", "weight": 50},
            {"id": "a2", "name": "Area Two", "weight": 50},
        ],
    }

    def _course(self, dir_name: str, modules: list[str], syllabus=None) -> dict:
        return {
            "id": dir_name,
            "_dir_name": dir_name,
            "syllabus": self.SYLLABUS if syllabus is None else syllabus,
            "modules": [{"file": f} for f in modules],
        }

    def _write_pack_file(self, dir_name: str, filename: str, blueprint) -> None:
        course_dir = self.packs_dir / dir_name
        course_dir.mkdir(exist_ok=True)
        (course_dir / filename).write_text(
            json.dumps({"coverage_blueprint": blueprint})
        )

    @staticmethod
    def _blueprint(area_a1_count: int, area_a2_count: int) -> list[dict]:
        entries = [
            {"topic": f"a1-t{i}", "area": "a1", "min": 1}
            for i in range(area_a1_count)
        ]
        entries += [
            {"topic": f"a2-t{i}", "area": "a2", "min": 1}
            for i in range(area_a2_count)
        ]
        return entries

    def test_balanced_blueprint_has_no_course_finding(self):
        self._write_pack_file("balanced", "mod1.json", self._blueprint(10, 10))
        course = self._course("balanced", ["mod1.json"])
        self.assertEqual(bm.course_blueprint_distribution_findings(course), [])

    def test_skewed_blueprint_is_critical(self):
        self._write_pack_file("skewed", "mod1.json", self._blueprint(19, 1))
        course = self._course("skewed", ["mod1.json"])
        findings = bm.course_blueprint_distribution_findings(course)
        self.assertTrue(findings)
        self.assertTrue(
            all("L27-BLUEPRINT-DISTRIBUTION" in detail for _, detail in findings)
        )

    def test_below_floor_blueprint_is_exempt(self):
        self._write_pack_file("small", "mod1.json", self._blueprint(9, 1))
        course = self._course("small", ["mod1.json"])
        self.assertEqual(bm.course_blueprint_distribution_findings(course), [])

    def test_blueprint_aggregates_across_multiple_pack_files_in_course(self):
        # Neither file alone crosses the floor; the aggregate does, and is skewed.
        self._write_pack_file("multi", "mod1.json", self._blueprint(9, 1))
        self._write_pack_file("multi", "mod2.json", self._blueprint(10, 0))
        course = self._course("multi", ["mod1.json", "mod2.json"])
        findings = bm.course_blueprint_distribution_findings(course)
        self.assertTrue(findings)

    def test_unweighted_syllabus_returns_no_findings(self):
        self._write_pack_file("none", "mod1.json", self._blueprint(19, 1))
        course = self._course(
            "none", ["mod1.json"], syllabus={"source": {"kind": "none"}}
        )
        self.assertEqual(bm.course_blueprint_distribution_findings(course), [])

    def test_pack_with_no_coverage_blueprint_is_skipped_gracefully(self):
        course_dir = self.packs_dir / "empty"
        course_dir.mkdir()
        (course_dir / "mod1.json").write_text(json.dumps({"questions": []}))
        course = self._course("empty", ["mod1.json"])
        self.assertEqual(bm.course_blueprint_distribution_findings(course), [])

    def test_course_excluded_from_manifest_when_blueprint_distribution_fails(self):
        # Actual question counts stay balanced (19 vs 19, via extra a2
        # questions the blueprint doesn't require) so L27-DISTRIBUTION
        # (finding 11's course-level check) does NOT fire; only the
        # blueprint's declared minimums are skewed (19 vs 1). This isolates
        # the new check: without it, this course would install cleanly.
        clean_q = {
            "type": "multiple_choice", "difficulty": "easy",
            "prompt": "What is 2+2?", "options": ["4", "5", "6", "7"], "answer": 0,
            "explanation": "Two plus two is four.",
        }
        questions = []
        blueprint = []
        for i in range(19):
            topic = f"a1-t{i}"
            questions.append({**clean_q, "id": f"q-a1-{i}", "topic": topic, "exam_area": "a1"})
            blueprint.append({"topic": topic, "area": "a1", "min": 1})
        topic = "a2-t0"
        questions.append({**clean_q, "id": "q-a2-0", "topic": topic, "exam_area": "a2"})
        blueprint.append({"topic": topic, "area": "a2", "min": 1})
        for i in range(18):
            questions.append({
                **clean_q, "id": f"q-a2-extra-{i}",
                "topic": f"a2-extra-{i}", "exam_area": "a2",
            })

        course_dir = self.packs_dir / "skewed"
        course_dir.mkdir()
        (course_dir / "_course.json").write_text(json.dumps({
            "id": "skewed", "syllabus": self.SYLLABUS,
        }))
        write_pack(
            course_dir, "mod1.json", questions=questions,
            coverage_blueprint=blueprint, certify=True,
        )
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc, manifest = bm.build(lint=True)
        err_text = err.getvalue()
        self.assertNotEqual(rc, 0)
        self.assertIn("L27-BLUEPRINT-DISTRIBUTION", err_text)
        self.assertNotIn("area 'a1' has", err_text)  # L27-DISTRIBUTION's own phrasing
        self.assertEqual(manifest["courses"], [])


class NoManifestWriteTests(_Base):
    """E-27, retired with the on-disk artifact: the manifest is computed and
    returned, never written — with no file there is no truncated read for a
    concurrent consumer to catch, and no stale artifact to outlive a failed
    build."""

    def test_manifest_is_returned_and_nothing_is_written(self):
        course = self.packs_dir / "c1"
        course.mkdir()
        write_pack(course, "mod1.json")
        rc, manifest, _, _ = self.run_build()
        self.assertEqual(rc, 0)
        # the returned manifest carries the expected shape
        self.assertIn("courses", manifest)
        # no manifest.json (or .tmp leftover) lands on disk
        for name in ("manifest.json", "manifest.json.tmp"):
            artifact = self.packs_dir / name
            self.assertFalse(artifact.exists(),
                             f"the build must not write {artifact}")


class RepositoryManifestBuildTests(unittest.TestCase):
    """Keep the repository's strict pack-manifest build contract pinned."""

    def _copy_repository(self, root: Path) -> Path:
        packs_dir = root / "question-packs"
        shutil.copytree(PROJECT_ROOT / "question-packs", packs_dir)
        return packs_dir

    def test_repository_tree_has_a_strict_build(self):
        with tempfile.TemporaryDirectory() as root:
            rc, manifest = build_repository_copy(self._copy_repository(Path(root)))
        self.assertEqual(rc, 0)
        self.assertIs(manifest["strict_gate"], True)

    def test_unchanged_tree_builds_match_without_generated_at(self):
        with tempfile.TemporaryDirectory() as root:
            packs_dir = self._copy_repository(Path(root))
            first_rc, first = build_repository_copy(packs_dir)
            second_rc, second = build_repository_copy(packs_dir)
        self.assertEqual(first_rc, 0)
        self.assertEqual(second_rc, 0)
        self.assertEqual(without_generated_at(first), without_generated_at(second))

    def test_independent_tree_copies_build_identical_manifests(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            first_rc, first = build_repository_copy(
                self._copy_repository(root_path / "first")
            )
            second_rc, second = build_repository_copy(
                self._copy_repository(root_path / "second")
            )
        self.assertEqual(first_rc, 0)
        self.assertEqual(second_rc, 0)
        self.assertEqual(
            without_generated_at(first),
            without_generated_at(second),
        )


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


class LogPathDefaultsTests(unittest.TestCase):
    """T5: log defaults are repo-local under the gitignored .logs/, not /tmp."""

    def test_lint_log_is_repo_local(self):
        self.assertEqual(bm.LINT_LOG, PROJECT_ROOT / ".logs" / "quizzler-lint.log")
        self.assertTrue(bm.LINT_LOG.is_relative_to(bm.PROJECT_ROOT))
        self.assertEqual(bm.LINT_LOG.parent, bm.PROJECT_ROOT / ".logs")

    def test_project_root_is_the_repo_root(self):
        self.assertEqual(bm.PROJECT_ROOT, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
