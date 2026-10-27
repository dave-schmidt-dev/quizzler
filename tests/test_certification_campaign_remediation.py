"""Remediation and CLI tests for the certification campaign ledger."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from tests.certification_campaign_fixtures import CampaignBase, QUESTIONS, cc


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
