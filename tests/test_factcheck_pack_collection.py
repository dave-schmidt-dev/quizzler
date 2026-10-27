"""Batch-collection and CLI tests for ``factcheck_pack``."""
from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.factcheck_pack_fixtures import fc

class CollectFindingsTests(unittest.TestCase):
    """The shared canonical batch loop (FIX A). `run_claude` is mocked — NO real
    LLM or network call happens here."""

    @staticmethod
    def _env(findings: list[dict], checked) -> str:
        inner = json.dumps({"findings": findings, "checked": checked})
        return json.dumps({"type": "result", "result": inner,
                           "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})

    def test_full_coverage_no_errors_is_covered(self):
        qs = [{"id": "q1"}, {"id": "q2"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=2)):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertEqual(res["questions_sent"], 2)
        self.assertTrue(fc.coverage_ok(res))

    def test_coverage_gap_retry_unions_findings_and_recovers(self):
        qs = [{"id": "q1"}, {"id": "q2"}]
        first = self._env([{"qid": "q1"}], checked=1)
        retry = self._env([{"qid": "q2"}], checked=2)
        with patch.object(fc, "run_claude", side_effect=[first, retry]) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual([f["qid"] for f in res["findings"]], ["q1", "q2"])
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertTrue(fc.coverage_ok(res))

    def test_operational_error_is_retried_and_recovered(self):
        qs = [{"id": "q1"}]
        with patch.object(
                fc, "run_claude",
                side_effect=[RuntimeError("transient"), self._env([], checked=1)]) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertTrue(fc.coverage_ok(res))

    def test_persistent_coverage_gap_remains_incomplete_after_one_retry(self):
        qs = [{"id": "q1"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=0)) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(res["coverage_gaps"]), 1)
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_persistent_operational_error_remains_incomplete_after_one_retry(self):
        qs = [{"id": "q1"}]
        with patch.object(fc, "run_claude", side_effect=RuntimeError("down")) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_full_coverage_is_not_retried(self):
        qs = [{"id": "q1"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(fc.coverage_ok(res))

    def test_retry_prompt_contains_json_only_reminder(self):
        """A regression asserts the retry prompt contains the JSON-only reminder."""
        qs = [{"id": "q1"}]
        prompts = []

        def _capture(prompt, model, timeout):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "Malformed non-JSON reply from critic."
            return self._env([], checked=1)

        with patch.object(fc, "run_claude", side_effect=_capture) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(prompts), 2)
        self.assertNotIn(fc.JSON_ONLY_REMINDER, prompts[0])
        self.assertIn(fc.JSON_ONLY_REMINDER, prompts[1])
        self.assertTrue(fc.coverage_ok(res))

    def test_embedded_object_input_returns_findings(self):
        """Embedded-object input returns its findings across sequential responses."""
        qs = [{"id": "q1"}]
        first = "Arbitrary model prose without valid JSON."
        embedded_json = json.dumps({
            "findings": [{
                "qid": "q1",
                "severity": "wrong-answer",
                "issue": "Option B is incorrect",
                "correction": "Use Option A",
                "confidence": "high",
            }],
            "checked": 1,
        })
        second = f"Here is the evaluation:\n{embedded_json}\nSummary complete."
        with patch.object(fc, "run_claude", side_effect=[first, second]) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(res["findings"]), 1)
        self.assertEqual(res["findings"][0]["qid"], "q1")
        self.assertEqual(res["findings"][0]["issue"], "Option B is incorrect")
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertTrue(fc.coverage_ok(res))

    def test_two_malformed_replies_return_incomplete_coverage_and_full_unchecked(self):
        """Two malformed replies return incomplete coverage and the full unchecked count."""
        qs = [{"id": "q1"}, {"id": "q2"}]
        first = "First attempt: arbitrary prose response with no JSON object."
        second = "Second attempt: still just explanatory prose with no JSON object."
        with patch.object(fc, "run_claude", side_effect=[first, second]) as run:
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["questions_unchecked"], 2)
        self.assertEqual(res["findings"], [])
        self.assertFalse(fc.coverage_ok(res))

    test_malformed_retry_prompt_contains_json_only_reminder = test_retry_prompt_contains_json_only_reminder
    test_malformed_retry_with_embedded_object_returns_findings = test_embedded_object_input_returns_findings
    test_two_malformed_replies_return_incomplete_coverage = test_two_malformed_replies_return_incomplete_coverage_and_full_unchecked

    def test_partial_checked_is_a_coverage_gap(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["coverage_gaps"]), 1)
        self.assertIn("checked=1 of 3", res["coverage_gaps"][0])
        self.assertEqual(res["questions_unchecked"], 2)
        self.assertFalse(fc.coverage_ok(res))

    def test_batch_error_recorded_and_not_covered(self):
        qs = [{"id": "q1"}]
        with patch.object(fc, "run_claude", side_effect=RuntimeError("timed out")):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_on_batch_callback_invoked_per_batch(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        seen = []
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            fc.collect_findings(qs, model=None, batch_size=1, timeout=5,
                                on_batch=lambda i, n: seen.append((i, n)))
        self.assertEqual(seen, [(0, 3), (1, 3), (2, 3)])

    def test_non_dict_critic_reply_is_batch_error(self):
        # E-19: a non-dict JSON reply (array) raises ValueError in extract_findings,
        # caught as a batch error → unchecked, coverage_ok False.
        qs = [{"id": "q1"}]
        # envelope whose result is a JSON array (not an object)
        env = json.dumps({"type": "result", "result": "[]",
                          "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=env):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["questions_unchecked"], 1)
        self.assertFalse(fc.coverage_ok(res))

    def test_nan_checked_is_coverage_gap(self):
        # E-20: a critic reply with checked=NaN must produce a coverage gap.
        # Without math.isfinite guard, NaN < n is always False so the batch
        # would appear fully covered — a silent integrity hole.
        qs = [{"id": "q1"}, {"id": "q2"}]
        inner = json.dumps({"findings": [], "checked": float("nan")}, allow_nan=True)
        env = json.dumps({"type": "result", "result": inner,
                          "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=env):
            res = fc.collect_findings(qs, model=None, batch_size=12, timeout=5)
        self.assertEqual(len(res["coverage_gaps"]), 1)
        self.assertIn("non-finite", res["coverage_gaps"][0])
        self.assertEqual(res["questions_unchecked"], 2)
        self.assertFalse(fc.coverage_ok(res))

    def test_subject_reaches_the_prompt_in_both_serial_and_parallel(self):
        # collect_findings threads `subject` into _run_one_batch through TWO
        # independently-written call sites (the serial `for` loop and the
        # ThreadPoolExecutor `submit` loop) — an easy spot for the two to
        # drift apart (e.g. one call site forwarding the positional args in
        # the wrong order) since neither is exercised by a test that inspects
        # the actual prompt text sent to the critic.
        captured = {"serial": [], "parallel": []}

        def _capture(key):
            def _run(prompt, model, timeout):
                captured[key].append(prompt)
                return self._env([], checked=1)
            return _run

        qs = [{"id": "q1"}, {"id": "q2"}]
        with patch.object(fc, "run_claude", side_effect=_capture("serial")):
            fc.collect_findings(qs, model=None, batch_size=1, timeout=5,
                                jobs=1, subject="CISSP")
        with patch.object(fc, "run_claude", side_effect=_capture("parallel")):
            fc.collect_findings(qs, model=None, batch_size=1, timeout=5,
                                jobs=4, subject="CISSP")
        self.assertGreaterEqual(len(captured["serial"]), 2)
        self.assertGreaterEqual(len(captured["parallel"]), 2)
        for prompt in captured["serial"] + captured["parallel"]:
            self.assertIn("CISSP", prompt)
            self.assertNotIn("Security+", prompt)
            self.assertNotIn("SY0-701", prompt)


class TestCollectFindings(unittest.TestCase):
    """Concurrency contract for the batch loop: parallel batches (``jobs > 1``)
    must produce output byte-for-byte identical to the serial run — same findings,
    errors, coverage gaps, and unchecked count — because results are aggregated in
    batch-INDEX order, never completion order. ``run_claude`` is mocked, so NO real
    LLM or network call happens; ordering is driven by keying the mock on prompt
    content (and a small sleep to force out-of-order completion)."""

    @staticmethod
    def _env(findings: list[dict], checked) -> str:
        inner = json.dumps({"findings": findings, "checked": checked})
        return json.dumps({"type": "result", "result": inner,
                           "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})

    @staticmethod
    def _finding(qid: str) -> dict:
        return {"qid": qid, "severity": "nit", "issue": f"issue-{qid}",
                "confidence": "low"}

    def _reply_for(self, qid: str) -> str:
        """Canonical per-batch reply used by the serial/parallel-parity test:
        q1 → one finding, q2 → two findings, q3 → error, q4 → coverage gap,
        q5 → clean. Deterministic (no sleeps)."""
        if qid == "q1":
            return self._env([self._finding("q1")], checked=1)
        if qid == "q2":
            return self._env([self._finding("q2a"), self._finding("q2b")], checked=1)
        if qid == "q4":
            return self._env([self._finding("q4")], checked=0)  # gap: 0 of 1
        if qid == "q5":
            return self._env([], checked=1)
        raise AssertionError(f"unexpected qid {qid!r}")  # pragma: no cover

    def _mixed_side_effect(self, prompt, model, timeout):
        # One-question batches (batch_size=1); key the reply on the qid in the
        # prompt. q3 fails (RuntimeError); every other qid uses _reply_for.
        for qid in ("q1", "q2", "q3", "q4", "q5"):
            if f'"{qid}"' in prompt:
                if qid == "q3":
                    raise RuntimeError("claude exited 1")
                return self._reply_for(qid)
        raise AssertionError("prompt matched no known qid")  # pragma: no cover

    def test_serial_and_parallel_are_identical(self):
        qs = [{"id": f"q{i}"} for i in range(1, 6)]
        with patch.object(fc, "run_claude", side_effect=self._mixed_side_effect):
            serial = fc.collect_findings(qs, model=None, batch_size=1, timeout=5, jobs=1)
        with patch.object(fc, "run_claude", side_effect=self._mixed_side_effect):
            parallel = fc.collect_findings(qs, model=None, batch_size=1, timeout=5, jobs=4)
        for key in ("findings", "errors", "coverage_gaps",
                    "questions_unchecked", "model", "questions_sent"):
            self.assertEqual(serial[key], parallel[key],
                             f"{key} differs between serial and parallel")
        # sanity: the fixture really exercised all three channels
        self.assertEqual([f["qid"] for f in serial["findings"]],
                         ["q1", "q2a", "q2b", "q4"])
        self.assertEqual(len(serial["errors"]), 1)
        self.assertEqual(len(serial["coverage_gaps"]), 1)
        self.assertEqual(serial["questions_unchecked"], 2)  # q3 error + q4 gap

    def _ordering_side_effect(self, prompt, model, timeout):
        # 4 one-question batches. The two EARLIER-index batches (q1, q2) are SLOW,
        # the two LATER (q3, q4) are FAST — so under parallelism the later batches
        # COMPLETE first. Index-order aggregation must still put q1/q2 first.
        #   q1: slow, finding + coverage gap (batch 1/4)
        #   q2: slow, error               (batch 2/4)
        #   q3: fast, finding + coverage gap (batch 3/4)
        #   q4: fast, error               (batch 4/4)
        if '"q1"' in prompt:
            time.sleep(0.30)
            return self._env([self._finding("q1")], checked=0)
        if '"q2"' in prompt:
            time.sleep(0.30)
            raise RuntimeError("slow boom")
        if '"q3"' in prompt:
            return self._env([self._finding("q3")], checked=0)
        if '"q4"' in prompt:
            raise RuntimeError("fast boom")
        raise AssertionError("prompt matched no known qid")  # pragma: no cover

    def test_parallel_aggregates_in_batch_index_order(self):
        qs = [{"id": f"q{i}"} for i in range(1, 5)]
        with patch.object(fc, "run_claude", side_effect=self._ordering_side_effect):
            res = fc.collect_findings(qs, model=None, batch_size=1, timeout=5, jobs=4)
        # findings: q1 (slow, index 0) precedes q3 (fast, index 2)
        self.assertEqual([f["qid"] for f in res["findings"]], ["q1", "q3"])
        # errors: batch 2/4 (slow q2) precedes batch 4/4 (fast q4)
        self.assertEqual(len(res["errors"]), 2)
        self.assertIn("batch 2/4", res["errors"][0])
        self.assertIn("batch 4/4", res["errors"][1])
        # coverage gaps: batch 1/4 (slow q1) precedes batch 3/4 (fast q3)
        self.assertEqual(len(res["coverage_gaps"]), 2)
        self.assertIn("batch 1/4", res["coverage_gaps"][0])
        self.assertIn("batch 3/4", res["coverage_gaps"][1])
        self.assertEqual(res["questions_unchecked"], 4)  # 2 gaps + 2 errors, 1 q each

    def test_failing_batch_counted_serial_and_parallel(self):
        qs = [{"id": "q1"}, {"id": "q2"}]
        for jobs in (1, 4):
            with self.subTest(jobs=jobs):
                with patch.object(fc, "run_claude",
                                  side_effect=RuntimeError("timed out")):
                    res = fc.collect_findings(qs, model=None, batch_size=1,
                                              timeout=5, jobs=jobs)
                self.assertEqual(len(res["errors"]), 2)
                self.assertEqual(res["batch_count"], 2)
                self.assertEqual(res["questions_unchecked"], 2)
                self.assertFalse(fc.coverage_ok(res))

    def test_coverage_gap_recorded_under_parallel(self):
        qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
        # Every batch self-reports checking 0 of its 1 question → a gap each.
        with patch.object(fc, "run_claude", return_value=self._env([], checked=0)):
            res = fc.collect_findings(qs, model=None, batch_size=1, timeout=5, jobs=4)
        self.assertEqual(len(res["coverage_gaps"]), 3)
        for i, g in enumerate(res["coverage_gaps"], start=1):
            self.assertIn(f"batch {i}/3", g)  # aggregated in index order
        self.assertEqual(res["questions_unchecked"], 3)
        self.assertFalse(fc.coverage_ok(res))

    def test_on_batch_monotonic_under_parallel(self):
        qs = [{"id": f"q{i}"} for i in range(1, 5)]
        seen = []
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            fc.collect_findings(qs, model=None, batch_size=1, timeout=5, jobs=4,
                                on_batch=lambda i, n: seen.append((i, n)))
        # Exactly n_batches calls, count climbs 0..n-1 in order (monotonic),
        # total n constant — even though batches finish out of order.
        self.assertEqual(seen, [(0, 4), (1, 4), (2, 4), (3, 4)])

    def test_jobs_greater_than_batch_count_clamps_and_works(self):
        qs = [{"id": "q1"}, {"id": "q2"}]  # only 2 batches
        with patch.object(fc, "run_claude", return_value=self._env([], checked=1)):
            res = fc.collect_findings(qs, model=None, batch_size=1, timeout=5,
                                      jobs=99)  # far more workers than batches
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["coverage_gaps"], [])
        self.assertEqual(res["questions_unchecked"], 0)
        self.assertEqual(res["questions_sent"], 2)
        self.assertTrue(fc.coverage_ok(res))


class MainCLIStrictSubjectTests(unittest.TestCase):
    """2026-08-11: factcheck_pack.py's own CLI (main()) independently
    re-implements the SAME --strict source_directive/subject asymmetry as
    verify_pack._layer_c_inputs — it is NOT shared code, so the two can drift
    apart. --dry-run prints the built prompt and returns before any LLM call
    or provider preflight, so this needs no mocking at all."""

    def _dry_run_prompt(self, pack: dict, extra_argv: list[str]) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pack.json"
            p.write_text(json.dumps(pack))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = fc.main([str(p), "--dry-run"] + extra_argv)
            self.assertEqual(rc, 0)
            return buf.getvalue()

    def test_strict_drops_source_directive_but_keeps_subject(self):
        pack = {
            "pack_id": "t", "subject": "CISSP",
            "source_directive": "Trust the author's framing.",
            "questions": [{"id": "q1", "prompt": "p"}],
        }
        lenient = self._dry_run_prompt(pack, [])
        strict = self._dry_run_prompt(pack, ["--strict"])
        self.assertIn("Trust the author's framing.", lenient)
        self.assertIn("CISSP", lenient)
        self.assertNotIn("Trust the author's framing.", strict)
        self.assertIn("CISSP", strict)

    def test_strict_without_subject_falls_back_to_default_not_security_plus(self):
        pack = {
            "pack_id": "t",
            "source_directive": "Trust the author's framing.",
            "questions": [{"id": "q1", "prompt": "p"}],
        }
        strict = self._dry_run_prompt(pack, ["--strict"])
        self.assertNotIn("Trust the author's framing.", strict)
        self.assertIn(fc.DEFAULT_SUBJECT, strict)
        self.assertNotIn("Security+", strict)
        self.assertNotIn("SY0-701", strict)


if __name__ == "__main__":
    unittest.main()
