"""Tests for the campaign frontier and evidence-source APIs (M1).

These tests never call a model CLI.  They pin the two contracts every ledger
reader shares: which frozen state a campaign is currently at (the frontier)
and which ``(qid, content hash)`` pairs carry clean configured-verifier
evidence (the sources), including the labels that distinguish a base census
from a round recheck and the recomputation that refuses to trust a stored
``valid`` or ``cleared_qids`` assertion.

Run from the project root::

    python3 -m unittest tests.test_campaign_evidence -v
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "campaign_evidence", PROJECT_ROOT / "scripts" / "campaign_evidence.py")
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)

# campaign_evidence's import puts scripts/ on sys.path, so the ledger
# builders come from the very same campaign_evidence implementation.
import certification_campaign as cc  # noqa: E402


QUESTIONS = [
    {"id": "q1", "type": "multiple_choice", "prompt": "First?",
     "options": ["A", "B"], "answer": 0, "explanation": "A."},
    {"id": "q2", "type": "multiple_choice", "prompt": "Second?",
     "options": ["A", "B"], "answer": 1, "explanation": "B."},
]


class CampaignEvidenceBase(unittest.TestCase):
    """A two-question pack plus the ledger helpers needed to build evidence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "private-source"
        self.source_root.mkdir()
        (self.source_root / "chapter.txt").write_text(
            "original source text", encoding="utf-8")
        self.pack = self.root / "core.json"
        self.write_pack()

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, **overrides):
        data = {
            "pack_id": "campaign-evidence-test",
            "subject": "CISSP",
            "questions": json.loads(json.dumps(QUESTIONS)),
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
        (self.root / "_course.json").write_text(json.dumps(course),
                                               encoding="utf-8")

    def snapshot(self):
        return cc.build_snapshot(self.pack)

    def profile(self, snapshot):
        return snapshot["critic_contract"]["profile"]

    def clear_census(self, snapshot, findings=()):
        """Return a complete census report by the configured verifier."""
        return {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": self.profile(snapshot),
            "complete": True,
            "examined_qids": list(snapshot["question_ids"]),
            "findings": list(findings),
            "errors": [],
        }

    def blocking(self, qid):
        return {"qid": qid, "issue": "key is incorrect",
                "severity": "wrong-answer", "confidence": "high"}

    def revised_snapshot(self, **revisions):
        """Write a pack where each named qid carries ``count`` revisions."""
        questions = json.loads(json.dumps(QUESTIONS))
        for qid, count in revisions.items():
            question = next(item for item in questions if item["id"] == qid)
            question["prompt"] += " revised" * count
        self.write_pack(questions=questions)
        return self.snapshot()

    def targeted_wrapper(self, snapshot, target_qids, blocking=()):
        """Return a clean targeted wrapper whose verifier blocks on ``blocking``."""
        def pass_report():
            return {
                "ready": False, "outcome": "review_ok", "partial": True,
                "layer_a": {"live": []},
                "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                            "questions_unchecked": 0,
                            "total": len(target_qids)},
            }
        wrapper = {
            "schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
            "certifying": False,
            "verifier_profile": self.profile(snapshot),
            "snapshot_fingerprint": snapshot["fingerprint"],
            "target_qids": list(target_qids),
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()},
            "exit_code": 3,
        }
        wrapper["verifier"]["report"]["layer_c"]["live"] = [
            self.blocking(qid) for qid in blocking
        ]
        return wrapper


class SourceLabelTests(CampaignEvidenceBase):
    """The source labels must name exactly what graded each pair clean."""

    def test_clean_census_labels_every_pair_base_census(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clear_census(snapshot))
        pairs, reasons = ce.evidence_sources(ledger, profile=self.profile(snapshot))
        self.assertEqual(reasons, [])
        self.assertEqual(pairs, {
            ("q1", snapshot["question_hashes"]["q1"]): "base-census",
            ("q2", snapshot["question_hashes"]["q2"]): "base-census",
        })

    def test_blocked_question_gets_round_recheck_label_at_new_content(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        cc.record_discovery(ledger, self.clear_census(base, [self.blocking("q1")]))
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(first, ["q1"]))

        pairs, reasons = ce.evidence_sources(ledger, profile=self.profile(base))
        self.assertEqual(reasons, [])
        self.assertEqual(pairs[("q2", base["question_hashes"]["q2"])],
                         "base-census")
        self.assertEqual(pairs[("q1", first["question_hashes"]["q1"])],
                         "round-recheck")
        # The blocked base content never carried clean evidence.
        self.assertNotIn(("q1", base["question_hashes"]["q1"]), pairs)

    def test_missing_census_withholds_base_pairs_but_keeps_round_pairs(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(first, ["q1"]))

        pairs, reasons = ce.evidence_sources(ledger, profile=self.profile(base))
        self.assertTrue(reasons)
        self.assertEqual(pairs,
                         {("q1", first["question_hashes"]["q1"]): "round-recheck"})

    def test_recheck_label_outranks_a_census_that_cleared_the_same_content(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        cc.record_discovery(ledger, self.clear_census(base))
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        # A recheck may cover more than its round declared; q2 is unchanged
        # content the census already cleared, and the recheck is newer.
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(first, ["q1", "q2"]))

        pairs, _reasons = ce.evidence_sources(ledger, profile=self.profile(base))
        self.assertEqual(pairs[("q2", base["question_hashes"]["q2"])],
                         "round-recheck")

    def test_hand_altered_recheck_record_is_recomputed_not_trusted(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        cc.record_discovery(ledger, self.clear_census(base, [self.blocking("q1")]))
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(
            ledger, self.targeted_wrapper(first, ["q1"], blocking=["q1"]))
        record = ledger["remediation_rounds"][-1]["targeted_rechecks"][-1]
        record["valid"] = True
        record["cleared_qids"] = ["q1"]

        pairs, _reasons = ce.evidence_sources(ledger, profile=self.profile(base))
        self.assertNotIn(("q1", first["question_hashes"]["q1"]), pairs)


class FrontierTests(CampaignEvidenceBase):
    """The frontier is the quarantine, else the last round, else the base."""

    def test_frontier_is_the_base_before_any_round(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        self.assertEqual(ce.campaign_frontier(ledger)["fingerprint"],
                         base["fingerprint"])

    def test_frontier_is_the_last_round_after_remediation(self):
        ledger = cc.new_ledger(self.snapshot())
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        second = self.revised_snapshot(q1=2)
        cc.begin_remediation(ledger, second, ["q1"])
        self.assertEqual(ce.campaign_frontier(ledger)["fingerprint"],
                         second["fingerprint"])

    def test_quarantine_snapshot_overtakes_the_last_round(self):
        ledger = cc.new_ledger(self.snapshot())
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        quarantined = self.revised_snapshot(q1=1, q2=1)
        ledger["quarantine"] = {"snapshot": quarantined}
        self.assertEqual(ce.campaign_frontier(ledger)["fingerprint"],
                         quarantined["fingerprint"])

    def test_malformed_quarantine_fails_closed(self):
        ledger = cc.new_ledger(self.snapshot())
        ledger["quarantine"] = {"snapshot": {"fingerprint": "sha256:" + "0" * 64}}
        with self.assertRaises(ce.CampaignError):
            ce.campaign_frontier(ledger)


class EvidenceProbeTests(CampaignEvidenceBase):
    """``_evidence_probe`` measures evidence against the frontier."""

    def test_probe_is_the_base_before_any_round(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        self.assertEqual(cc._evidence_probe(ledger, None)["fingerprint"],
                         base["fingerprint"])

    def test_probe_follows_the_last_round(self):
        ledger = cc.new_ledger(self.snapshot())
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        second = self.revised_snapshot(q1=2)
        cc.begin_remediation(ledger, second, ["q1"])
        self.assertEqual(cc._evidence_probe(ledger, None)["fingerprint"],
                         second["fingerprint"])

    def test_probe_follows_the_quarantine_frontier(self):
        ledger = cc.new_ledger(self.snapshot())
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        quarantined = self.revised_snapshot(q1=1, q2=1)
        ledger["quarantine"] = {"snapshot": quarantined}
        self.assertEqual(cc._evidence_probe(ledger, None)["fingerprint"],
                         quarantined["fingerprint"])

    def test_a_valid_current_snapshot_wins_over_the_frontier(self):
        ledger = cc.new_ledger(self.snapshot())
        current = self.revised_snapshot(q1=1)
        self.assertEqual(cc._evidence_probe(ledger, current)["fingerprint"],
                         current["fingerprint"])


if __name__ == "__main__":
    unittest.main()
