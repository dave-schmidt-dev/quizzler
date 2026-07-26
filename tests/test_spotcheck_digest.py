"""Unit tests for ``scripts/spotcheck_digest.py`` (INV-8 spot-check digest).

Exercises qid resolution, rendering, and missing-qid handling against a small
throw-away fixture course (not the real, gitignored sy0-701 content). The
script is imported by path (``scripts/`` isn't a package), mirroring
test_build_manifest.py / test_verify_pack.py.

Run from the project root::

    python3 -m unittest tests.test_spotcheck_digest -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "spotcheck_digest.py"

_spec = importlib.util.spec_from_file_location("spotcheck_digest", SCRIPT_PATH)
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


MC_Q = {
    "id": "c1q1", "type": "multiple_choice", "topic": "provisioning",
    "difficulty": "medium",
    "prompt": "Which process disables an account on offboarding?",
    "options": ["Provisioning", "Attestation", "De-provisioning", "Least privilege"],
    "answer": 2,
    "explanation": "De-provisioning removes access when an employee leaves.",
    "diagram": None,
    "tags": ["chapter-1", "provisioning"],
}

SCENARIO_Q = {
    "id": "c1q2", "type": "scenario_multiple_choice", "topic": "key-exchange",
    "difficulty": "medium",
    "prompt": "A key is delivered in person, out of band. Which method is this?",
    "options": ["Out-of-band key exchange", "In-band key exchange", "Key escrow", "Key stretching"],
    "answer": 0,
    "explanation": "Out-of-band exchange delivers the key outside the network.",
    "diagram": None,
    "tags": ["chapter-1"],
}

MULTISELECT_Q = {
    "id": "c2q1", "type": "multiple_select", "topic": "malware",
    "difficulty": "hard",
    "prompt": "Which of these are types of malware? (Select all that apply.)",
    "options": ["Virus", "Firewall", "Spyware", "Router"],
    "answers": [0, 2],
    "explanation": "Virus and spyware are malware; firewall and router are not.",
    "diagram": None,
    "tags": ["chapter-2"],
}

TRUEFALSE_Q = {
    "id": "c2q2", "type": "true_false", "topic": "impact",
    "difficulty": "easy",
    "prompt": "A vulnerability's impact can extend to an entire industry.",
    "answer": True,
    "explanation": "The chapter says impact can be industry-wide or org-specific.",
    "diagram": None,
    "tags": ["chapter-2"],
}

MATCHING_Q = {
    "id": "c2q3", "type": "matching", "topic": "data-obfuscation",
    "difficulty": "medium",
    "prompt": "Match each technique to its description.",
    "leftItems": ["Tokenization", "Data masking"],
    "rightItems": [
        "Substitutes realistic fake values, not reversible",
        "Random surrogate reversible by internal lookup",
    ],
    "correctPairs": [1, 0],
    "explanation": "Tokenization is reversible via lookup; masking is not.",
    "diagram": None,
    "tags": ["chapter-2"],
}


class _FixtureBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.course_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, filename: str, questions: list[dict], title: str | None = None) -> Path:
        p = self.course_dir / filename
        payload = {
            "pack_id": filename.replace(".json", ""),
            "title": title or filename.replace(".json", ""),
            "questions": questions,
        }
        p.write_text(json.dumps(payload))
        return p

    def write_course_meta(self):
        (self.course_dir / "_course.json").write_text(
            json.dumps({"id": "fixture", "name": "Fixture Course"})
        )


class QidResolutionTests(_FixtureBase):
    def test_resolves_qid_from_correct_pack_among_several(self):
        self.write_pack("ch01.json", [MC_Q], title="Chapter 1")
        self.write_pack("ch02.json", [MULTISELECT_Q, TRUEFALSE_Q, MATCHING_Q], title="Chapter 2")
        self.write_course_meta()

        index, warnings = sd.load_course_index(self.course_dir)

        self.assertEqual(warnings, [])
        self.assertIn("c1q1", index)
        self.assertIn("c2q2", index)
        self.assertEqual(index["c1q1"]["pack_file"], "ch01.json")
        self.assertEqual(index["c1q1"]["pack_title"], "Chapter 1")
        self.assertEqual(index["c2q2"]["pack_file"], "ch02.json")
        # _course.json must never be treated as a pack.
        self.assertNotIn("id", index)

    def test_ignores_invalid_json_pack_and_warns(self):
        self.write_pack("ch01.json", [MC_Q])
        (self.course_dir / "ch02.json").write_text("{not valid json")

        index, warnings = sd.load_course_index(self.course_dir)

        self.assertIn("c1q1", index)
        self.assertTrue(any("ch02.json" in w for w in warnings))

    def test_duplicate_qid_across_packs_keeps_first_and_warns(self):
        self.write_pack("ch01.json", [MC_Q])
        dupe = dict(MC_Q)
        dupe["prompt"] = "A different question reusing the same id."
        self.write_pack("ch02.json", [dupe])

        index, warnings = sd.load_course_index(self.course_dir)

        self.assertEqual(index["c1q1"]["pack_file"], "ch01.json")
        self.assertTrue(any("duplicate qid" in w for w in warnings))

    def test_missing_course_dir_reports_warning_not_crash(self):
        missing = self.course_dir / "does-not-exist"
        index, warnings = sd.load_course_index(missing)
        self.assertEqual(index, {})
        self.assertTrue(any("not found" in w for w in warnings))


class RenderShapeTests(unittest.TestCase):
    """render_question must surface prompt, topic, difficulty, explanation,
    and mark exactly the correct option(s)/pair(s) -- for every scored type
    the pack schema defines."""

    def _entry(self, q):
        return {"question": q, "pack_file": "chXX.json", "pack_title": "Chapter XX"}

    def test_multiple_choice_marks_only_correct_option(self):
        out = sd.render_question("c1q1", self._entry(MC_Q), "test reason")
        self.assertIn("De-provisioning", out)
        self.assertIn(f"{sd._mark(True)} De-provisioning", out)
        for wrong in ("Provisioning", "Attestation", "Least privilege"):
            self.assertIn(f"{sd._mark(False)} {wrong}", out)
        self.assertIn("provisioning", out)          # topic
        self.assertIn("medium", out)                 # difficulty
        self.assertIn("De-provisioning removes access", out)  # explanation
        self.assertIn("test reason", out)
        self.assertIn(MC_Q["prompt"], out)

    def test_scenario_multiple_choice_marks_only_correct_option(self):
        out = sd.render_question("c1q2", self._entry(SCENARIO_Q), "r")
        self.assertIn(f"{sd._mark(True)} Out-of-band key exchange", out)
        self.assertIn(f"{sd._mark(False)} In-band key exchange", out)

    def test_multiple_select_marks_all_correct_options(self):
        out = sd.render_question("c2q1", self._entry(MULTISELECT_Q), "r")
        self.assertIn(f"{sd._mark(True)} Virus", out)
        self.assertIn(f"{sd._mark(True)} Spyware", out)
        self.assertIn(f"{sd._mark(False)} Firewall", out)
        self.assertIn(f"{sd._mark(False)} Router", out)

    def test_true_false_marks_correct_boolean(self):
        out = sd.render_question("c2q2", self._entry(TRUEFALSE_Q), "r")
        self.assertIn(f"{sd._mark(True)} True", out)
        self.assertIn(f"{sd._mark(False)} False", out)

    def test_matching_resolves_correct_pairs(self):
        out = sd.render_question("c2q3", self._entry(MATCHING_Q), "r")
        # correctPairs = [1, 0]: Tokenization -> rightItems[1], Data masking -> rightItems[0]
        self.assertIn("Tokenization -> Random surrogate reversible by internal lookup", out)
        self.assertIn("Data masking -> Substitutes realistic fake values, not reversible", out)

    def test_unrecognized_type_falls_back_without_crashing(self):
        weird = {
            "id": "cXqY", "type": "future_type", "topic": "t", "difficulty": "easy",
            "prompt": "p", "explanation": "e", "some_field": "value",
        }
        out = sd.render_question("cXqY", self._entry(weird), "r")
        self.assertIn("unrecognized question type", out)
        self.assertIn("value", out)

    def test_missing_fields_render_placeholders_not_crash(self):
        sparse = {"id": "c9q9", "type": "multiple_choice", "options": [], "answer": 0}
        out = sd.render_question("c9q9", self._entry(sparse), "r")
        self.assertIn("(missing prompt)", out)
        self.assertIn("(missing explanation)", out)
        self.assertIn("(none)", out)  # topic/difficulty


class MissingQidTests(_FixtureBase):
    def test_missing_qid_reported_not_crashed(self):
        self.write_pack("ch01.json", [MC_Q])

        digest, warnings = sd.build_digest(self.course_dir, ["c1q1", "ghost-qid"])

        self.assertIn("## c1q1", digest)
        self.assertIn("## ghost-qid", digest)
        self.assertIn("MISSING", digest)
        self.assertTrue(any("ghost-qid" in w for w in warnings))
        # The valid qid still renders fully despite the missing one.
        self.assertIn(MC_Q["prompt"], digest)

    def test_all_missing_still_produces_digest(self):
        self.write_pack("ch01.json", [MC_Q])
        digest, warnings = sd.build_digest(self.course_dir, ["nope1", "nope2"])
        self.assertIn("## nope1", digest)
        self.assertIn("## nope2", digest)
        self.assertTrue(any("2 qid(s) not found" in w for w in warnings))


class BuildDigestOrderTests(_FixtureBase):
    def test_preserves_requested_qid_order(self):
        self.write_pack("ch01.json", [MC_Q])
        self.write_pack("ch02.json", [MULTISELECT_Q])

        digest, _ = sd.build_digest(self.course_dir, ["c2q1", "c1q1"])

        self.assertLess(digest.index("## c2q1"), digest.index("## c1q1"))


class DefaultReasonTests(unittest.TestCase):
    def test_known_qid_uses_build_notes_reason(self):
        self.assertIn("c19q18", sd.REASONS)
        self.assertIn("stem", sd.REASONS["c19q18"])

    def test_unknown_qid_falls_back_to_default_reason(self):
        entry = {"question": MC_Q, "pack_file": "ch01.json", "pack_title": "Chapter 1"}
        out = sd.render_question("c1q1", entry, sd.REASONS.get("nonexistent-qid", sd.DEFAULT_REASON))
        self.assertIn(sd.DEFAULT_REASON, out)


class MainCliTests(_FixtureBase):
    def test_main_writes_digest_file_with_default_qids_missing(self):
        # None of the DEFAULT_QIDS exist in this fixture course -- every one
        # should render as MISSING, and the run must still exit 0 (missing
        # qids are reported, not fatal).
        self.write_pack("ch01.json", [MC_Q])
        out_path = self.course_dir / "OUT.md"

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = sd.main(["--course-dir", str(self.course_dir), "--out", str(out_path)])

        self.assertEqual(rc, 0)
        self.assertTrue(out_path.is_file())
        content = out_path.read_text()
        self.assertIn("c19q18", content)
        self.assertIn("MISSING", content)
        self.assertIn("wrote", stdout.getvalue())

    def test_main_with_explicit_qids_renders_found_question(self):
        self.write_pack("ch01.json", [MC_Q, SCENARIO_Q])
        out_path = self.course_dir / "OUT.md"

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = sd.main([
                "--course-dir", str(self.course_dir),
                "--out", str(out_path),
                "c1q1", "c1q2",
            ])

        self.assertEqual(rc, 0)
        content = out_path.read_text()
        self.assertIn(MC_Q["prompt"], content)
        self.assertIn(SCENARIO_Q["prompt"], content)
        self.assertNotIn("MISSING", content)

    def test_main_defaults_out_path_under_course_dir(self):
        self.write_pack("ch01.json", [MC_Q])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = sd.main(["--course-dir", str(self.course_dir), "c1q1"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.course_dir / "SPOTCHECK_DIGEST.md").is_file())


if __name__ == "__main__":
    unittest.main()
