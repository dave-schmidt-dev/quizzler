"""Tests binding question context and pack identity into the campaign snapshot.

The frozen snapshot must bind the pack's subject and source_directive
(``question_context``) and its course id and pack_id (``pack_identity``), so a
remediation round cannot re-scope a campaign and a same-named pack in another
course can never reuse another campaign's evidence.  These tests never call a
model CLI; they exercise only snapshot and round contracts.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "certification_campaign.py"
_spec = importlib.util.spec_from_file_location("certification_campaign", SCRIPT_PATH)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


QUESTIONS = [
    {"id": "q1", "type": "multiple_choice", "prompt": "First?",
     "options": ["A", "B"], "answer": 0, "explanation": "A."},
    {"id": "q2", "type": "multiple_choice", "prompt": "Second?",
     "options": ["A", "B"], "answer": 1, "explanation": "B."},
]


class SnapshotBindingBase(unittest.TestCase):
    """One course directory holding a grounded two-question pack."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "private-source"
        self.source_root.mkdir()
        (self.source_root / "chapter.txt").write_text(
            "original source text", encoding="utf-8")
        self.course = self.root / "course-a"
        self.course.mkdir()
        self.pack = self.course / "core.json"
        self.write_pack()
        self.write_course(self.course)

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, pack_path=None, **overrides):
        pack_path = self.pack if pack_path is None else pack_path
        data = {
            "pack_id": "binding-test",
            "subject": "CISSP",
            "questions": QUESTIONS,
            "lint_waivers": [],
            "factcheck_waivers": [],
        }
        data.update(overrides)
        pack_path.write_text(json.dumps(data), encoding="utf-8")

    def write_course(self, course_dir):
        course = {
            "grounding": {
                "text_root": str(self.source_root),
                "packs": {"core.json": "chapter.txt"},
            }
        }
        (course_dir / "_course.json").write_text(json.dumps(course), encoding="utf-8")

    def snapshot(self, pack_path=None, profile="codex-terra-high"):
        return cc.build_snapshot(self.pack if pack_path is None else pack_path,
                                 verifier_profile=profile)

    def revised_questions(self, qid="q1"):
        questions = json.loads(json.dumps(QUESTIONS))
        question = next(item for item in questions if item["id"] == qid)
        question["prompt"] += " revised"
        return questions

    def legacy_snapshot(self):
        """Return a well-formed snapshot_version 1 snapshot (the pre-binding shape)."""
        current = self.snapshot()
        legacy = {key: value for key, value in current.items()
                  if key not in ("fingerprint", "snapshot_version",
                                 "question_context", "pack_identity")}
        legacy["snapshot_version"] = 1
        legacy["fingerprint"] = cc._digest(legacy)
        return legacy


class SnapshotSeamTests(SnapshotBindingBase):
    def test_build_snapshot_is_extracted_into_campaign_snapshot(self):
        self.assertEqual(cc.build_snapshot.__module__, "campaign_snapshot")
        self.assertEqual(cc.FROZEN_FIELDS, (
            "pack_name", "pack_identity", "question_context",
            "waivers", "grounding", "critic_contract",
        ))

    def test_snapshot_is_version_2_and_carries_context_and_identity(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["snapshot_version"], 2)
        self.assertTrue(snapshot["question_context"].startswith("sha256:"))
        self.assertEqual(snapshot["pack_identity"],
                         {"course": "course-a", "pack_id": "binding-test"})


class QuestionContextBindingTests(SnapshotBindingBase):
    """M0a: subject and source_directive are frozen into the snapshot."""

    def test_subject_change_changes_question_context_and_fingerprint(self):
        baseline = self.snapshot()
        self.write_pack(subject="Security+")
        changed = self.snapshot()
        self.assertNotEqual(baseline["question_context"], changed["question_context"])
        self.assertNotEqual(baseline["fingerprint"], changed["fingerprint"])
        # Only the context changed: the per-question hashes are untouched.
        self.assertEqual(baseline["question_hashes"], changed["question_hashes"])

    def test_source_directive_change_changes_question_context_and_fingerprint(self):
        baseline = self.snapshot()
        self.write_pack(source_directive="Answer only from the assigned chapter.")
        changed = self.snapshot()
        self.assertNotEqual(baseline["question_context"], changed["question_context"])
        self.assertNotEqual(baseline["fingerprint"], changed["fingerprint"])
        self.assertEqual(baseline["question_hashes"], changed["question_hashes"])

    def test_round_changing_subject_plus_one_question_is_refused(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        self.write_pack(subject="Security+", questions=self.revised_questions())
        with self.assertRaisesRegex(cc.CampaignError, "question_context"):
            cc.begin_remediation(ledger, self.snapshot(), ["q1"])

    def test_round_changing_source_directive_plus_one_question_is_refused(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        self.write_pack(source_directive="Answer only from the assigned chapter.",
                        questions=self.revised_questions())
        with self.assertRaisesRegex(cc.CampaignError, "question_context"):
            cc.begin_remediation(ledger, self.snapshot(), ["q1"])

    def test_v1_ledger_loads_but_its_snapshot_no_longer_matches(self):
        ledger = cc.new_ledger(self.legacy_snapshot())
        path = self.root / "legacy-ledger.json"
        cc.save_ledger(path, ledger)
        loaded = cc.load_ledger(path)
        self.assertEqual(loaded["snapshot"]["snapshot_version"], 1)

        current = self.snapshot()
        self.assertNotEqual(loaded["snapshot"]["fingerprint"], current["fingerprint"])
        permitted, reasons = cc.eligibility(loaded, current_snapshot=current)
        self.assertIn("the frozen campaign snapshot no longer matches the pack", reasons)
        self.assertFalse(permitted)


class PackIdentityBindingTests(SnapshotBindingBase):
    """M0b: course id and pack_id are frozen into the snapshot."""

    def test_same_filename_in_a_different_course_gives_a_different_fingerprint(self):
        other_course = self.root / "course-b"
        other_course.mkdir()
        self.write_course(other_course)
        other_pack = other_course / "core.json"
        self.write_pack(other_pack)

        baseline = self.snapshot()
        other = self.snapshot(other_pack)
        self.assertEqual(other["pack_identity"]["course"], "course-b")
        self.assertNotEqual(baseline["fingerprint"], other["fingerprint"])
        # The difference is exactly the course: content, context, and grounding
        # are identical between the two packs.
        self.assertEqual(baseline["question_hashes"], other["question_hashes"])
        self.assertEqual(baseline["question_context"], other["question_context"])
        self.assertEqual(baseline["grounding"], other["grounding"])

    def test_same_filename_with_a_different_pack_id_gives_a_different_fingerprint(self):
        baseline = self.snapshot()
        self.write_pack(pack_id="other-pack")
        changed = self.snapshot()
        self.assertEqual(changed["pack_identity"]["pack_id"], "other-pack")
        self.assertNotEqual(baseline["fingerprint"], changed["fingerprint"])
        self.assertEqual(baseline["question_hashes"], changed["question_hashes"])
        self.assertEqual(baseline["question_context"], changed["question_context"])

    def test_round_cannot_change_course(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        other_course = self.root / "course-b"
        other_course.mkdir()
        self.write_course(other_course)
        other_pack = other_course / "core.json"
        self.write_pack(other_pack, questions=self.revised_questions())
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            cc.begin_remediation(ledger, self.snapshot(other_pack), ["q1"])

    def test_round_cannot_change_pack_id(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        self.write_pack(pack_id="other-pack", questions=self.revised_questions())
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            cc.begin_remediation(ledger, self.snapshot(), ["q1"])


if __name__ == "__main__":
    unittest.main()
