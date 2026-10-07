"""Combined campaign lineage workflow tests (M7a).

Covers end-to-end combinations of inheritance, chained remediation rounds,
quarantine, and bundler admission gates.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_pack_assets as bpa
import campaign_finalize as cf
import campaign_inheritance as ci
import campaign_quarantine as cq
import certification_campaign as cc
import install_gate as ig
import pack_quarantine as pq
import verify_pack as vp

QUESTIONS = [
    {
        "id": "q1",
        "type": "multiple_choice",
        "topic": "math",
        "exam_area": "algebra",
        "difficulty": "easy",
        "prompt": "What is 1+1?",
        "options": ["2", "3", "4", "5"],
        "answer": 0,
        "explanation": "One plus one is two.",
    },
    {
        "id": "q2",
        "type": "multiple_choice",
        "topic": "math",
        "exam_area": "algebra",
        "difficulty": "easy",
        "prompt": "What is 2+2?",
        "options": ["4", "5", "6", "7"],
        "answer": 0,
        "explanation": "Two plus two is four.",
    },
    {
        "id": "q3",
        "type": "multiple_choice",
        "topic": "math",
        "exam_area": "algebra",
        "difficulty": "easy",
        "prompt": "What is 3+3?",
        "options": ["6", "7", "8", "9"],
        "answer": 0,
        "explanation": "Three plus three is six.",
    },
]

CLEAN_LAYER_A = {"live": [], "waived": [], "hygiene": []}


class LineageWorkflowCase(unittest.TestCase):
    """Fixture with a valid course, grounding, and lint-clean pack."""

    def setUp(self) -> None:
        """Set up temporary directory and fixture structure."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_root = self.root / "private-source"
        self.source_root.mkdir()
        (self.source_root / "chapter.txt").write_text(
            "Source text for grounding.", encoding="utf-8"
        )
        self.packs_root = self.root / "question-packs"
        self.course = self.packs_root / "course-a"
        self.course.mkdir(parents=True)
        self.pack = self.course / "ch01.json"
        self.destination = self.root / "Resources"
        self.write_course()
        self.write_pack()

    def tearDown(self) -> None:
        """Clean up temporary directory."""
        self._tmp.cleanup()

    def write_course(self, course_dir: Path | None = None) -> None:
        """Write course metadata with syllabus and grounding."""
        target = self.course if course_dir is None else course_dir
        (target / "_course.json").write_text(
            json.dumps(
                {
                    "id": "course-a",
                    "name": "Course A",
                    "syllabus": {
                        "source": {"kind": "syllabus", "title": "Math syllabus"},
                        "areas": [
                            {"id": "algebra", "name": "Algebra", "weight": 100},
                        ],
                    },
                    "grounding": {
                        "text_root": str(self.source_root),
                        "packs": {
                            "ch01.json": "chapter.txt",
                            "clean.json": "chapter.txt",
                        },
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def write_pack(
        self,
        pack_path: Path | None = None,
        questions: list[dict] | None = None,
        min_count: int = 2,
        **overrides: Any,
    ) -> Path:
        """Write a lint-clean pack file."""
        target = self.pack if pack_path is None else pack_path
        q_list = copy.deepcopy(QUESTIONS) if questions is None else questions
        payload = {
            "subject": "Math",
            "title": "Lineage fixture",
            "version": 1,
            "pack_id": target.stem,
            "questions": q_list,
            "coverage_blueprint": [
                {"topic": "math", "area": "algebra", "min": min_count},
            ],
        }
        payload.update(overrides)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target

    def certify(self, pack_path: Path, ledger_path: Path) -> tuple[int, str]:
        """Certify the pack through certify_campaign with Layer A patched clean."""
        with patch.object(vp, "run_layer_a", return_value=dict(CLEAN_LAYER_A)):
            return cf.certify_campaign(pack_path, ledger_path)

    def clean_census(self, snapshot: dict) -> dict:
        """Return a clean discovery census covering snapshot questions."""
        return {
            "snapshot_fingerprint": snapshot["fingerprint"],
            "reviewer": snapshot["critic_contract"]["profile"],
            "complete": True,
            "examined_qids": list(snapshot["question_ids"]),
            "findings": [],
            "errors": [],
        }

    def prior_ledger(self, name: str = "prior.json") -> Path:
        """Build and certify a prior ledger, returning its path."""
        snapshot = cc.build_snapshot(self.pack)
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))
        path = self.root / name
        cc.save_ledger(path, ledger)
        rc, out = self.certify(self.pack, path)
        self.assertEqual(rc, 0, out)
        return path

    def targeted_recheck_wrapper(
        self, snapshot: dict, target_qids: list[str]
    ) -> dict:
        """Return a passing targeted recheck wrapper for the target qids."""
        report = {
            "ready": False,
            "outcome": "review_ok",
            "partial": True,
            "layer_a": {"live": []},
            "layer_c": {
                "live": [],
                "errors": [],
                "coverage_gaps": [],
                "questions_unchecked": 0,
                "total": len(target_qids),
            },
        }
        return {
            "schema_version": cc.HYBRID_JSON_SCHEMA_VERSION,
            "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "target_qids": list(target_qids),
            "advisory": {"exit_code": 3, "report": report},
            "verifier": {"exit_code": 3, "report": report},
            "exit_code": 3,
        }


class CombinedLineageWorkflowTests(LineageWorkflowCase):
    """Combined lineage workflow test suite covering M7a scenarios."""

    def test_inheritance_remediation_quarantine_workflow(self) -> None:
        """Test inheritance followed by remediation, quarantine, and certification."""
        prior_path = self.prior_ledger()
        new_ledger = ci.inherit_ledger(self.pack, prior_path)

        # 1. Remediation round: revise prompt of q1
        pack_data = json.loads(self.pack.read_text(encoding="utf-8"))
        pack_data["questions"][0]["prompt"] = "What is 1+1 revised?"
        self.pack.write_text(json.dumps(pack_data, indent=2), encoding="utf-8")
        revised_snapshot = cc.build_snapshot(self.pack)
        cc.begin_remediation(new_ledger, revised_snapshot, ["q1"])
        recheck_data = self.targeted_recheck_wrapper(revised_snapshot, ["q1"])
        cc.record_hybrid_recheck(new_ledger, recheck_data)

        # 2. Quarantine one question: q3
        pq.quarantine(self.pack, ["q3"], "quarantining q3 for investigation")
        reduced_snapshot = cc.build_snapshot(self.pack)
        cq.begin_quarantine(new_ledger, reduced_snapshot, pack=self.pack)

        # 3. Certify campaign
        ledger_path = self.root / "campaign.json"
        cc.save_ledger(ledger_path, new_ledger)
        rc, out = self.certify(self.pack, ledger_path)
        self.assertEqual(rc, 0, out)

        stamped = json.loads(self.pack.read_text(encoding="utf-8"))
        provenance = stamped["certification"]["provenance"]
        self.assertIn("inheritance", provenance)
        self.assertEqual(provenance["remediation_round"], 1)
        self.assertEqual(provenance["quarantined_qids"], ["q3"])

        # 4. install_gate evaluates pack admission
        gate_no_partial = ig.evaluate(
            self.packs_root, report=lambda _: None, allow_partial=False
        )
        self.assertNotIn(("course-a", "ch01.json"), gate_no_partial.admitted)
        self.assertIn(("course-a", "ch01.json"), gate_no_partial.rejections)

        gate_partial = ig.evaluate(
            self.packs_root, report=lambda _: None, allow_partial=True
        )
        self.assertIn(("course-a", "ch01.json"), gate_partial.admitted)
        self.assertNotIn(("course-a", "ch01.json"), gate_partial.rejections)

        # 5. build_pack_assets refuses without --allow-partial
        bpa_result_no_partial = bpa.build(
            self.packs_root, self.destination, lambda _: None, allow_partial=False
        )
        self.assertEqual(len(bpa_result_no_partial["assets"]), 0)
        self.assertTrue(
            any("partial install" in rej for rej in bpa_result_no_partial["rejections"])
        )

        bpa_result_partial = bpa.build(
            self.packs_root, self.destination, lambda _: None, allow_partial=True
        )
        self.assertEqual(len(bpa_result_partial["assets"]), 1)
        self.assertEqual(bpa_result_partial["rejections"], [])

    def test_refused_order_quarantined_ledger_cannot_be_inherited(self) -> None:
        """A ledger that has (or had) a quarantine cannot be inherited from."""
        snapshot = cc.build_snapshot(self.pack)
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))

        pq.quarantine(self.pack, ["q3"], "quarantine before certification")
        reduced_snapshot = cc.build_snapshot(self.pack)
        cq.begin_quarantine(ledger, reduced_snapshot)

        qledger_path = self.root / "quarantined_campaign.json"
        cc.save_ledger(qledger_path, ledger)
        rc, out = self.certify(self.pack, qledger_path)
        self.assertEqual(rc, 0, out)

        # Attempting inheritance from this quarantined ledger is refused:
        # a) Pack still carries partial_install marker
        with self.assertRaisesRegex(ci.CampaignError, "partially installed pack"):
            ci.inherit_ledger(self.pack, qledger_path)

        # b) Direct check of prior_refusal_reasons confirms quarantine key refuses it
        qledger_data = cc.load_ledger(qledger_path)
        reasons = ci.prior_refusal_reasons(qledger_data, base=reduced_snapshot)
        self.assertTrue(
            any("quarantined campaign cannot be inherited" in r for r in reasons)
        )

        # c) Even with a clean unquarantined pack, inheritance is refused by prior quarantine key
        clean_pack = self.course / "clean.json"
        self.write_pack(clean_pack, questions=copy.deepcopy(QUESTIONS[:2]), min_count=1)
        clean_snapshot = cc.build_snapshot(clean_pack)
        clean_ledger = cc.new_ledger(clean_snapshot)
        cc.record_discovery(clean_ledger, self.clean_census(clean_snapshot))
        clean_ledger_path = self.root / "clean_ledger.json"
        cc.save_ledger(clean_ledger_path, clean_ledger)
        rc, out = self.certify(clean_pack, clean_ledger_path)
        self.assertEqual(rc, 0, out)

        with self.assertRaisesRegex(
            ci.CampaignError, "quarantined campaign cannot be inherited"
        ):
            ci.inherit_ledger(clean_pack, qledger_path)

    def test_strip_attack_refused_by_inheritance_and_collect_packs(self) -> None:
        """Quarantine and certify, then delete marker, sidecar, and quarantined_qids.

        Inheritance from the ledger is refused by the prior quarantine key and
        the receipt header digest mismatch; build_pack_assets.collect_packs
        (no allow_partial) refuses the stripped pack.
        """
        two_questions = copy.deepcopy(QUESTIONS[:2])
        self.write_pack(questions=two_questions, min_count=2)
        snapshot = cc.build_snapshot(self.pack)
        ledger = cc.new_ledger(snapshot)
        cc.record_discovery(ledger, self.clean_census(snapshot))

        # Quarantine q2 and certify
        pq.quarantine(self.pack, ["q2"], "quarantining q2")
        reduced_snapshot = cc.build_snapshot(self.pack)
        cq.begin_quarantine(ledger, reduced_snapshot)
        ledger_path = self.root / "quarantined_campaign.json"
        cc.save_ledger(ledger_path, ledger)
        rc, out = self.certify(self.pack, ledger_path)
        self.assertEqual(rc, 0, out)

        # Strip attack:
        # 1. Delete partial_install marker from pack
        pack_data = json.loads(self.pack.read_text(encoding="utf-8"))
        pack_data.pop("partial_install", None)
        # 2. Delete quarantined_qids key from provenance
        pack_data["certification"]["provenance"].pop("quarantined_qids", None)
        self.pack.write_text(json.dumps(pack_data, indent=2), encoding="utf-8")
        # 3. Delete sidecar
        pq.sidecar_path(self.pack).unlink(missing_ok=True)

        # Attempting inheritance from that ledger is refused by prior quarantine key:
        with self.assertRaisesRegex(
            ci.CampaignError, "quarantined campaign cannot be inherited"
        ):
            ci.inherit_ledger(self.pack, ledger_path)

        # If an attacker tampers with the prior ledger to drop the quarantine key,
        # inheritance is refused by the receipt header digest mismatch:
        tampered_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        tampered_ledger.pop("quarantine", None)
        tampered_ledger["snapshot"] = reduced_snapshot
        tampered_ledger["discoveries"] = [
            {
                "valid": True,
                "reviewer": reduced_snapshot["critic_contract"]["profile"],
                "complete": True,
                "snapshot_fingerprint": reduced_snapshot["fingerprint"],
                "examined_qids": ["q1"],
                "findings": [],
                "errors": [],
                "advisory": False,
            }
        ]
        tampered_path = self.root / "tampered_ledger.json"
        tampered_path.write_text(
            json.dumps(tampered_ledger, indent=2), encoding="utf-8"
        )

        with self.assertRaisesRegex(
            ci.CampaignError,
            "pack's certification block does not match the prior campaign's issuance receipt",
        ):
            ci.inherit_ledger(self.pack, tampered_path)

        # build_pack_assets.collect_packs (no allow_partial) refuses the stripped pack
        assets, rejections = bpa.collect_packs(
            self.packs_root, report=lambda _: None, allow_partial=False
        )
        self.assertEqual(len(assets), 0)
        self.assertTrue(len(rejections) > 0)
        self.assertTrue(any("L23" in r for r in rejections))


if __name__ == "__main__":
    unittest.main()
