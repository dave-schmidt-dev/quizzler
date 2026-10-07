"""Tests for ledger-level campaign quarantine (M2).

Quarantine shrinks a campaign's certification frontier to a retained subset of
questions while every frozen non-question input -- pack name, pack identity,
question context, waivers, grounding and reviewer contract -- stays identical
to the campaign's anchor.  These tests never call a model CLI: they exercise
the ledger contract plus one deterministic ``certify_campaign`` run on a
lint-clean fixture.

Run from the project root::

    python3 -m unittest tests.test_campaign_quarantine -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HYBRID_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"
_spec = importlib.util.spec_from_file_location("hybrid_verify", HYBRID_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# canonical modules: hybrid_verify imports certification_campaign, which imports
# campaign_quarantine, so both are the exact objects the finalizer sees.
cc = hv.certification_campaign
cq = sys.modules["campaign_quarantine"]
pack_cert = hv.verify_pack.pack_cert

from tests.test_campaign_inheritance import InheritanceCase

QUESTIONS = [
    {"id": "q1", "type": "multiple_choice", "topic": "math",
     "difficulty": "easy", "prompt": "What is 2+2?",
     "options": ["4", "5", "6", "7"], "answer": 0,
     "explanation": "Two plus two is four."},
    {"id": "q2", "type": "multiple_choice", "topic": "math",
     "difficulty": "easy", "prompt": "What is 3+3?",
     "options": ["6", "7", "8", "9"], "answer": 0,
     "explanation": "Three plus three is six."},
]

THIRD_QUESTION = {"id": "q3", "type": "multiple_choice", "topic": "math",
                  "difficulty": "easy", "prompt": "What is 4+4?",
                  "options": ["8", "9", "10", "11"], "answer": 0,
                  "explanation": "Four plus four is eight."}


class QuarantineCase(unittest.TestCase):
    """A lint-clean two-question pack plus the helpers to reduce and certify it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course = self.root / "course-a"
        self.course.mkdir()
        self.pack = self.course / "ch01.json"
        self.write_pack()

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, pack_path=None, questions=None, **overrides):
        pack_path = self.pack if pack_path is None else pack_path
        questions = (json.loads(json.dumps(QUESTIONS)) if questions is None
                     else questions)
        payload = {
            "subject": "Math",
            "title": "Quarantine fixture",
            "version": 1,
            "pack_id": "ch01",
            "questions": questions,
            "coverage_blueprint": [{"topic": "math", "min": len(questions)}],
        }
        payload.update(overrides)
        pack_path.write_text(json.dumps(payload), encoding="utf-8")

    def snapshot(self):
        return cc.build_snapshot(self.pack)

    def reduced_questions(self, *removed):
        return [q for q in json.loads(json.dumps(QUESTIONS))
                if q["id"] not in removed]

    def reduced_snapshot(self, *removed, **overrides):
        self.write_pack(questions=self.reduced_questions(*removed), **overrides)
        return self.snapshot()

    def snapshot_with_questions(self, questions, **overrides):
        self.write_pack(questions=questions, **overrides)
        return self.snapshot()

    def revised_snapshot(self, **revisions):
        questions = json.loads(json.dumps(QUESTIONS))
        for qid, count in revisions.items():
            question = next(item for item in questions if item["id"] == qid)
            question["prompt"] += " revised" * count
        self.write_pack(questions=questions)
        return self.snapshot()

    def blocking(self, qid):
        return {"qid": qid, "issue": "key is incorrect",
                "severity": "wrong-answer", "confidence": "high"}

    def clear_census(self, snapshot, findings=()):
        return {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": snapshot["critic_contract"]["profile"],
            "complete": True,
            "examined_qids": list(snapshot["question_ids"]),
            "findings": list(findings),
            "errors": [],
        }

    def targeted_wrapper(self, snapshot, target_qids):
        def pass_report():
            return {
                "ready": False, "outcome": "review_ok", "partial": True,
                "layer_a": {"live": []},
                "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                            "questions_unchecked": 0,
                            "total": len(target_qids)},
            }
        return {
            "schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
            "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "target_qids": list(target_qids),
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()},
            "exit_code": 3,
        }

    def clean_ledger(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        cc.record_discovery(ledger, self.clear_census(base))
        return ledger, base

    def save_ledger(self, ledger):
        path = self.root / "campaign.json"
        cc.save_ledger(path, ledger)
        return path


class BeginQuarantineTests(QuarantineCase):
    """M2 tests 1-3: freeze a reduced frontier, only shrink, frozen fields."""

    def test_quarantine_freezes_the_reduced_frontier_and_records_qids(self):
        ledger, base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")

        returned = cq.begin_quarantine(ledger, reduced)

        self.assertIs(returned, ledger)
        self.assertEqual(ledger["quarantine"]["quarantined_qids"], ["q2"])
        self.assertEqual(ledger["quarantine"]["snapshot"]["fingerprint"],
                         reduced["fingerprint"])
        self.assertEqual(ledger["quarantine"]["anchor_snapshot_fingerprint"],
                         base["fingerprint"])
        self.assertEqual(cq.quarantined_qids(ledger), ("q2",))
        self.assertEqual(cc.campaign_frontier(ledger)["fingerprint"],
                         reduced["fingerprint"])

    def test_quarantine_must_shrink_and_preserve_order(self):
        ledger, base = self.clean_ledger()

        with self.assertRaisesRegex(cc.CampaignError, "drop at least one"):
            cq.begin_quarantine(ledger, base)

        questions = json.loads(json.dumps(QUESTIONS)) + [THIRD_QUESTION]
        self.write_pack(questions=questions)
        ledger, _three = self.clean_ledger()
        reordered = [questions[2], questions[0]]
        with self.assertRaisesRegex(cc.CampaignError, "order"):
            cq.begin_quarantine(ledger, self.snapshot_with_questions(reordered))

    def test_quarantine_cannot_edit_a_retained_question(self):
        ledger, _base = self.clean_ledger()
        revised = json.loads(json.dumps(QUESTIONS))
        revised[0]["prompt"] += " revised"
        with self.assertRaisesRegex(cc.CampaignError,
                                    "content of retained question q1"):
            cq.begin_quarantine(ledger, self.snapshot_with_questions([revised[0]]))

    def test_quarantine_refuses_a_changed_subject(self):
        ledger, _base = self.clean_ledger()
        with self.assertRaisesRegex(cc.CampaignError, "question_context"):
            cq.begin_quarantine(
                ledger, self.reduced_snapshot("q2", subject="Security+"))

    def test_quarantine_refuses_a_changed_source_directive(self):
        ledger, _base = self.clean_ledger()
        with self.assertRaisesRegex(cc.CampaignError, "question_context"):
            cq.begin_quarantine(ledger, self.reduced_snapshot(
                "q2", source_directive="Answer only from the chapter."))

    def test_quarantine_refuses_a_changed_pack_id(self):
        ledger, _base = self.clean_ledger()
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            cq.begin_quarantine(
                ledger, self.reduced_snapshot("q2", pack_id="other-pack"))

    def test_quarantine_refuses_a_changed_course(self):
        ledger, _base = self.clean_ledger()
        other = self.root / "course-b"
        other.mkdir()
        other_pack = other / self.pack.name
        self.write_pack(pack_path=other_pack,
                        questions=self.reduced_questions("q2"))
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            cq.begin_quarantine(ledger, cc.build_snapshot(other_pack))

    def test_explicit_quarantined_qids_must_match_the_reduction(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")

        with self.assertRaisesRegex(cc.CampaignError, "exactly the questions"):
            cq.begin_quarantine(ledger, reduced, ["q1"])

        cq.begin_quarantine(ledger, reduced, ["q2"])
        self.assertEqual(ledger["quarantine"]["quarantined_qids"], ["q2"])

    def test_a_later_quarantine_records_the_full_dropped_set(self):
        questions = json.loads(json.dumps(QUESTIONS)) + [THIRD_QUESTION]
        self.write_pack(questions=questions)
        ledger, _base = self.clean_ledger()

        first_drop = self.reduced_snapshot("q3")
        cq.begin_quarantine(ledger, first_drop)
        self.assertEqual(ledger["quarantine"]["quarantined_qids"], ["q3"])

        second_drop = self.reduced_snapshot("q3", "q2")
        cq.begin_quarantine(ledger, second_drop)
        self.assertEqual(ledger["quarantine"]["quarantined_qids"], ["q2", "q3"])
        self.assertEqual(cq.quarantined_qids(ledger), ("q2", "q3"))


class BaseSourcePreconditionTests(QuarantineCase):
    """M2 test 6: a valid base source, not a *complete* base census."""

    def test_quarantine_requires_a_valid_base_source(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        with self.assertRaisesRegex(cc.CampaignError, "valid base evidence source"):
            cq.begin_quarantine(ledger, self.reduced_snapshot("q2"))

    def test_a_census_that_blocks_every_question_is_not_a_base_source(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        cc.record_discovery(ledger, self.clear_census(
            base, [self.blocking("q1"), self.blocking("q2")]))
        with self.assertRaisesRegex(cc.CampaignError, "valid base evidence source"):
            cq.begin_quarantine(ledger, self.reduced_snapshot("q2"))

    def test_round_recheck_evidence_alone_cannot_anchor_a_quarantine(self):
        base = self.snapshot()
        ledger = cc.new_ledger(base)
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        cc.record_hybrid_recheck(ledger, self.targeted_wrapper(first, ["q1"]))

        revised = [q for q in json.loads(json.dumps(QUESTIONS))
                   if q["id"] == "q1"]
        revised[0]["prompt"] += " revised"
        with self.assertRaisesRegex(cc.CampaignError, "valid base evidence source"):
            cq.begin_quarantine(ledger, self.snapshot_with_questions(revised))


class ReleaseQuarantineTests(QuarantineCase):
    """M2 test 5: release restores the pre-quarantine frontier."""

    def test_release_restores_the_previous_frontier(self):
        ledger, base = self.clean_ledger()
        first = self.revised_snapshot(q1=1)
        cc.begin_remediation(ledger, first, ["q1"])
        revised = [q for q in json.loads(json.dumps(QUESTIONS))
                   if q["id"] == "q1"]
        revised[0]["prompt"] += " revised"
        reduced = self.snapshot_with_questions(revised)
        cq.begin_quarantine(ledger, reduced)
        self.assertEqual(cc.campaign_frontier(ledger)["fingerprint"],
                         reduced["fingerprint"])

        returned = cq.release_quarantine(ledger)

        self.assertIs(returned, ledger)
        self.assertIsNone(ledger["quarantine"])
        self.assertEqual(cq.quarantined_qids(ledger), ())
        self.assertEqual(cc.campaign_frontier(ledger)["fingerprint"],
                         first["fingerprint"])
        self.assertNotEqual(base["fingerprint"], first["fingerprint"])

    def test_release_without_an_active_quarantine_is_refused(self):
        ledger = cc.new_ledger(self.snapshot())
        with self.assertRaisesRegex(cc.CampaignError, "no active quarantine"):
            cq.release_quarantine(ledger)

    def test_load_ledger_rejects_a_quarantine_without_qids(self):
        ledger, _base = self.clean_ledger()
        cq.begin_quarantine(ledger, self.reduced_snapshot("q2"))
        del ledger["quarantine"]["quarantined_qids"]
        path = self.root / "broken.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "quarantined_qids"):
            cc.load_ledger(path)

    def test_a_quarantine_cannot_claim_a_retained_question(self):
        ledger, _base = self.clean_ledger()
        cq.begin_quarantine(ledger, self.reduced_snapshot("q2"))
        ledger["quarantine"]["quarantined_qids"] = ["q1"]
        path = self.root / "altered.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "cannot remain"):
            cc.load_ledger(path)


class BlockerScopeTests(QuarantineCase):
    """A blocker on a retained question still blocks; a dropped one is skipped."""

    def test_blocker_on_a_retained_question_still_blocks(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cq.begin_quarantine(ledger, reduced)
        cc._append_blocker(ledger, kind="finding",
                           detail=json.dumps(self.blocking("q1")),
                           source="test", qid="q1")

        permitted, reasons = cc.certification_eligibility(
            ledger, current_snapshot=reduced)

        self.assertFalse(permitted)
        self.assertIn("open campaign blockers remain", reasons)

    def test_blocker_scoped_to_a_quarantined_question_is_skipped(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cc._append_blocker(ledger, kind="finding",
                           detail=json.dumps(self.blocking("q2")),
                           source="test", qid="q2")
        cq.begin_quarantine(ledger, reduced)

        permitted, reasons = cc.certification_eligibility(
            ledger, current_snapshot=reduced)

        self.assertTrue(permitted, reasons)


class ProvenanceValidatorTests(QuarantineCase):
    """The single pack_cert validator owns the optional quarantined_qids shape."""

    def provenance(self):
        return {
            "kind": "frozen-campaign-evidence",
            "evidence_policy": "no-new-llm-call",
            "campaign_snapshot_fingerprint": "sha256:" + "a" * 64,
            "base_snapshot_fingerprint": "sha256:" + "b" * 64,
            "verifier_profile": "codex-terra-high",
            "verifier_provider": "codex",
            "verifier_model": "gpt-5.1-codex",
            "remediation_qids": [],
        }

    def test_quarantined_qids_are_optional_and_shape_validated(self):
        provenance = self.provenance()
        self.assertTrue(pack_cert._frozen_campaign_provenance_fresh(provenance))

        provenance["quarantined_qids"] = ["q2", "q7"]
        self.assertTrue(pack_cert._frozen_campaign_provenance_fresh(provenance))

        for bad in (["q2", "q2"], [1], "q2", None, {"q2": True}, [""]):
            with self.subTest(bad=bad):
                provenance["quarantined_qids"] = bad
                self.assertFalse(
                    pack_cert._frozen_campaign_provenance_fresh(provenance))


class FinalizeBindingTests(QuarantineCase):
    """The context binding is carried through the deterministic finalizer."""

    def test_certify_campaign_stamps_the_reduced_pack_with_quarantine_provenance(self):
        ledger, base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cq.begin_quarantine(ledger, reduced)
        path = self.save_ledger(ledger)

        rc, out = hv.certify_campaign(self.pack, path)

        self.assertEqual(rc, 0, out)
        stamped = json.loads(self.pack.read_text(encoding="utf-8"))
        self.assertTrue(pack_cert.certification_fresh(stamped))
        self.assertEqual([q["id"] for q in stamped["questions"]], ["q1"])
        provenance = stamped["certification"]["provenance"]
        self.assertEqual(provenance["quarantined_qids"], ["q2"])
        self.assertEqual(provenance["campaign_snapshot_fingerprint"],
                         reduced["fingerprint"])
        self.assertEqual(provenance["base_snapshot_fingerprint"],
                         base["fingerprint"])

    def test_certify_campaign_refuses_a_subject_change_after_quarantine(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cq.begin_quarantine(ledger, reduced)
        path = self.save_ledger(ledger)
        self.reduced_snapshot("q2", subject="Security+")

        rc, out = hv.certify_campaign(self.pack, path)

        self.assertEqual(rc, 2)
        self.assertIn("snapshot no longer matches the pack", out)
        self.assertNotIn("certification",
                         json.loads(self.pack.read_text(encoding="utf-8")))

    def test_certify_campaign_refuses_a_source_directive_change_after_quarantine(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cq.begin_quarantine(ledger, reduced)
        path = self.save_ledger(ledger)
        self.reduced_snapshot("q2", source_directive="Answer only from the chapter.")

        rc, out = hv.certify_campaign(self.pack, path)

        self.assertEqual(rc, 2)
        self.assertIn("snapshot no longer matches the pack", out)
        self.assertNotIn("certification",
                         json.loads(self.pack.read_text(encoding="utf-8")))

    def test_certify_campaign_refuses_a_retained_blocker(self):
        ledger, _base = self.clean_ledger()
        reduced = self.reduced_snapshot("q2")
        cq.begin_quarantine(ledger, reduced)
        cc._append_blocker(ledger, kind="finding",
                           detail=json.dumps(self.blocking("q1")),
                           source="test", qid="q1")
        path = self.save_ledger(ledger)

        rc, out = hv.certify_campaign(self.pack, path)

        self.assertEqual(rc, 2)
        self.assertIn("open campaign blockers remain", out)
        self.assertNotIn("certification",
                         json.loads(self.pack.read_text(encoding="utf-8")))

    def test_begin_and_release_quarantine_cli_dispatch(self):
        ledger, _base = self.clean_ledger()
        path = self.save_ledger(ledger)
        self.reduced_snapshot("q2")

        output = io.StringIO()
        with redirect_stdout(output):
            rc = cc.main(["begin-quarantine", "--ledger", str(path),
                          "--pack", str(self.pack)])
        self.assertEqual(rc, 0)
        self.assertEqual(cq.quarantined_qids(cc.load_ledger(path)), ("q2",))

        with redirect_stdout(output):
            rc = cc.main(["release-quarantine", "--ledger", str(path)])
        self.assertEqual(rc, 0)
        self.assertIsNone(cc.load_ledger(path)["quarantine"])


class InheritedBaseSourceTests(InheritanceCase):
    """An inherited ledger counts as a valid base source when given the pack."""

    def test_inherited_ledger_quarantine_requires_pack(self) -> None:
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        self.rewrite_pack(questions=[QUESTIONS[0]])
        reduced = self.snapshot()

        with self.assertRaisesRegex(cc.CampaignError, "valid base evidence source"):
            cq.begin_quarantine(ledger, reduced)

        cq.begin_quarantine(ledger, reduced, pack=self.pack)
        self.assertEqual(cq.quarantined_qids(ledger), ("q2",))


if __name__ == "__main__":
    unittest.main()

