"""Tests for cross-campaign census inheritance (M6, INV-7).

Every prior campaign state is built through the real ``certify_campaign``
with Layer A patched clean, so each prior ledger ends in a genuine issuance
receipt bound to the pack's certification block.  No reviewer or network
call ever happens.
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
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "hybrid_verify", PROJECT_ROOT / "scripts" / "hybrid_verify.py")
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# The same module objects hybrid_verify uses, so patches land where
# certify_campaign actually looks its collaborators up.
cc = hv.certification_campaign
ce = sys.modules["campaign_evidence"]
cs = sys.modules["campaign_snapshot"]
vp = hv.verify_pack
pack_cert = vp.pack_cert
verifier_profiles = sys.modules["verifier_profiles"]
factcheck_pack = sys.modules["factcheck_pack"]
import campaign_inheritance as ci  # noqa: E402

QUESTIONS = [
    {"id": "q1", "type": "multiple_choice", "topic": "math", "difficulty": "easy",
     "prompt": "What is 2+2?", "options": ["4", "5", "6", "7"], "answer": 0,
     "explanation": "Two plus two is four."},
    {"id": "q2", "type": "multiple_choice", "topic": "math", "difficulty": "easy",
     "prompt": "What is 3+3?", "options": ["6", "7", "8", "9"], "answer": 0,
     "explanation": "Three plus three is six."},
]
THIRD_QUESTION = {"id": "q3", "type": "multiple_choice", "topic": "math",
                  "difficulty": "easy", "prompt": "What is 4+4?",
                  "options": ["8", "9", "10", "11"], "answer": 0,
                  "explanation": "Four plus four is eight."}

CLEAN_LAYER_A = {"live": [], "waived": [], "hygiene": []}


class InheritanceCase(unittest.TestCase):
    """A two-question pack plus the prior-campaign and recheck helpers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "private-source"
        self.source_root.mkdir()
        (self.source_root / "chapter.txt").write_text(
            "original source text", encoding="utf-8")
        self.course = self.root / "course-a"
        self.course.mkdir()
        self.pack = self.course / "ch01.json"
        self.write_pack()

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, pack_path=None, questions=None, **overrides):
        """Write the fixture pack and its course grounding metadata."""
        pack_path = self.pack if pack_path is None else pack_path
        data = {
            "subject": "Math", "title": "Inheritance fixture", "version": 1,
            "pack_id": "ch01",
            "questions": (json.loads(json.dumps(QUESTIONS))
                          if questions is None else questions),
            "coverage_blueprint": [{"topic": "math", "min": 1}],
        }
        data.update(overrides)
        pack_path.write_text(json.dumps(data), encoding="utf-8")
        (pack_path.parent / "_course.json").write_text(json.dumps({
            "grounding": {"text_root": str(self.source_root),
                          "packs": {pack_path.name: "chapter.txt"}},
        }), encoding="utf-8")

    def rewrite_pack(self, *, questions=None, **overrides):
        """Edit the pack on disk in place, keeping its certification block."""
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        if questions is not None:
            data["questions"] = questions
        data.update(overrides)
        self.pack.write_text(json.dumps(data), encoding="utf-8")

    def snapshot(self):
        return cc.build_snapshot(self.pack)

    def clean_census(self, snapshot):
        return {"snapshot_fingerprint": snapshot["fingerprint"],
                "reviewer": snapshot["critic_contract"]["profile"],
                "complete": True, "examined_qids": list(snapshot["question_ids"]),
                "findings": [], "errors": []}

    def prior_ledger(self, name="prior.json"):
        """Certify the pack through the real finalizer and return the path."""
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))
        path = self.save(ledger, name)
        rc, out = self.certify(path)
        self.assertEqual(rc, 0, out)
        return path

    def certify(self, ledger_path):
        with patch.object(vp, "run_layer_a", return_value=dict(CLEAN_LAYER_A)):
            return hv.certify_campaign(self.pack, ledger_path)

    def inherit(self, prior_path, **kwargs):
        return ci.inherit_ledger(self.pack, prior_path, **kwargs)

    def save(self, ledger, name="campaign.json"):
        path = self.root / name
        cc.save_ledger(path, ledger)
        return path

    def targeted_wrapper(self, snapshot, target_qids):
        report = {"ready": False, "outcome": "review_ok", "partial": True,
                  "layer_a": {"live": []},
                  "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                              "questions_unchecked": 0,
                              "total": len(target_qids)}}
        return {"schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
                "certifying": False,
                "verifier_profile": snapshot["critic_contract"]["profile"],
                "snapshot_fingerprint": snapshot["fingerprint"],
                "target_qids": list(target_qids),
                "advisory": {"exit_code": 3, "report": report},
                "verifier": {"exit_code": 3, "report": report},
                "exit_code": 3}

    def recheck(self, ledger, snapshot, target_qids):
        cc.record_hybrid_recheck(
            ledger, self.targeted_wrapper(snapshot, target_qids))

    def evidence(self, ledger, data):
        profile = ledger["snapshot"]["critic_contract"]["profile"]
        return ce.evidence_sources(ledger, profile=profile, data=data)

    def inherited(self, pairs):
        return {pair for pair, source in pairs.items()
                if source == ce.INHERITED_SOURCE}


class FreshInheritanceTests(InheritanceCase):
    """M6 tests 1 and 15: a clean carryover certifies with no new review."""

    def test_fresh_inheritance_certifies_without_census_or_reviewer(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        record = ledger["inheritance"]
        self.assertEqual(record["inherited_qids"], ["q1", "q2"])
        self.assertEqual(record["inheritance_recheck_qids"], [])
        self.assertEqual(ledger["remediation_rounds"], [])
        # The prior ledger and certification block are embedded verbatim.
        self.assertEqual(record["prior_ledger"], cc.load_ledger(prior))
        self.assertEqual(record["prior_certification"],
                         json.loads(self.pack.read_text())["certification"])
        path = self.save(ledger)
        with patch.object(factcheck_pack, "run_critic",
                          side_effect=AssertionError("reviewer invoked")):
            rc, out = self.certify(path)
        self.assertEqual(rc, 0, out)
        stamped = json.loads(self.pack.read_text())
        self.assertTrue(pack_cert.certification_fresh(stamped))
        self.assertEqual(stamped["certification"]["provenance"]["inheritance"], {
            "prior_campaign_snapshot_fingerprint":
                record["prior_campaign_snapshot_fingerprint"],
            "prior_receipt_digest": record["prior_receipt_digest"],
            "inherited_count": 2, "inheritance_recheck_qids": []})
        # The new ledger records its own receipt, so depth 1 is the ceiling.
        self.assertEqual(len(cc.load_ledger(path)["issuance_receipts"]), 1)

    def test_init_cli_dispatch_writes_the_inherited_ledger(self):
        prior = self.prior_ledger()
        path = self.root / "inherited.json"
        output = io.StringIO()
        argv = ["init", str(self.pack), "--ledger", str(path),
                "--inherit-from-ledger", str(prior)]
        with redirect_stdout(output):
            self.assertEqual(cc.main(argv), 0)
        self.assertEqual(cc.load_ledger(path)["inheritance"]["inherited_qids"],
                         ["q1", "q2"])
        self.rewrite_pack(subject="Security+")
        argv[3] = str(self.root / "none.json")
        with redirect_stdout(output):
            self.assertEqual(cc.main(argv), 2)


class PerQidCoverageTests(InheritanceCase):
    """M6 tests 2-5: the per-qid stamp and full-hash rules."""

    def test_stale_stamp_leaves_qid_uncovered_and_requires_its_recheck(self):
        prior = self.prior_ledger()
        questions = json.loads(json.dumps(QUESTIONS))
        questions[0]["prompt"] += " revised"
        self.rewrite_pack(questions=questions)
        ledger = self.inherit(prior)
        self.assertEqual(ledger["inheritance"]["inherited_qids"], ["q2"])
        self.assertEqual(ledger["inheritance"]["inheritance_recheck_qids"],
                         ["q1"])
        round_one = ledger["remediation_rounds"][0]
        self.assertEqual(round_one["kind"], "inheritance-recheck")
        self.assertEqual(round_one["declared_changed_qids"], ["q1"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.assertIn("lack clean high-verifier evidence", out)
        self.recheck(ledger, ledger["snapshot"], ["q1"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 0, out)
        provenance = json.loads(
            self.pack.read_text())["certification"]["provenance"]
        self.assertEqual(provenance["remediation_qids"], [])
        self.assertEqual(provenance["inheritance"]["inherited_count"], 1)
        self.assertEqual(
            provenance["inheritance"]["inheritance_recheck_qids"], ["q1"])

    def test_deleted_stamp_leaves_qid_uncovered_while_others_inherit(self):
        prior = self.prior_ledger()
        data = json.loads(self.pack.read_text())
        del data["certification"]["question_stamps"]["q2"]
        self.pack.write_text(json.dumps(data), encoding="utf-8")
        ledger = self.inherit(prior)
        self.assertEqual(ledger["inheritance"]["inherited_qids"], ["q1"])
        self.assertEqual(ledger["inheritance"]["inheritance_recheck_qids"],
                         ["q2"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.assertIn("q2", out)

    def test_missing_stamp_on_a_new_qid_leaves_it_uncovered(self):
        prior = self.prior_ledger()
        self.rewrite_pack(
            questions=json.loads(json.dumps(QUESTIONS)) + [THIRD_QUESTION])
        ledger = self.inherit(prior)
        self.assertEqual(ledger["inheritance"]["inherited_qids"], ["q1", "q2"])
        self.assertEqual(ledger["inheritance"]["inheritance_recheck_qids"],
                         ["q3"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.assertIn("q3", out)
        self.recheck(ledger, ledger["snapshot"], ["q3"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 0, out)

    def test_subject_change_after_inheritance_uncovers_every_qid(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        self.rewrite_pack(subject="Security+")
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        pairs, reasons = self.evidence(ledger, data)
        self.assertEqual(reasons, [])
        self.assertEqual(self.inherited(pairs), set())
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.assertIn("snapshot no longer matches the pack", out)


class WholeRefusalTests(InheritanceCase):
    """M6 tests 6-9, 12 and 13: inheritance is refused entirely."""

    def test_uncertified_pack_is_refused(self):
        prior = self.prior_ledger()
        good = json.loads(self.pack.read_text())

        def tamper(mutator, pattern):
            data = json.loads(json.dumps(good))
            mutator(data)
            self.pack.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(cc.CampaignError, pattern):
                self.inherit(prior)
            self.pack.write_text(json.dumps(good), encoding="utf-8")

        tamper(lambda data: data.pop("certification"), "no certification block")
        tamper(lambda data: data["certification"].__setitem__("certified", False),
               "not certified")
        tamper(lambda data: data["certification"].__setitem__(
                   "critic_contract_version", "2020-01-01"),
               "critic contract version")
        tamper(lambda data: data["certification"].pop("provenance"),
               "no campaign provenance")

    def test_forged_block_without_a_receipt_is_refused(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))
        prior = self.save(ledger, "prior.json")
        with patch.object(cc, "save_ledger", side_effect=OSError("disk full")):
            rc, _out = self.certify(prior)
        self.assertEqual(rc, 2)
        # The block is genuinely fresh; only the ledger's receipt is missing.
        self.assertTrue(pack_cert.certification_fresh(
            json.loads(self.pack.read_text())))
        self.assertNotIn("issuance_receipts", json.loads(prior.read_text()))
        with self.assertRaisesRegex(cc.CampaignError, "no issuance receipt"):
            self.inherit(prior)

    def test_tampered_header_is_refused(self):
        prior = self.prior_ledger()
        data = json.loads(self.pack.read_text())
        data["certification"]["blocking_count"] = 1
        self.pack.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "does not match the prior"):
            self.inherit(prior)

    def test_rewritten_stamp_stays_uncovered_via_the_full_hash(self):
        prior = self.prior_ledger()
        questions = json.loads(json.dumps(QUESTIONS))
        questions[0]["prompt"] += " revised"
        self.rewrite_pack(questions=questions)
        data = json.loads(self.pack.read_text())
        forged = pack_cert.question_content_hash(data["questions"][0], data)
        data["certification"]["question_stamps"]["q1"] = forged
        self.pack.write_text(json.dumps(data), encoding="utf-8")
        # A stamp rewritten only on the pack cannot equal the receipt's stamp.
        only_pack = self.inherit(prior)
        self.assertEqual(only_pack["inheritance"]["inherited_qids"], ["q2"])
        # Rewrite the prior receipt to the same forged stamp as well: only the
        # full-dict hash rule can still leave q1 uncovered.
        value = json.loads(prior.read_text())
        value["issuance_receipts"][-1]["question_stamps"]["q1"] = forged
        prior.write_text(json.dumps(value), encoding="utf-8")
        ledger = self.inherit(prior)
        self.assertEqual(ledger["inheritance"]["inherited_qids"], ["q2"])
        self.assertEqual(ledger["inheritance"]["inheritance_recheck_qids"],
                         ["q1"])

    def test_frozen_input_drift_is_refused(self):
        prior = self.prior_ledger()
        self.rewrite_pack(lint_waivers=[{"rule": "L1", "qid": "q1"}])
        with self.assertRaisesRegex(cc.CampaignError, "waivers"):
            self.inherit(prior)
        self.rewrite_pack(lint_waivers=[])
        (self.source_root / "chapter.txt").write_text(
            "edited source text", encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "grounding"):
            self.inherit(prior)
        (self.source_root / "chapter.txt").write_text(
            "original source text", encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "critic_contract"):
            self.inherit(prior, verifier_profile="claude-opus-high")
        drifted = verifier_profiles.VerifierProfile(
            "codex-terra-high", "codex", "other-model", "high")
        with patch.dict(verifier_profiles.PROFILES, {"codex-terra-high": drifted}):
            with self.assertRaisesRegex(cc.CampaignError, "critic_contract"):
                self.inherit(prior)
        self.rewrite_pack(pack_id="other-pack")
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            self.inherit(prior)
        self.rewrite_pack(pack_id="ch01")
        other_course = self.root / "course-b"
        other_course.mkdir()
        other_pack = other_course / self.pack.name
        other_pack.write_text(self.pack.read_text(encoding="utf-8"),
                               encoding="utf-8")
        (other_course / "_course.json").write_text(
            (self.course / "_course.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError, "pack_identity"):
            ci.inherit_ledger(other_pack, prior)

    def test_inheriting_from_an_inherited_ledger_is_refused(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        path = self.save(ledger, "inherited.json")
        with self.assertRaisesRegex(cc.CampaignError,
                                    "cannot be inherited again"):
            self.inherit(path)

    def test_legacy_v1_prior_is_refused(self):
        prior = self.prior_ledger()
        value = json.loads(prior.read_text())
        value["snapshot"]["snapshot_version"] = 1
        payload = {key: value_snapshot
                   for key, value_snapshot in value["snapshot"].items()
                   if key != "fingerprint"}
        value["snapshot"]["fingerprint"] = cs._digest(payload)
        legacy = self.root / "legacy.json"
        legacy.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(cc.CampaignError,
                                    "current campaign snapshot"):
            self.inherit(legacy)


class RecomputeTrustTests(InheritanceCase):
    """M6 tests 10 and 11: credit is recomputed, never read from the ledger."""

    def test_inherited_qid_changed_in_a_round_loses_its_credit(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        questions = json.loads(json.dumps(QUESTIONS))
        questions[0]["prompt"] += " revised"
        self.rewrite_pack(questions=questions)
        revised = self.snapshot()
        cc.begin_remediation(ledger, revised, ["q1"])
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        pairs, reasons = self.evidence(ledger, data)
        self.assertEqual(reasons, [])
        self.assertNotIn(("q1", revised["question_hashes"]["q1"]), pairs)
        self.assertEqual(self.inherited(pairs),
                         {("q2", ledger["snapshot"]["question_hashes"]["q2"])})
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.recheck(ledger, revised, ["q1"])
        path = self.save(ledger)
        rc, out = self.certify(path)
        self.assertEqual(rc, 0, out)
        provenance = json.loads(
            self.pack.read_text())["certification"]["provenance"]
        self.assertEqual(provenance["remediation_qids"], ["q1"])
        self.assertEqual(provenance["inheritance"]["inherited_count"], 1)

    def test_hand_edited_inherited_qids_give_no_credit(self):
        prior = self.prior_ledger()
        questions = json.loads(json.dumps(QUESTIONS))
        questions[0]["prompt"] += " revised"
        self.rewrite_pack(questions=questions)
        ledger = self.inherit(prior)
        ledger["inheritance"]["inherited_qids"] = ["q1", "q2"]
        path = self.save(ledger)
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        pairs, _reasons = self.evidence(cc.load_ledger(path), data)
        self.assertNotIn("q1", {qid for qid, _hash in pairs})
        rc, out = self.certify(path)
        self.assertEqual(rc, 2)
        self.assertIn("q1", out)

    def test_edited_embedded_prior_gives_no_credit(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        value = json.loads(json.dumps(ledger))
        value["inheritance"]["prior_ledger"]["issuance_receipts"][-1][
            "certification_header_digest"] = "sha256:" + "0" * 64
        path = self.root / "tampered.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        pairs, reasons = self.evidence(cc.load_ledger(path), data)
        self.assertTrue(reasons)
        self.assertEqual(self.inherited(pairs), set())
        rc, _out = self.certify(path)
        self.assertEqual(rc, 2)


class EvidenceSourceTests(InheritanceCase):
    """M6 tests 14 and 15: the inherited source's pack requirement."""

    def test_missing_pack_refuses_inherited_evidence(self):
        prior = self.prior_ledger()
        ledger = self.inherit(prior)
        profile = ledger["snapshot"]["critic_contract"]["profile"]
        pairs, reasons = ce.evidence_sources(ledger, profile=profile)
        self.assertTrue(reasons)
        self.assertIn("requires the pack", reasons[0])
        self.assertEqual(pairs, {})
        self.assertFalse(cc.certification_eligibility(
            ledger, current_snapshot=ledger["snapshot"])[0])
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        eligible, reasons = cc.certification_eligibility(
            ledger, current_snapshot=ledger["snapshot"], data=data)
        self.assertTrue(eligible, reasons)

    def test_census_backed_ledger_without_inheritance_is_unchanged(self):
        snapshot = self.snapshot()
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))
        data = json.loads(self.pack.read_text(encoding="utf-8"))
        profile = snapshot["critic_contract"]["profile"]
        baseline = ce.evidence_sources(ledger, profile=profile)
        self.assertEqual(
            ce.evidence_sources(ledger, profile=profile, data=data), baseline)
        self.assertEqual(cc.certification_eligibility(
            ledger, current_snapshot=snapshot), (True, []))
        self.assertEqual(cc.certification_eligibility(
            ledger, current_snapshot=snapshot, data=data), (True, []))


if __name__ == "__main__":
    unittest.main()
