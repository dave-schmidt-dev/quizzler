"""Tests for the evidence-only certification campaign ledger.

These tests never call a model CLI.  They exercise only snapshot and ledger
contracts so discovery coordination cannot accidentally become a second
certification authority.
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
