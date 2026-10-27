"""Unit tests for ``scripts/verify_pack.py`` — the pack-readiness gate.

verify_pack runs Layer A (structure) + Layer C (factual LLM critic) as one hard
gate. Layer C's LLM subprocess is NON-deterministic and costs money, so every
test here MOCKS ``factcheck_pack.run_claude`` (and ``shutil.which`` so the gate
believes the ``claude`` CLI is present) to return a canned ``claude
--output-format json`` envelope. NO real LLM or network call happens.

Throw-away temp packs are written per-test (mirroring test_build_manifest.py) so
the real ``question-packs/`` tree is never touched.

Run from the project root::

    python3 -m unittest tests.test_verify_pack -v
"""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.verify_pack_fixtures import (
    CLEAN_Q,
    PROJECT_ROOT,
    SCRIPT_PATH,
    _Base,
    envelope,
    fc,
    vp,
)

class RetiredCliTests(unittest.TestCase):
    def test_direct_cli_fails_fast_to_hybrid(self):
        result = subprocess.run(
            ["python3", str(SCRIPT_PATH), "question-packs/cissp/cissp-core.json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("internal library primitive", result.stderr)
        self.assertIn("hybrid_verify.py", result.stderr)


class CleanPackTests(_Base):
    def test_clean_pack_no_findings_is_review_passed_not_ready(self):
        pack = self.write_pack()
        original_text = pack.read_text()
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 3)
        self.assertIn("REVIEW PASSED", out)
        self.assertNotIn("PACK READY", out)
        self.assertEqual(pack.read_text(), original_text)
        self.assertIn("Layer A (structure): clean", out)
        self.assertIn("Layer C (factual): clean", out)

    def test_a_clean_review_never_writes_a_certification(self):
        # INV-7: only hybrid_verify --certify-campaign certifies; main only reviews.
        pack = self.write_pack()
        rc, _out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(pack.read_text()))


class LayerATests(_Base):
    def test_layer_a_critical_blocks(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        pack = self.write_pack(questions=[dirty])
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer-A", out)
        self.assertIn("L12", out)


class LayerCTests(_Base):
    FINDING = {"qid": "q1", "severity": "wrong-answer",
               "issue": "two plus two is five, not four",
               "correction": "the answer is four", "confidence": "high"}

    def test_layer_c_finding_blocks(self):
        pack = self.write_pack()
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer C (factual): 1 live finding", out)
        self.assertIn("PACK NOT READY: 0 Layer-A + 1 blocking Layer-C finding(s)", out)

    def test_layer_c_finding_waived_is_review_passed(self):
        pack = self.write_pack(factcheck_waivers=[
            {"qid": "q1", "reason": "intentional trick distractor; verified by author"},
        ])
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 3)
        self.assertIn("REVIEW PASSED", out)
        # The waiver is blanket (qid-only), so FIX G adds a non-blocking hygiene
        # nudge alongside the waive — the review still passes.
        self.assertIn(
            "Layer C (factual): clean (1 waived, 1 hygiene, "
            "graded as: Verify Fixture)", out)


class LayerCProgressTests(_Base):
    """The single-critic path must expose the same INV-1 event contract as a panel."""

    def test_run_layer_c_forwards_batch_progress_and_lifecycle_events(self):
        pack = self.write_pack()
        events = []

        def fake_collect(questions, model, batch_size, timeout, **kwargs):
            self.assertIsNotNone(kwargs["on_batch"])
            kwargs["on_batch"](0, 2)
            kwargs["on_batch"](1, 2)
            return {"findings": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0, "model": "observed-model",
                    "batch_count": 2,
                    "questions_sent": len(questions),
                    "questions_graded": len(questions)}

        with patch.object(fc, "collect_findings", side_effect=fake_collect), \
             patch.object(vp.critic_providers, "preflight", return_value=None):
            result = vp.run_layer_c(
                pack, "requested-model", 1, 30,
                on_event=lambda kind, **info: events.append((kind, info)))

        self.assertEqual([kind for kind, _info in events],
                         ["pass_start", "batch", "batch", "pass_done"])
        self.assertEqual(events[0][1],
                         {"label": fc.DEFAULT_PROVIDER, "index": 0, "total": 1})
        self.assertEqual([event[1]["i"] for event in events[1:3]], [0, 1])
        self.assertEqual([event[1]["n"] for event in events[1:3]], [2, 2])
        self.assertEqual(events[-1][1],
                         {"label": fc.DEFAULT_PROVIDER, "findings": 0,
                          "errors": 0, "model": "observed-model"})
        self.assertEqual(result["model"], "observed-model")

    def test_run_layer_c_uses_collector_batch_count_without_rebatching(self):
        pack = self.write_pack()

        def fake_collect(questions, model, batch_size, timeout, **kwargs):
            return {
                "findings": [], "errors": ["batch 1/1 failed"],
                "coverage_gaps": [], "questions_unchecked": len(questions),
                "model": None, "batch_count": 1,
                "questions_sent": len(questions),
                "questions_graded": len(questions),
            }

        with patch.object(fc, "collect_findings", side_effect=fake_collect), \
             patch.object(fc, "batched", side_effect=AssertionError("rebatched")), \
             patch.object(vp.critic_providers, "preflight", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "every Layer-C batch failed"):
                vp.run_layer_c(pack, "requested-model", 12, 30)

    def test_json_mode_keeps_progress_events_off_stdout(self):
        pack = self.write_pack()
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", return_value=envelope([])), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"), \
             redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--model", "opus", "--json"])

        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(out.getvalue())["exit_code"], 3)
        self.assertEqual(err.getvalue(), "")


class NoFactcheckTests(_Base):
    def test_no_factcheck_skips_layer_c_and_prints_note(self):
        pack = self.write_pack()
        # No run_claude mock needed — Layer C must not run at all. If it did, the
        # real `claude` CLI absence would surface; we assert the skip note instead.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        # FIX D: --no-factcheck must NEVER return 0 (a CI `verify_pack
        # --no-factcheck && deploy` would otherwise ship an unfactchecked pack).
        # A clean structure-only run returns the distinct exit code 3.
        self.assertEqual(rc, 3)
        # Structure-only must NOT claim full readiness — it never ran Layer C.
        self.assertIn("NOT certified ready", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())
        self.assertIn("structure-only (Layer C skipped) — this is NOT the full readiness gate.",
                      out.getvalue())

    def test_no_factcheck_still_blocks_on_layer_a(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")
        pack = self.write_pack(questions=[dirty])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())


class LayerAHygieneTests(_Base):
    def test_stale_lint_waiver_is_hygiene_not_blocking(self):
        # FIX E: a stale lint_waiver (matches no finding) is a list-rot nudge,
        # not a content defect. lint_pack folds it into `violations`, but the
        # readiness gate must NOT block an otherwise-clean pack on it — it is
        # surfaced as non-blocking hygiene, exactly like Layer C's own hygiene.
        pack = self.write_pack(lint_waivers=[
            {"rule": "L10", "qid": "ghost", "reason": "no longer needed"},
        ])
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 3)
        self.assertIn("REVIEW PASSED", out)
        self.assertIn("Layer A (structure): clean", out)
        # The stale waiver is still surfaced (just not blocking).
        self.assertIn("hygiene", out)
        self.assertIn("WAIVER", out)


class L23CoverageCriticalTests(_Base):
    """L23 absent-`coverage_blueprint` is CRITICAL and blocks readiness."""

    def test_absent_blueprint_critical_blocks_full_gate(self):
        pack = self.write_pack_without_blueprint()
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("L23", out)

    def test_absent_blueprint_critical_blocks_no_factcheck(self):
        pack = self.write_pack_without_blueprint()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())

    def test_blueprint_undercoverage_is_blocking_critical(self):
        pack = self.write_pack(coverage_blueprint=[{"topic": "unseen-topic", "min": 1}])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())
        self.assertIn("L23", out.getvalue())


class EmptyPackTests(_Base):
    def test_empty_questions_not_ready_full_gate(self):
        # FIX B: a pack with zero questions has nothing for the critic to check;
        # the gate must NOT certify it. Exit 2 (NOT READY), not 0.
        pack = self.write_pack(questions=[])
        rc, _out, err = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("no questions", err)

    def test_empty_questions_not_ready_under_no_factcheck(self):
        # FIX B: the guard also fires under --no-factcheck (where Layer C never
        # loads questions) — an empty pack is never ready, even structure-only.
        pack = self.write_pack(questions=[])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("no questions", err.getvalue())


class LayerCCoverageTests(_Base):
    """FIX A: a Layer-C run that did not actually inspect every question must
    NEVER certify PACK READY. run_claude is mocked — NO real LLM/network."""

    @staticmethod
    def _envelope(findings: list[dict], checked) -> str:
        inner = json.dumps({"findings": findings, "checked": checked})
        return json.dumps({"type": "result", "result": inner,
                           "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})

    def test_partial_coverage_is_not_ready(self):
        # Critic self-reports checked=0 of 1 → coverage gap → NOT READY.
        pack = self.write_pack(questions=[dict(CLEAN_Q)])
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude",
                          return_value=self._envelope([], checked=0)), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack), "--model", "opus"])
        self.assertEqual(rc, 2)
        self.assertIn("coverage incomplete", out.getvalue())
        self.assertIn("1 question(s) unchecked", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())

    def test_one_failed_batch_is_not_ready_not_operational(self):
        # 2 questions @ batch-size 1 → 2 batches. One batch times out, one is
        # clean. A PARTIAL failure must be NOT READY (exit 2), distinct from the
        # all-failed operational error (exit 1). The unchecked batch must not ship.
        q2 = dict(CLEAN_Q)
        q2["id"] = "q2"
        pack = self.write_pack(questions=[dict(CLEAN_Q), q2])
        clean_env = envelope([])  # checked=99 ≥ 1 → no gap on the good batch

        def fake_run_claude(prompt, model, timeout):
            if "q2" in prompt:
                return clean_env
            raise RuntimeError("claude call timed out after 180s")

        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", side_effect=fake_run_claude), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack), "--model", "opus", "--batch-size", "1"])
        self.assertEqual(rc, 2)
        self.assertIn("NOT checked", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())


class SubjectThreadingTests(_Base):
    """2026-08-11: the critic's persona used to be hardcoded to CompTIA
    Security+ (SY0-701) regardless of the pack's own `subject`. Confirms
    verify_pack.main actually reads and forwards it end-to-end, not just that
    build_prompt supports the parameter in isolation."""

    def test_pack_subject_reaches_the_critic_prompt(self):
        pack = self.write_pack(subject="CISSP")
        captured = {}

        def fake_run_claude(prompt, model, timeout):
            captured["prompt"] = prompt
            return envelope([])

        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", side_effect=fake_run_claude), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(pack), "--model", "opus"])
        self.assertEqual(rc, 3)
        self.assertIn("CISSP", captured["prompt"])
        self.assertNotIn("Security+", captured["prompt"])

    def test_missing_pack_subject_does_not_default_to_security_plus(self):
        pack = self.write_pack(subject=None)  # no `subject` key at all
        captured = {}

        def fake_run_claude(prompt, model, timeout):
            captured["prompt"] = prompt
            return envelope([])

        with patch.object(fc, "run_claude", side_effect=fake_run_claude), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                vp.main([str(pack), "--model", "opus"])
        self.assertNotIn("Security+", captured["prompt"])
        self.assertNotIn("SY0-701", captured["prompt"])

    def test_report_names_a_real_pack_subject_not_just_the_default(self):
        # `subject` is mandatory under L29, so no pack that reaches this gate
        # can exercise the DEFAULT_SUBJECT fallback any more; that fallback is
        # covered directly in tests/test_factcheck_pack.py. What remains worth
        # asserting here is that the report prints the pack's own subject
        # rather than a hardcoded course name.
        pack = self.write_pack(subject="CISSP")
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 3)
        self.assertIn("graded as: CISSP", out)
        self.assertNotIn(f"graded as: {fc.DEFAULT_SUBJECT}", out)


class StrictModeSubjectSourceDirectiveTests(_Base):
    """2026-08-11: --strict must drop the pack's `source_directive` (an author
    framing assertion) but KEEP `subject` (basic pack identity, e.g. "CISSP")
    — see verify_pack._layer_c_inputs's docstring/comment. This asymmetry has
    to hold on the Layer-C path (run_layer_c)."""

    def _pack_with_directive_and_subject(self) -> Path:
        return self.write_pack(
            source_directive="Trust the author's framing.", subject="CISSP")

    def test_layer_c_inputs_strict_drops_directive_keeps_subject(self):
        pack = self._pack_with_directive_and_subject()
        # (questions, context_qids, effective_batch, total, source_directive,
        #  source_text, subject)
        lenient = vp._layer_c_inputs(pack, None, False, 12)
        strict = vp._layer_c_inputs(pack, None, True, 12)
        self.assertEqual(lenient[4], "Trust the author's framing.")
        self.assertIsNone(strict[4])
        self.assertEqual(lenient[6], "CISSP")
        self.assertEqual(strict[6], "CISSP")

    def test_run_layer_c_strict_keeps_subject_drops_source_directive(self):
        """Single-critic path: what reaches collect_findings AND what lands
        in the reported result dict."""
        pack = self._pack_with_directive_and_subject()
        captured = {}

        def fake_collect_findings(questions, model, batch_size, timeout, **kw):
            captured.update(kw)
            return {"findings": [], "errors": [], "coverage_gaps": [],
                    "questions_unchecked": 0, "model": "m",
                    "batch_count": 1,
                    "questions_sent": len(questions),
                    "questions_graded": len(questions)}

        with patch.object(fc, "collect_findings", side_effect=fake_collect_findings), \
             patch.object(vp.critic_providers, "preflight", return_value=None):
            lenient_result = vp.run_layer_c(pack, "model", 12, 30, strict=False)
            captured_lenient = dict(captured)
            captured.clear()
            strict_result = vp.run_layer_c(pack, "model", 12, 30, strict=True)

        self.assertEqual(captured_lenient["source_directive"],
                         "Trust the author's framing.")
        self.assertEqual(captured_lenient["subject"], "CISSP")
        self.assertTrue(lenient_result["source_directive_active"])

        self.assertIsNone(captured["source_directive"])
        self.assertEqual(captured["subject"], "CISSP")
        self.assertFalse(strict_result["source_directive_active"])
        self.assertEqual(strict_result["subject"], "CISSP")


class OperationalErrorTests(_Base):
    def test_missing_pack_is_operational_error(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = vp.main([str(self.tmp_path / "does-not-exist.json")])
        self.assertEqual(rc, 1)
        self.assertIn("pack not found", err.getvalue())

    def test_missing_claude_cli_is_operational_error(self):
        pack = self.write_pack()
        err = io.StringIO()
        with patch.object(vp.critic_providers.shutil, "which", return_value=None):
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = vp.main([str(pack)])
        self.assertEqual(rc, 1)
        self.assertIn("claude", err.getvalue())


if __name__ == "__main__":
    unittest.main()
