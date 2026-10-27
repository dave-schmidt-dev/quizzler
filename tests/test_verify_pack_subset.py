"""Severity-gate, subset and targeted-neighborhood tests for ``verify_pack``."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.verify_pack_fixtures import (
    CLEAN_LAYER_A,
    CLEAN_Q,
    CLEAN_Q2,
    _Base,
    fc,
    vp,
)

class SeverityGateTests(_Base):
    """The severity gate (FIX #1 of the 2026-07 hardening): only a wrong-answer OR
    any high-confidence finding BLOCKS; the probabilistic nit/ambiguous tail is
    advisory (the review passes, exit 3). This is the behavior that ended the
    7-run non-convergence."""

    def test_advisory_only_finding_is_review_passed(self):
        adv = {"qid": "q1", "severity": "nit", "issue": "off-axis distractor",
               "correction": "swap it", "confidence": "medium"}
        rc, out, _ = self.run_main([str(self.write_pack())], findings=[adv])
        self.assertEqual(rc, 3)
        self.assertIn("REVIEW PASSED", out)
        self.assertIn("advisory", out)

    def test_high_confidence_nit_is_advisory(self):
        # Terra can be highly confident about a quality nit; confidence alone
        # must not promote it into a certification blocker.
        f = {"qid": "q1", "severity": "nit", "issue": "the NAV acronym is wrong",
             "correction": "Network Allocation Vector", "confidence": "high"}
        rc, out, _ = self.run_main([str(self.write_pack())], findings=[f])
        self.assertEqual(rc, 3)
        self.assertIn("REVIEW PASSED", out)
        self.assertIn("advisory", out)

    def test_strict_makes_advisory_block(self):
        adv = {"qid": "q1", "severity": "ambiguous", "issue": "two defensible answers",
               "correction": "tighten stem", "confidence": "low"}
        rc, out, _ = self.run_main([str(self.write_pack()), "--strict"], findings=[adv])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)

    def test_mislabeled_finding_fails_safe(self):
        # A garbled severity/confidence must BLOCK (fail-safe), never slip to
        # advisory — the gate trusts these labels, so unknown = most severe.
        f = {"qid": "q1", "severity": "totally-bogus", "issue": "x",
             "correction": "y", "confidence": "who-knows"}
        rc, _out, _ = self.run_main([str(self.write_pack())], findings=[f])
        self.assertEqual(rc, 2)


class SubsetTests(_Base):
    """--only re-checks a subset for shrinking confirmation runs but NEVER certifies
    the whole pack (FIX #2): a clean subset exits 3, not 0."""

    def _pack(self):
        return self.write_pack(questions=[dict(CLEAN_Q), dict(CLEAN_Q2)])

    def test_clean_subset_is_not_certification(self):
        pack = self._pack()
        original_text = pack.read_text()
        rc, out, _ = self.run_main([str(pack), "--only", "q1"], findings=[])
        self.assertEqual(rc, 3)  # exit 3, NOT 0 — mirrors --no-factcheck
        self.assertIn("REVIEW PASSED", out)
        self.assertNotIn("PACK READY", out)
        self.assertEqual(pack.read_text(), original_text)

    def test_blocking_in_subset_still_blocks(self):
        f = {"qid": "q1", "severity": "wrong-answer", "issue": "x",
             "correction": "y", "confidence": "high"}
        rc, out, _ = self.run_main([str(self._pack()), "--only", "q1"], findings=[f])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)

    def test_unmatched_only_is_error(self):
        rc, _out, err = self.run_main([str(self._pack()), "--only", "nonesuch"], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("none of the --only ids matched", err)

    def test_partially_unknown_only_ids_fail_closed(self):
        rc, _out, err = self.run_main(
            [str(self._pack()), "--only", "q1,nonesuch"], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("unknown --only question id(s): nonesuch", err)


class TargetedNeighborhoodTests(_Base):
    """``--only`` must stay cheap without claiming full duplicate coverage."""

    def _large_pack(self) -> Path:
        questions = [dict(CLEAN_Q)]
        for index in range(1, vp.TARGETED_CONTEXT_LIMIT + 5):
            question = dict(CLEAN_Q2)
            question["id"] = f"q{index + 1}"
            question["topic"] = "other"
            question["prompt"] = f"Unrelated practice item {index}."
            question["explanation"] = f"Unrelated explanation {index}."
            questions.append(question)
        return self.write_pack(questions=questions)

    def test_targets_forward_with_bounded_deterministic_context(self):
        pack = self._large_pack()
        first = vp._layer_c_inputs(pack, {"q1"}, False, 12)
        second = vp._layer_c_inputs(pack, {"q1"}, False, 12)
        questions, context_qids, effective_batch, total, *_rest = first

        self.assertEqual([q["id"] for q in questions],
                         [q["id"] for q in second[0]])
        self.assertEqual(context_qids, second[1])
        self.assertEqual([q["id"] for q in questions],
                         ["q1"] + [f"q{i}" for i in range(2, 26)])
        self.assertEqual(questions[0]["id"], "q1")
        self.assertNotIn("q1", context_qids)
        self.assertEqual(len(context_qids), vp.TARGETED_CONTEXT_LIMIT)
        self.assertEqual(len(questions), 1 + vp.TARGETED_CONTEXT_LIMIT)
        self.assertEqual(effective_batch, len(questions))
        self.assertEqual(total, 1)

    def test_targeted_run_forwards_context_to_the_critic(self):
        pack = self._large_pack()
        captured = {}

        def fake_collect(questions, model, batch_size, timeout, **kwargs):
            captured["ids"] = [q["id"] for q in questions]
            captured["context_qids"] = kwargs["context_qids"]
            return {"findings": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0, "model": "m",
                    "batch_count": 1,
                    "questions_sent": len(questions), "questions_graded": 1}

        with patch.object(fc, "collect_findings", side_effect=fake_collect), \
             patch.object(vp.critic_providers, "preflight", return_value=None):
            vp.run_layer_c(pack, "model", 12, 30, only={"q1"})

        self.assertEqual(captured["ids"][0], "q1")
        self.assertEqual(len(captured["context_qids"]), vp.TARGETED_CONTEXT_LIMIT)
        self.assertNotIn("q1", captured["context_qids"])

    def test_multiple_targets_grade_all_targets_with_bounded_deterministic_context(self):
        """A multi-ID recheck grades every target, never its ride-along context.

        This is the regression boundary for the campaign runner: two edited IDs
        must remain two graded IDs, while the comparison neighborhood stays
        bounded and reproducible.  It must also retain the ordinary ``--only``
        no-stamp rule.
        """
        pack = self._large_pack()
        targets = {"q1", "q20"}
        first = vp._layer_c_inputs(pack, targets, False, 12)
        second = vp._layer_c_inputs(pack, targets, False, 12)
        questions, context_qids, effective_batch, total, *_rest = first
        selected_ids = [q["id"] for q in questions]

        self.assertEqual(selected_ids, [q["id"] for q in second[0]])
        self.assertEqual(context_qids, second[1])
        self.assertTrue(targets.issubset(selected_ids))
        self.assertEqual([qid for qid in selected_ids if qid in targets],
                         ["q1", "q20"], "payload preserves pack order")
        self.assertTrue(targets.isdisjoint(context_qids))
        self.assertEqual(len(context_qids), vp.TARGETED_CONTEXT_LIMIT)
        self.assertEqual(len(questions), len(targets) + vp.TARGETED_CONTEXT_LIMIT)
        self.assertEqual(effective_batch, len(questions))
        self.assertEqual(total, len(targets))

        captured = {}

        def fake_collect(sent_questions, model, batch_size, timeout, **kwargs):
            captured["ids"] = [q["id"] for q in sent_questions]
            captured["context_qids"] = kwargs["context_qids"]
            return {"findings": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0, "model": "m",
                    "batch_count": 1,
                    "questions_sent": len(sent_questions),
                    "questions_graded": len(targets)}

        with patch.object(fc, "collect_findings", side_effect=fake_collect), \
             patch.object(vp.critic_providers, "preflight", return_value=None):
            layer_c = vp.run_layer_c(pack, "model", 12, 30, only=targets)

        self.assertEqual(captured["ids"], selected_ids)
        self.assertEqual(captured["context_qids"], context_qids)
        self.assertEqual(layer_c["questions_graded"], len(targets))

        original_text = pack.read_text()
        with patch.object(vp, "run_layer_a", return_value=CLEAN_LAYER_A), \
             patch.object(vp, "run_layer_c", return_value=layer_c), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = vp.main([str(pack), "--only", ",".join(sorted(targets))])

        self.assertEqual(rc, 3)
        self.assertEqual(pack.read_text(), original_text)
        self.assertNotIn("certification", json.loads(pack.read_text()))

    def test_full_pass_keeps_all_questions_and_requested_batch_size(self):
        pack = self._large_pack()
        questions, context_qids, effective_batch, total, *_rest = (
            vp._layer_c_inputs(pack, None, False, 7))
        self.assertEqual(len(questions), vp.TARGETED_CONTEXT_LIMIT + 5)
        self.assertIsNone(context_qids)
        self.assertEqual(effective_batch, 7)
        self.assertIsNone(total)

    def test_layer_c_inputs_rejects_unknown_target(self):
        with self.assertRaisesRegex(ValueError, "unknown --only question id"):
            vp._layer_c_inputs(self._large_pack(), {"missing"}, False, 12)


if __name__ == "__main__":
    unittest.main()
