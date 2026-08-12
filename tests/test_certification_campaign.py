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
            "ds": {"exit_code": 3, "report": pass_report()},
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
            "ds": {"exit_code": 3, "report": pass_report()},
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


class DiscoveryTests(CampaignBase):
    def test_malformed_deepseek_finding_is_preserved_as_advisory_evidence(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "deepseek", findings=[{"qid": "q1", "issue": "bad", "severity": "unknown"}]))
        self.assertEqual(ledger["discoveries"][-1]["valid"], True)
        self.assertEqual(ledger["blockers"], [])
        self.assertTrue(ledger["discoveries"][-1]["advisory"])

    def test_incomplete_deepseek_coverage_does_not_block_verifier_gate(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_report(
            snapshot, "deepseek", examined_qids=["q1"]))
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
        cc.record_discovery(ledger, self.clear_report(snapshot, "deepseek"))
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
        cc.record_discovery(clean, self.clear_report(snapshot, "deepseek", examined_qids=["q1"]))
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
        cc.record_discovery(ledger, self.clear_report(snapshot, "deepseek"))
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
        cc.record_discovery(ledger, self.clear_report(snapshot, "deepseek"))
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
                         ["deepseek-advisory", "codex-terra-high"])
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

    def test_malformed_hybrid_deepseek_pass_is_advisory_and_eligibility_continues(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        wrapper = self.hybrid_wrapper(snapshot)
        wrapper["ds"] = {
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
                         ["deepseek-advisory", "codex-terra-high"])
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

    def test_incomplete_deepseek_recheck_does_not_block_clean_verifier(self):
        baseline = self.snapshot()
        ledger = self._ledger_with_q1_finding(baseline)
        changed = self.changed_snapshot("q1")
        cc.begin_remediation(ledger, changed, ["q1"])
        recheck = self.targeted_wrapper(changed, ["q1"])
        recheck["ds"]["report"]["layer_c"]["questions_unchecked"] = 1
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
