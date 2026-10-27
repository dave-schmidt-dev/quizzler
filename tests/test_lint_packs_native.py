"""Unit tests for ``scripts/lint_packs.py`` — native metadata and source grounding."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.lint_pack_fixtures import (
    PROJECT_ROOT,
    lp,
    mc,
    rules,
)


class L28SourceGroundingTests(unittest.TestCase):
    """A course that opts into `grounding` must actually wire every pack into it.

    Like L27, L28 needs a real course DIRECTORY (the map lives in the sibling
    `_course.json`), so these fixtures write a two-file tree rather than a bare
    pack.
    """

    def _lint_in_course(self, pack_name: str, course, *, grounded_packs=None,
                         text_files: dict[str, str] | None = None) -> list:
        """Lint `pack_name` (an empty `{}` pack) inside a tmp course dir.

        `course` is either a full course dict, or (when `grounded_packs` is
        given) `None`/ignored — a `grounding` block pointing `text_root` at a
        real tmp `src` dir is built automatically so relative-path mismatches
        between the fixture and `course_grounding`'s `.resolve()` can't happen.
        Returns the pack's L28 findings.
        """
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            src = course_dir / "src"
            src.mkdir()
            for name, text in (text_files or {}).items():
                (src / name).write_text(text)
            if grounded_packs is not None:
                course = {
                    "id": "c", "name": "C",
                    "grounding": {"text_root": str(src), "packs": grounded_packs},
                }
            if course is not None:
                (course_dir / "_course.json").write_text(json.dumps(course))
            (course_dir / pack_name).write_text("{}")
            return rules(lp.lint_pack(course_dir / pack_name)["violations"], "L28")

    def test_no_grounding_block_is_not_gated(self):
        """A course that hasn't opted in must not turn red the moment a pack
        is added — this rule's failure mode isn't this course's to police."""
        found = self._lint_in_course("pack.json", course={"id": "c", "name": "C"})
        self.assertEqual(found, [])

    def test_a_pack_outside_a_course_is_not_gated(self):
        found = self._lint_in_course("pack.json", course=None)
        self.assertEqual(found, [])

    def test_unmapped_pack_in_a_grounded_course_is_critical(self):
        found = rules(
            self._lint_in_course(
                "pack.json", None, grounded_packs={"other.json": "ch1.txt"}),
            "L28", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("'pack.json'", found[0]["detail"])
        self.assertIn("grounding.packs", found[0]["detail"])

    def test_mapped_and_resolvable_pack_is_clean(self):
        found = self._lint_in_course(
            "pack.json", None, grounded_packs={"pack.json": "ch1.txt"},
            text_files={"ch1.txt": "real chapter text"})
        self.assertEqual(found, [])

    def test_mapped_but_missing_file_is_critical(self):
        found = rules(
            self._lint_in_course(
                "pack.json", None, grounded_packs={"pack.json": "ch1.txt"},
            ),
            "L28", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("could not be loaded", found[0]["detail"])

    def test_mapped_to_non_txt_file_is_critical(self):
        """Same defense course_grounding.load_source_text applies: only a
        `.txt` file living directly inside text_root resolves."""
        found = rules(
            self._lint_in_course(
                "pack.json", None, grounded_packs={"pack.json": "ch1.html"},
                text_files={"ch1.html": "<p>text</p>"}),
            "L28", "critical")
        self.assertEqual(len(found), 1)

    def test_l28_is_waivable(self):
        """Unlike L25-L27, a course may deliberately leave one pack ungrounded
        (e.g. a cross-chapter final review with no single source chapter)."""
        with tempfile.TemporaryDirectory() as d:
            course_dir = Path(d)
            course = {
                "id": "c", "name": "C",
                "grounding": {"text_root": str(course_dir / "src"),
                              "packs": {"other.json": "ch1.txt"}},
            }
            (course_dir / "_course.json").write_text(json.dumps(course))
            pack = {"lint_waivers": [
                {"rule": "L28", "reason": "cross-chapter review, no single source"}
            ]}
            (course_dir / "pack.json").write_text(json.dumps(pack))
            result = lp.lint_pack(course_dir / "pack.json")
        self.assertEqual(rules(result["violations"], "L28"), [])
        self.assertEqual(len(rules(result["waived"], "L28")), 1)

    def test_l28_not_in_non_waivable_rules(self):
        self.assertNotIn("L28", lp.NON_WAIVABLE_RULES)


class L29NativeMetadataContractTests(unittest.TestCase):
    """L29 keeps the Python authoring contract and the Swift decoder in step.

    The rule exists because they silently disagreed: the live CISSP pack
    declared ``generation_mode: "llm-assisted"``, QuizzlerKit accepts only
    ``manual|templated|llm|hybrid``, and nothing checked, so the app refused
    all 203 questions with no signal on either side.
    """

    def pack(self, **over) -> dict:
        base = {
            "pack_id": "p", "subject": "S", "title": "T", "version": 1,
            "questions": [mc()],
        }
        base.update(over)
        return base

    def test_undocumented_generation_mode_fires(self):
        found = rules(lp.check_l29_native_metadata_contract(
            self.pack(generation_mode="llm-assisted")), "L29", "critical")
        self.assertEqual(len(found), 1)
        self.assertIn("llm-assisted", found[0]["detail"])

    def test_every_documented_generation_mode_passes(self):
        for mode in sorted(lp.NATIVE_GENERATION_MODES):
            with self.subTest(mode=mode):
                self.assertEqual(
                    lp.check_l29_native_metadata_contract(self.pack(generation_mode=mode)), [])

    def test_absent_generation_mode_passes(self):
        self.assertEqual(lp.check_l29_native_metadata_contract(self.pack()), [])

    def test_documented_authoring_metadata_is_allowed_but_unknown_keys_fail(self):
        self.assertEqual(
            lp.check_l29_native_metadata_contract(self.pack(
                factcheck_waivers=[], source_directive="Course source"
            )),
            [],
        )
        found = rules(
            lp.check_l29_native_metadata_contract(self.pack(unexpected=True)),
            "L29",
            "critical",
        )
        self.assertEqual(len(found), 1)
        self.assertIn("unexpected", found[0]["detail"])

        question = dict(mc())
        question["unexpected_question_key"] = True
        found = rules(
            lp.check_l29_native_metadata_contract(self.pack(questions=[question])),
            "L29",
            "critical",
        )
        self.assertEqual(len(found), 1)
        self.assertIn("unexpected_question_key", found[0]["detail"])

    def test_wrong_contract_version_fires(self):
        found = rules(lp.check_l29_native_metadata_contract(self.pack(version=2)), "L29")
        self.assertEqual(len(found), 1)

    def test_blank_identity_fields_fire_one_finding_each(self):
        found = rules(lp.check_l29_native_metadata_contract(
            self.pack(pack_id="  ", subject="", title="T")), "L29")
        self.assertEqual(len(found), 2)

    def test_notes_limit_matches_the_native_limit(self):
        limit = lp.NATIVE_NOTES_MAX
        self.assertEqual(lp.check_l29_native_metadata_contract(self.pack(notes="n" * limit)), [])
        self.assertEqual(len(rules(lp.check_l29_native_metadata_contract(
            self.pack(notes="n" * (limit + 1))), "L29")), 1)

    def test_generated_at_requires_a_full_timestamp_with_an_offset(self):
        for good in ("2026-08-11T01:18:41+00:00", "2026-08-11T01:18:41Z",
                     "2026-08-11T01:18:41.500-04:00"):
            with self.subTest(value=good):
                self.assertEqual(
                    lp.check_l29_native_metadata_contract(self.pack(generated_at=good)), [])
        # A bare date is what a Python receipt writes and what Swift refuses.
        for bad in ("2026-08-04", "2026-08-11T01:18:41", "not-a-date", "2026-13-01T00:00:00Z"):
            with self.subTest(value=bad):
                self.assertEqual(
                    len(rules(lp.check_l29_native_metadata_contract(
                        self.pack(generated_at=bad)), "L29")), 1)

    def test_empty_questions_fires(self):
        self.assertEqual(
            len(rules(lp.check_l29_native_metadata_contract(self.pack(questions=[])), "L29")), 1)

    def test_l29_is_not_waivable(self):
        """A waiver cannot make the app decode a pack it rejects."""
        self.assertIn("L29", lp.NON_WAIVABLE_RULES)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pack.json"
            path.write_text(json.dumps(self.pack(
                generation_mode="llm-assisted",
                lint_waivers=[{"rule": "L29", "reason": "we like this mode"}])))
            result = lp.lint_pack(path)
        self.assertEqual(len(rules(result["violations"], "L29")), 1)
        self.assertEqual(rules(result["waived"], "L29"), [])

    def test_placeholder_critic_subjects_fire_l29(self):
        placeholders = (
            "COURSE NAME",
            "course name",
            "Course Name",
            "course-name",
            "course_name",
            "coursename",
            "COURSE-NAME",
            "COURSE_NAME",
            "[COURSE NAME]",
            "<COURSE NAME>",
            "{{COURSE NAME}}",
            "\"COURSE NAME\"",
            "SUBJECT",
            "Subject",
            "subject name",
            "YOUR COURSE NAME",
            "placeholder",
            "Placeholder",
            "TODO",
            "TBD",
            "insert course name",
            "enter course name",
        )
        for ph in placeholders:
            with self.subTest(subject=ph):
                found = rules(lp.check_l29_native_metadata_contract(self.pack(subject=ph)), "L29", "critical")
                self.assertEqual(len(found), 1)
                self.assertIn("is a placeholder", found[0]["detail"])

        # Placeholder subject failure cannot be waived (L29 is non-waivable)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pack.json"
            path.write_text(json.dumps(self.pack(
                subject="COURSE NAME",
                lint_waivers=[{"rule": "L29", "reason": "placeholder intended"}],
            )))
            result = lp.lint_pack(path)
        self.assertEqual(len(rules(result["violations"], "L29")), 1)
        self.assertEqual(rules(result["waived"], "L29"), [])

    def test_valid_concise_labels_pass_l29(self):
        valid_labels = (
            "Go",
            "AWS",
            "SQL",
            "Git",
            "Math",
            "CISSP",
            "C++",
            "Rust",
            "Security+",
            "General Knowledge",
            "Sample Course",
            "S",
        )
        for label in valid_labels:
            with self.subTest(subject=label):
                self.assertEqual(lp.check_l29_native_metadata_contract(self.pack(subject=label)), [])


class NativeContractParityTests(unittest.TestCase):
    """The Python constants must equal the Swift ones they mirror.

    Reading them out of QuizzlerKit's source is the point: a test that
    restated the values would pass while the two languages drifted apart,
    which is precisely the failure this rule was added to catch.
    """

    SWIFT = PROJECT_ROOT / "app" / "QuizzlerKit" / "Sources" / "QuizzlerKit" / "Questions" / "PackManifest.swift"

    def swift_source(self) -> str:
        self.assertTrue(self.SWIFT.exists(), f"missing {self.SWIFT}")
        return self.SWIFT.read_text(encoding="utf-8")

    def test_generation_mode_allowlist_matches_quizzlerkit(self):
        import re
        source = self.swift_source()
        match = re.search(r'\[((?:"[a-z]+",?\s*)+)\]\.contains\(generationMode\)', source)
        self.assertIsNotNone(match, "could not find the generationMode allowlist in PackManifest.swift")
        swift_modes = set(re.findall(r'"([a-z]+)"', match.group(1)))
        self.assertEqual(swift_modes, set(lp.NATIVE_GENERATION_MODES))

    def test_contract_version_matches_quizzlerkit(self):
        import re
        match = re.search(r"currentContractVersion\s*=\s*(\d+)", self.swift_source())
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), lp.NATIVE_CONTRACT_VERSION)

    def test_notes_limit_matches_quizzlerkit(self):
        import re
        match = re.search(r"notes\?\.count \?\? 0 <= (\d+)", self.swift_source())
        self.assertIsNotNone(match, "could not find the notes length limit in PackManifest.swift")
        self.assertEqual(int(match.group(1)), lp.NATIVE_NOTES_MAX)


class InstalledPackContractTests(unittest.TestCase):
    """Every pack this repository ships must be loadable by the app."""

    def installable_packs(self) -> list[Path]:
        return list(lp.pack_discovery.iter_installable_packs(PROJECT_ROOT / "question-packs"))

    def test_every_installed_pack_satisfies_the_native_contract(self):
        packs = self.installable_packs()
        self.assertGreater(len(packs), 0, "no installable packs found to check")
        for path in packs:
            with self.subTest(pack=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(lp.check_l29_native_metadata_contract(data), [])


if __name__ == "__main__":
    unittest.main()
