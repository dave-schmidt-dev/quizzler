"""Tests for the evidence-only certification campaign ledger.

These tests never call a model CLI.  They exercise only snapshot and ledger
contracts so discovery coordination cannot accidentally become a second
certification authority.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests.certification_campaign_fixtures import CampaignBase, QUESTIONS, cc


class SnapshotTests(CampaignBase):
    def test_snapshot_is_deterministic_and_portable(self):
        one = self.snapshot()
        two = self.snapshot()
        self.assertEqual(one, two)
        serialized = json.dumps(one)
        self.assertNotIn(str(self.source_root), serialized)
        self.assertNotIn("original source text", serialized)

    def test_snapshot_invalidates_for_waiver_grounding_source_and_contract_changes(self):
        baseline = self.snapshot()

        self.write_pack(lint_waivers=[{"rule": "L10", "qid": "q1", "reason": "reviewed"}])
        self.assertNotEqual(baseline["fingerprint"], self.snapshot()["fingerprint"])

        self.write_pack()
        course_path = self.root / "_course.json"
        course = json.loads(course_path.read_text(encoding="utf-8"))
        course["grounding"]["packs"][self.pack.name] = "other.txt"
        (self.source_root / "other.txt").write_text("original source text", encoding="utf-8")
        course_path.write_text(json.dumps(course), encoding="utf-8")
        self.assertNotEqual(baseline["fingerprint"], self.snapshot()["fingerprint"])

        self.write_pack()
        (self.source_root / "chapter.txt").write_text("revised source text", encoding="utf-8")
        self.assertNotEqual(baseline["fingerprint"], self.snapshot()["fingerprint"])

        self.write_pack()
        (self.source_root / "chapter.txt").write_text("original source text", encoding="utf-8")
        with patch.object(cc.pack_cert, "CRITIC_CONTRACT_VERSION", "next-contract"):
            self.assertNotEqual(baseline["fingerprint"], self.snapshot()["fingerprint"])


class SupersetIdentityTests(CampaignBase):
    """Keep the campaign's broad question digest separate from stamp identity."""

    def _questions_with_fields(self, **additional_fields):
        questions = json.loads(json.dumps(QUESTIONS))
        question = next(item for item in questions if item["id"] == "q1")
        question.update({
            "topic": "identity",
            "exam_objective": "OBJ-1",
            "answers": ["A"],
            "leftItems": ["left"],
            "rightItems": ["right"],
            "correctPairs": [["left", "right"]],
        })
        question.update(additional_fields)
        return questions

    def _mutate_question_field(self, question, field):
        value = question[field]
        if isinstance(value, str):
            question[field] = f"{value} revised"
        elif isinstance(value, list):
            question[field] = [*value, "revised"]
        elif isinstance(value, bool):
            question[field] = not value
        elif isinstance(value, (int, float)):
            question[field] = value + 1
        else:
            self.fail(f"test fixture has no mutation for {field}: {value!r}")

    def test_editing_any_relevant_fields_member_changes_the_campaign_per_question_hash(self):
        questions = self._questions_with_fields()
        self.write_pack(questions=questions)
        baseline = self.snapshot()
        baseline_digest = baseline["question_hashes"]["q1"]

        for field in cc.pack_cert.RELEVANT_FIELDS:
            with self.subTest(field=field):
                changed_questions = json.loads(json.dumps(questions))
                changed_question = next(
                    item for item in changed_questions if item["id"] == "q1"
                )
                self._mutate_question_field(changed_question, field)
                self.write_pack(questions=changed_questions)
                changed = self.snapshot()

                self.assertEqual(
                    changed["question_hashes"]["q2"],
                    baseline["question_hashes"]["q2"],
                )
                self.assertNotEqual(
                    changed["question_hashes"][changed_question["id"]],
                    baseline_digest,
                )

    def test_editing_a_field_outside_relevant_fields_also_changes_the_campaign_per_question_hash(self):
        questions = self._questions_with_fields(
            exam_area="Domain 1", difficulty="easy", tags=["identity"],
        )
        self.write_pack(questions=questions)
        baseline = self.snapshot()

        for field in ("exam_area", "difficulty", "tags"):
            with self.subTest(field=field):
                changed_questions = json.loads(json.dumps(questions))
                changed_question = next(
                    item for item in changed_questions if item["id"] == "q1"
                )
                self._mutate_question_field(changed_question, field)
                self.write_pack(questions=changed_questions)
                changed = self.snapshot()

                self.assertEqual(
                    changed["question_hashes"]["q2"],
                    baseline["question_hashes"]["q2"],
                )
                self.assertNotEqual(
                    changed["question_hashes"]["q1"],
                    baseline["question_hashes"]["q1"],
                )

    def test_editing_subject_or_source_directive_changes_the_snapshot_fingerprint_with_no_question_changed(self):
        questions = self._questions_with_fields()
        self.write_pack(questions=questions)
        baseline = self.snapshot()

        for field, value in (
            ("subject", "CISSP Advanced"),
            ("source_directive", "updated source directive"),
        ):
            with self.subTest(field=field):
                self.write_pack(questions=questions, **{field: value})
                changed = self.snapshot()

                self.assertNotEqual(changed["fingerprint"], baseline["fingerprint"])
                self.assertEqual(json.loads(self.pack.read_text(encoding="utf-8"))["questions"], questions)

    def test_editing_subject_or_source_directive_does_not_change_the_campaign_per_question_digest(self):
        questions = self._questions_with_fields()
        self.write_pack(questions=questions)
        baseline = self.snapshot()

        for field, value in (
            ("subject", "CISSP Advanced"),
            ("source_directive", "updated source directive"),
        ):
            with self.subTest(field=field):
                self.write_pack(questions=questions, **{field: value})
                self.assertEqual(self.snapshot()["question_hashes"], baseline["question_hashes"])


class DiscoveryTests(CampaignBase):
    def test_advisory_reviewer_accepts_current_and_legacy_labels(self):
        self.assertTrue(cc._is_advisory_reviewer("opencode-advisory"))
        self.assertTrue(cc._is_advisory_reviewer("opencode-low-advisory"))
        self.assertFalse(cc._is_advisory_reviewer("codex-terra-high"))

    def test_malformed_advisory_finding_is_preserved_as_advisory_evidence(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "opencode-low-advisory", findings=[{"qid": "q1", "issue": "bad", "severity": "unknown"}]))
        self.assertEqual(ledger["discoveries"][-1]["valid"], True)
        self.assertEqual(ledger["blockers"], [])
        self.assertTrue(ledger["discoveries"][-1]["advisory"])

    def test_incomplete_advisory_coverage_does_not_block_verifier_gate(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "opencode-low-advisory", examined_qids=["q1"]))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        self.assertFalse(ledger["discoveries"][0]["valid"])
        self.assertEqual(ledger["blockers"], [])
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_incomplete_verifier_coverage_is_left_to_final_full_gate(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "terra", complete=False, examined_qids=["q1"]))
        self.assertFalse(ledger["discoveries"][-1]["valid"])
        self.assertEqual(ledger["blockers"], [])
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_incomplete_verifier_findings_still_block(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        finding = {"qid": "q1", "issue": "key is incorrect",
                   "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "terra", complete=False, examined_qids=["q1"],
            findings=[finding]))
        self.assertFalse(ledger["discoveries"][-1]["valid"])
        self.assertTrue(any(item["kind"] == "finding" and item["status"] == "open"
                            for item in ledger["blockers"]))
        self.assertFalse(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_malformed_incomplete_verifier_report_still_blocks(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "terra", complete=False, examined_qids=[1]))
        self.assertTrue(any(item["kind"] == "operational" and item["status"] == "open"
                            for item in ledger["blockers"]))
        self.assertFalse(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_discovery_never_stamps_the_pack(self):
        snapshot = self.snapshot()
        before = json.loads(self.pack.read_text(encoding="utf-8"))
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(snapshot, "opencode-low-advisory"))
        after = json.loads(self.pack.read_text(encoding="utf-8"))
        self.assertEqual(before, after)
        self.assertNotIn("certification", after)
        self.assertNotIn("certification", ledger)
        self.assertTrue(ledger["final_certification"]["required"])

    def test_open_finding_or_coverage_gap_prevents_final_attempt(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        finding = {"qid": "q1", "issue": "key is incorrect",
                   "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra", findings=[finding]))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        permitted, reasons = cc.eligibility(ledger, current_snapshot=snapshot)
        self.assertFalse(permitted)
        self.assertIn("open campaign blockers remain", reasons)

        clean = cc.new_ledger(snapshot)
        cc.record_discovery(clean, self.clear_report(snapshot, "opencode-low-advisory", examined_qids=["q1"]))
        cc.record_discovery(clean, self.clear_report(snapshot, "terra"))
        self.assertTrue(cc.eligibility(clean, current_snapshot=snapshot)[0])

    def test_certification_eligibility_allows_advisory_full_census_only(self):
        for category in ("duplicate", "nit"):
            with self.subTest(category=category):
                snapshot = self.snapshot()
                ledger = cc.new_ledger(snapshot)
                finding = {
                    "qid": "q1", "issue": "quality observation",
                    "severity": "nit", "category": category, "confidence": "high",
                }
                cc.record_discovery(ledger, self.clear_report(
                    snapshot, "terra", findings=[finding]))
                self.assertTrue(cc.certification_eligibility(
                    ledger, current_snapshot=snapshot)[0])

        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        finding = {
            "qid": "q1", "issue": "incorrect answer", "severity": "wrong-answer",
            "category": "wrong-answer", "confidence": "high",
        }
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra", findings=[finding]))
        permitted, reasons = cc.certification_eligibility(
            ledger, current_snapshot=snapshot)
        self.assertFalse(permitted)
        self.assertIn("open campaign blockers remain", reasons)

    def test_operational_final_failure_is_retryable_on_same_snapshot(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(snapshot, "opencode-low-advisory"))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])
        cc.record_final_attempt(ledger, snapshot_fingerprint=snapshot["fingerprint"],
                                outcome="operational-error")
        self.assertEqual(ledger["final_certification"]["attempts"][-1]["outcome"],
                         "operational-error")
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_changed_pack_snapshot_prevents_final_attempt(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(snapshot, "opencode-low-advisory"))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        self.write_pack(lint_waivers=[{"rule": "L10", "qid": "q1", "reason": "reviewed"}])
        permitted, reasons = cc.eligibility(ledger, current_snapshot=self.snapshot())
        self.assertFalse(permitted)
        self.assertIn("the frozen campaign snapshot no longer matches the pack", reasons)


class HybridAdapterTests(CampaignBase):
    def test_valid_hybrid_wrapper_creates_two_complete_discoveries(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_hybrid_discovery(ledger, self.hybrid_wrapper(snapshot))
        self.assertEqual([entry["reviewer"] for entry in ledger["discoveries"]],
                         ["opencode-advisory", "codex-terra-high"])
        self.assertTrue(all(entry["valid"] for entry in ledger["discoveries"]))
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_full_hybrid_wrapper_snapshot_must_match_campaign(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        wrapper = self.hybrid_wrapper(snapshot)
        wrapper["snapshot_fingerprint"] = "sha256:" + "f" * 64
        cc.record_hybrid_discovery(ledger, wrapper)
        self.assertTrue(any(item["kind"] == "operational"
                            and item["status"] == "open"
                            for item in ledger["blockers"]))

    def test_malformed_hybrid_advisory_pass_is_advisory_and_eligibility_continues(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        wrapper = self.hybrid_wrapper(snapshot)
        wrapper["advisory"] = {
            "exit_code": 1,
            "report_error": "provider timed out",
            "diagnostic": "stderr indicated a timeout",
        }
        cc.record_hybrid_discovery(ledger, wrapper)
        self.assertFalse(ledger["discoveries"][0]["valid"])
        self.assertEqual(ledger["blockers"], [])
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_advisory_outer_exit_code_does_not_override_verifier_exit_code(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        wrapper = self.hybrid_wrapper(snapshot)
        wrapper["exit_code"] = 3
        wrapper["verifier"]["exit_code"] = 2
        cc.record_hybrid_discovery(ledger, wrapper)
        self.assertEqual(ledger["blockers"], [])
        self.assertEqual([entry["reviewer"] for entry in ledger["discoveries"]],
                         ["opencode-advisory", "codex-terra-high"])
        self.assertTrue(cc.eligibility(ledger, current_snapshot=snapshot)[0])

    def test_certifying_hybrid_wrapper_is_rejected_and_blocks_eligibility(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        wrapper = self.hybrid_wrapper(snapshot, certifying=True)
        cc.record_hybrid_discovery(ledger, wrapper)
        self.assertIn("non-certifying", ledger["discoveries"][-1]["problem"])
        self.assertFalse(cc.eligibility(ledger, current_snapshot=snapshot)[0])


if __name__ == "__main__":
    unittest.main()
