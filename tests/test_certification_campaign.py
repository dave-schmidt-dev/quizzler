"""Tests for the evidence-only certification campaign ledger.

These tests never call a model CLI.  They exercise only snapshot and ledger
contracts so discovery coordination cannot accidentally become a second
certification authority.
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

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


class CampaignBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "private-source"
        self.source_root.mkdir()
        (self.source_root / "chapter.txt").write_text("original source text", encoding="utf-8")
        self.pack = self.root / "core.json"
        self.write_pack()

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, **overrides):
        data = {
            "pack_id": "campaign-test",
            "subject": "CISSP",
            "questions": QUESTIONS,
            "lint_waivers": [],
            "factcheck_waivers": [],
        }
        data.update(overrides)
        self.pack.write_text(json.dumps(data), encoding="utf-8")
        course = {
            "grounding": {
                "text_root": str(self.source_root),
                "packs": {self.pack.name: "chapter.txt"},
            }
        }
        (self.root / "_course.json").write_text(json.dumps(course), encoding="utf-8")

    def snapshot(self, profile="codex-terra-high"):
        return cc.build_snapshot(self.pack, verifier_profile=profile)

    def clear_report(self, snapshot, reviewer, **overrides):
        if reviewer == "terra":
            reviewer = snapshot["critic_contract"]["profile"]
        report = {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": reviewer,
            "complete": True,
            "examined_qids": list(snapshot["question_ids"]),
            "findings": [],
            "errors": [],
        }
        report.update(overrides)
        return report

    def hybrid_wrapper(self, snapshot, **overrides):
        def pass_report():
            return {
                "ready": False,
                "outcome": "review_ok",
                "partial": False,
                "layer_a": {"live": []},
                "layer_c": {
                    "live": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0,
                    "total": len(snapshot["question_ids"]),
                },
            }
        wrapper = {
            "schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
            "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()},
            "exit_code": 3,
        }
        wrapper.update(overrides)
        return wrapper

    def targeted_wrapper(self, snapshot, target_qids, **overrides):
        def pass_report():
            return {
                "ready": False,
                "outcome": "review_ok",
                "partial": True,
                "layer_a": {"live": []},
                "layer_c": {
                    "live": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0,
                    "total": len(target_qids),
                },
            }
        wrapper = {
            "schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
            "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "target_qids": list(target_qids),
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()},
            "exit_code": 3,
        }
        wrapper.update(overrides)
        return wrapper

    def changed_snapshot(self, qid="q1"):
        questions = json.loads(json.dumps(QUESTIONS))
        question = next(item for item in questions if item["id"] == qid)
        question["prompt"] += " revised"
        self.write_pack(questions=questions)
        return self.snapshot()

    def revised_snapshot(self, **revisions):
        """Write a pack where each named qid carries ``count`` revisions.

        Revision counts are cumulative from the pristine questions, so a chain
        of rounds can be expressed as ``q1=1`` then ``q1=2`` and each round gets
        genuinely distinct question content.
        """
        questions = json.loads(json.dumps(QUESTIONS))
        for qid, count in revisions.items():
            question = next(item for item in questions if item["id"] == qid)
            question["prompt"] += " revised" * count
        self.write_pack(questions=questions)
        return self.snapshot()

    def blocking_finding(self, qid, issue="key is incorrect"):
        return {"qid": qid, "issue": issue, "severity": "wrong-answer",
                "confidence": "high"}

    def census_blocking(self, snapshot, *qids):
        """Return a ledger whose base census blocks exactly ``qids``."""
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "terra",
            findings=[self.blocking_finding(qid) for qid in qids]))
        return ledger

    def recheck(self, snapshot, targets, *, blocking=()):
        """Return a targeted wrapper whose verifier blocks on ``blocking``."""
        wrapper = self.targeted_wrapper(snapshot, targets)
        wrapper["verifier"]["report"]["layer_c"]["live"] = [
            self.blocking_finding(qid, "still wrong") for qid in blocking
        ]
        return wrapper


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


class RemediationTransitionTests(CampaignBase):
    def _ledger_with_q1_finding(self, snapshot):
        ledger = cc.new_ledger(snapshot)
        finding = {"qid": "q1", "issue": "key is incorrect",
                   "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra", findings=[finding]))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        return ledger

    def _ledger_with_real_blocking_census(self, snapshot):
        ledger = cc.new_ledger(snapshot)
        finding = {"qid": "q1", "issue": "key is incorrect",
                   "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "terra", findings=[finding]))
        return ledger

    def test_undeclared_question_change_rejects_remediation(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        changed = self.changed_snapshot("q1")
        with self.assertRaisesRegex(cc.CampaignError, "declared changed_qids"):
            cc.begin_remediation(ledger, changed, ["q2"])

    def test_waiver_grounding_or_profile_change_rejects_remediation(self):
        baseline = self.snapshot()

        self.write_pack(lint_waivers=[{"rule": "L10", "qid": "q1", "reason": "reviewed"}])
        with self.assertRaisesRegex(cc.CampaignError, "waivers"):
            cc.begin_remediation(cc.new_ledger(baseline), self.snapshot(), ["q1"])

        self.write_pack()
        (self.source_root / "chapter.txt").write_text("revised source", encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "grounding"):
            cc.begin_remediation(cc.new_ledger(baseline), self.snapshot(), ["q1"])

        self.write_pack()
        (self.source_root / "chapter.txt").write_text("original source text", encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "critic_contract"):
            cc.begin_remediation(cc.new_ledger(baseline), self.snapshot("claude-opus-high"), ["q1"])

    def test_clean_targeted_recheck_resolves_only_known_finding_blocker(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_q1_finding(baseline)
        changed = self.changed_snapshot("q1")
        cc.begin_remediation(ledger, changed, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(changed, ["q1"]))
        findings = [item for item in ledger["blockers"] if item["kind"] == "finding"]
        self.assertEqual(findings[0]["status"], "resolved")
        self.assertEqual(findings[0]["resolution_evidence"]["kind"],
                         "two-review-targeted-recheck")
        self.assertTrue(cc.eligibility(ledger, current_snapshot=changed)[0])

    def test_real_blocking_base_census_needs_only_clean_targeted_recheck(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_real_blocking_census(baseline)
        changed = self.changed_snapshot("q1")

        cc.begin_remediation(ledger, changed, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(changed, ["q1"]))

        self.assertEqual(
            [entry["reviewer"] for entry in ledger["discoveries"]],
            [baseline["critic_contract"]["profile"]],
        )
        self.assertTrue(cc.certification_eligibility(
            ledger, current_snapshot=changed
        )[0])

    def test_incomplete_advisory_recheck_does_not_block_clean_verifier(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_q1_finding(baseline)
        changed = self.changed_snapshot("q1")
        cc.begin_remediation(ledger, changed, ["q1"])
        recheck = self.targeted_wrapper(changed, ["q1"])
        recheck["advisory"]["report"]["layer_c"]["questions_unchecked"] = 1
        cc.record_hybrid_recheck(ledger, recheck)
        self.assertTrue(ledger["remediation"]["targeted_rechecks"][-1]["valid"])
        self.assertFalse(any(item["status"] == "open" for item in ledger["blockers"]))
        self.assertTrue(cc.eligibility(ledger, current_snapshot=changed)[0])

    def test_targeted_advisory_finding_qualifies_but_blocking_does_not(self):
        for severity, expected in (("nit", True), ("wrong-answer", False)):
            with self.subTest(severity=severity):
                self.write_pack()
                baseline = self.snapshot()
                ledger = self._ledger_with_q1_finding(baseline)
                changed = self.changed_snapshot("q1")
                cc.begin_remediation(ledger, changed, ["q1"])
                recheck = self.targeted_wrapper(changed, ["q1"])
                recheck["verifier"]["report"]["layer_c"]["live"] = [{
                    "qid": "q1", "issue": "targeted review note",
                    "severity": severity, "confidence": "high",
                }]
                cc.record_hybrid_recheck(ledger, recheck)
                permitted, _reasons = cc.certification_eligibility(
                    ledger, current_snapshot=changed)
                self.assertEqual(permitted, expected)

    def test_targeted_coverage_gap_or_finding_cannot_clear_blockers(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_q1_finding(baseline)
        changed = self.changed_snapshot("q1")
        cc.begin_remediation(ledger, changed, ["q1"])
        gap = self.targeted_wrapper(changed, ["q1"])
        gap["verifier"]["report"]["layer_c"]["questions_unchecked"] = 1
        cc.record_hybrid_recheck(ledger, gap)
        self.assertFalse(ledger["remediation"]["targeted_rechecks"][-1]["valid"])
        self.assertEqual(ledger["blockers"][0]["status"], "open")

        finding = self.targeted_wrapper(changed, ["q1"])
        finding["verifier"]["report"]["layer_c"]["live"] = [{
            "qid": "q1", "issue": "still wrong", "severity": "wrong-answer",
            "confidence": "high",
        }]
        cc.record_hybrid_recheck(ledger, finding)
        self.assertFalse(ledger["remediation"]["targeted_rechecks"][-1]["valid"])
        self.assertFalse(cc.eligibility(ledger, current_snapshot=changed)[0])

    def test_malformed_discovery_blocker_is_never_auto_resolved(self):
        baseline = self.snapshot()
        ledger = cc.new_ledger(baseline)
        malformed = {"qid": "(no-qid)", "issue": "unscoped",
                     "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(baseline, "terra", findings=[malformed]))
        cc.record_discovery(ledger, self.clear_report(baseline, "terra"))
        changed = self.changed_snapshot("q1")
        cc.begin_remediation(ledger, changed, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(changed, ["q1"]))
        malformed_blocker = next(item for item in ledger["blockers"]
                                 if item["kind"] == "malformed-finding")
        self.assertEqual(malformed_blocker["status"], "open")
        self.assertFalse(cc.eligibility(ledger, current_snapshot=changed)[0])


class ChainedRemediationTests(CampaignBase):
    """A feedback round must not cost a second full census.

    Before chaining, a ledger allowed exactly one remediation transition, so a
    recheck that surfaced a *new* finding on a question it was asked to re-read
    left the campaign with no legal path to a stamp.  Four CySA+ campaigns
    produced one certification that way.
    """

    def test_two_round_chain_certifies_without_a_second_census(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")

        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"], blocking=["q1"]))
        self.assertFalse(cc.certification_eligibility(ledger, current_snapshot=first)[0])

        second = self.revised_snapshot(q1=2)
        cc.begin_remediation(ledger, second, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(second, ["q1"]))

        self.assertEqual([entry["round"] for entry in ledger["remediation_rounds"]], [1, 2])
        self.assertEqual(ledger["remediation_rounds"][1]["base_snapshot_fingerprint"],
                         first["fingerprint"])
        # One census, two rounds, one stamp.
        self.assertEqual(len(ledger["discoveries"]), 1)
        permitted, reasons = cc.certification_eligibility(ledger, current_snapshot=second)
        self.assertTrue(permitted, reasons)

    def test_later_round_resolves_base_and_earlier_round_blockers(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"], blocking=["q1"]))

        findings = [item for item in ledger["blockers"] if item["kind"] == "finding"]
        self.assertEqual(len(findings), 2, "base census and round 1 each raised one")
        self.assertTrue(all(item["status"] == "open" for item in findings))

        second = self.revised_snapshot(q1=2)
        cc.begin_remediation(ledger, second, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(second, ["q1"]))

        findings = [item for item in ledger["blockers"] if item["kind"] == "finding"]
        self.assertEqual([item["status"] for item in findings], ["resolved", "resolved"])
        for item in findings:
            self.assertEqual(item["resolution_evidence"]["kind"],
                             "two-review-targeted-recheck")

    def test_partially_blocking_recheck_still_clears_its_clean_questions(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1", "q2")

        first = self.revised_snapshot(q1=1, q2=1)
        cc.begin_remediation(ledger, first, ["q1", "q2"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1", "q2"], blocking=["q1"]))
        record = ledger["remediation_rounds"][0]["targeted_rechecks"][-1]
        self.assertFalse(record["valid"])
        self.assertEqual(record["cleared_qids"], ["q2"])
        statuses = {item["qid"]: item["status"]
                    for item in ledger["blockers"] if item["kind"] == "finding"}
        self.assertEqual(statuses["q2"], "resolved")
        self.assertEqual(statuses["q1"], "open")

        # Round 2 re-reads only the question that blocked; q2 is never reviewed
        # again, which is the reviewer pass the partial credit saves.
        second = self.revised_snapshot(q1=2, q2=1)
        cc.begin_remediation(ledger, second, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(second, ["q1"]))
        permitted, reasons = cc.certification_eligibility(ledger, current_snapshot=second)
        self.assertTrue(permitted, reasons)

    def test_unscoped_recheck_finding_credits_nothing_as_clean(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1", "q2")
        first = self.revised_snapshot(q1=1, q2=1)
        cc.begin_remediation(ledger, first, ["q1", "q2"])
        wrapper = self.targeted_wrapper(first, ["q1", "q2"])
        wrapper["verifier"]["report"]["layer_c"]["live"] = [
            {"qid": "(no-qid)", "issue": "unscoped", "severity": "wrong-answer",
             "confidence": "high"}
        ]
        cc.record_hybrid_recheck(ledger, wrapper)
        record = ledger["remediation_rounds"][0]["targeted_rechecks"][-1]
        self.assertEqual(record["cleared_qids"], [])
        self.assertFalse(cc.certification_eligibility(ledger, current_snapshot=first)[0])

    def test_second_round_declared_set_must_match_change_since_prior_round(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])

        second = self.revised_snapshot(q1=1, q2=1)
        with self.assertRaisesRegex(cc.CampaignError, "declared changed_qids"):
            cc.begin_remediation(ledger, second, ["q1", "q2"])
        cc.begin_remediation(ledger, second, ["q2"])
        self.assertEqual(ledger["remediation_rounds"][1]["declared_changed_qids"], ["q2"])

    def test_round_must_chain_from_the_previous_round_not_the_base(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])

        # Re-declaring the base-anchored change set is the shape of chaining from
        # a snapshot this round does not follow.
        with self.assertRaisesRegex(cc.CampaignError, "declared changed_qids"):
            cc.begin_remediation(ledger, self.revised_snapshot(q1=2), ["q1", "q2"])

    def test_round_requires_a_change_since_the_round_it_chains_from(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        with self.assertRaisesRegex(cc.CampaignError, "at least one changed question"):
            cc.begin_remediation(ledger, first, ["q1"])

    def test_recheck_may_cover_more_than_declared_but_never_less(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1", "q2")
        first = self.revised_snapshot(q1=1, q2=1)
        cc.begin_remediation(ledger, first, ["q1", "q2"])

        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"]))
        record = ledger["remediation_rounds"][0]["targeted_rechecks"][-1]
        self.assertEqual(record["problem"],
                         "targeted recheck does not cover every declared remediation qid")
        self.assertFalse(cc.certification_eligibility(ledger, current_snapshot=first)[0])

        # Rechecking a superset is more evidence, not less.
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1", "q2"]))
        self.assertTrue(ledger["remediation_rounds"][0]["targeted_rechecks"][-1]["valid"])
        self.assertEqual(
            cc.resolve_blocker(
                ledger,
                next(item["id"] for item in ledger["blockers"]
                     if item["kind"] == "operational"),
                resolution="superseded by the covering recheck",
            )["blockers"][-1]["status"],
            "resolved",
        )
        permitted, reasons = cc.certification_eligibility(ledger, current_snapshot=first)
        self.assertTrue(permitted, reasons)

    def test_question_edited_after_its_clean_recheck_loses_that_evidence(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"]))
        self.assertTrue(cc.certification_eligibility(ledger, current_snapshot=first)[0])

        # Editing q1 again without opening a round must not inherit round 1's
        # clean evidence: that evidence is bound to the content it graded.
        later = self.revised_snapshot(q1=2)
        permitted, reasons = cc.certification_eligibility(ledger, current_snapshot=later)
        self.assertFalse(permitted)
        self.assertTrue(
            any("lack clean high-verifier evidence" in reason for reason in reasons),
            reasons,
        )

    def test_legacy_single_remediation_ledger_loads_and_stays_eligible(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"]))

        # Strip the chain fields to reproduce a ledger written before chaining.
        legacy = json.loads(json.dumps(ledger))
        legacy.pop("remediation_rounds")
        legacy["remediation"].pop("round")
        path = self.root / "legacy-ledger.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = cc.load_ledger(path)
        self.assertEqual(len(loaded["remediation_rounds"]), 1)
        self.assertEqual(loaded["remediation_rounds"][0]["round"], 1)
        permitted, reasons = cc.certification_eligibility(loaded, current_snapshot=first)
        self.assertTrue(permitted, reasons)

        # And it can still take its next round.
        second = self.revised_snapshot(q1=2)
        cc.begin_remediation(loaded, second, ["q1"])
        self.assertEqual(loaded["remediation_rounds"][1]["round"], 2)

    def test_recheck_evidence_is_recomputed_not_trusted(self):
        base = self.snapshot()
        ledger = self.census_blocking(base, "q1")
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.recheck(first, ["q1"], blocking=["q1"]))

        # Hand-asserting a clean record must not certify: eligibility re-derives
        # cleanliness from the stored reviewer report.
        record = ledger["remediation_rounds"][0]["targeted_rechecks"][-1]
        record["valid"] = True
        record["cleared_qids"] = ["q1"]
        self.assertFalse(cc.certification_eligibility(ledger, current_snapshot=first)[0])


class CampaignCliTests(CampaignBase):
    def _run_main(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cc.main(argv)
        return result, output.getvalue()

    def test_begin_remediation_and_ingest_recheck_dispatch(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_finding(baseline)
        ledger_path = self.root / "campaign.json"
        cc.save_ledger(ledger_path, ledger)
        changed = self.changed_snapshot()

        result, _ = self._run_main([
            "begin-remediation", "--ledger", str(ledger_path), "--pack", str(self.pack),
            "--changed-ids", "q1",
        ])
        self.assertEqual(result, 0)
        recheck_path = self.root / "recheck.json"
        recheck_path.write_text(json.dumps(self.targeted_wrapper(changed, ["q1"])),
                                encoding="utf-8")
        result, _ = self._run_main([
            "ingest-recheck", "--ledger", str(ledger_path), "--report", str(recheck_path),
        ])
        self.assertEqual(result, 0)
        stored = cc.load_ledger(ledger_path)
        self.assertTrue(stored["remediation"]["targeted_rechecks"][-1]["valid"])

    def _ledger_with_finding(self, snapshot):
        ledger = cc.new_ledger(snapshot)
        finding = {"qid": "q1", "issue": "key is incorrect",
                   "severity": "wrong-answer", "confidence": "high"}
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra", findings=[finding]))
        cc.record_discovery(ledger, self.clear_report(snapshot, "terra"))
        return ledger

    def changed_snapshot(self):
        questions = json.loads(json.dumps(QUESTIONS))
        questions[0]["prompt"] += " revised"
        self.write_pack(questions=questions)
        return self.snapshot()


if __name__ == "__main__":
    unittest.main()
