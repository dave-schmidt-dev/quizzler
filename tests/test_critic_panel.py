"""Tests for the multi-provider critic panel and the verify_pack seam.

The single most important test in this file is
``MergeFindingsTests.test_a_finding_from_one_pass_alone_survives_the_merge``. The
whole panel is worthless — actively harmful, in fact — if the merge ever becomes
a majority vote, because the defect it exists to catch is the finding only
one model was sharp enough to raise.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.critic_provider_fixtures import (
    CLEAN_Q,
    NATIVE_METADATA,
    _critic_json,
    _finding,
    cp,
    fc,
    pack_cert,
    panel_mod,
    vp,
)


class RunCriticSeamTests(unittest.TestCase):
    def test_claude_path_unwraps_the_envelope_and_reads_model_usage(self):
        stdout = json.dumps({
            "type": "result", "result": _critic_json([]),
            "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=stdout):
            reply = fc.run_critic("prompt", "claude-sonnet-5", 5)
        self.assertEqual(reply.provider, "claude")
        self.assertEqual(reply.model, "claude-sonnet-5")
        self.assertIn("findings", reply.text)

    def test_claude_rejects_a_variant_rather_than_ignoring_it(self):
        with patch.object(fc, "run_claude") as claude:
            with self.assertRaises(ValueError) as ctx:
                fc.run_critic("prompt", "claude-sonnet-5", 5, variant="max")
        claude.assert_not_called()
        self.assertIn("--variant", str(ctx.exception))

    def test_a_non_claude_provider_never_touches_the_claude_cli(self):
        def _boom(*a, **k):
            raise AssertionError("run_claude must not be called for another provider")

        with patch.object(fc, "run_claude", side_effect=_boom), \
             patch.object(cp, "run",
                          return_value=cp.CriticReply(_critic_json([]),
                                                      None,
                                                      "opencode")) as ran:
            reply = fc.run_critic("prompt", None, 5, provider="opencode")
        ran.assert_called_once()
        self.assertEqual(reply.provider, "opencode")

    def test_collect_findings_rejects_an_unknown_provider_up_front(self):
        """Fail once, not once per batch.

        Without the up-front check a typo produces N identical per-batch errors
        that look like N transient failures.
        """
        with self.assertRaises(ValueError):
            fc.collect_findings([{"id": "q1"}], None, 12, 5, provider="nope")

    def test_collect_findings_records_the_provider_it_used(self):
        with patch.object(cp, "run",
                          return_value=cp.CriticReply(_critic_json([], checked=1),
                                                      "served-model", "opencode")):
            result = fc.collect_findings([{"id": "q1"}], "ds-flash", 12, 5,
                                         provider="opencode")
        self.assertEqual(result["provider"], "opencode")
        self.assertEqual(result["model"], "served-model")
        self.assertEqual(result["errors"], [])


class ParsePanelTests(unittest.TestCase):
    def test_provider_equals_model_syntax_survives_colons_in_model_ids(self):
        """`=` separates, not `:` — some gateway model ids contain colons."""
        passes = panel_mod.parse_panel(
            "opencode=deepseek-v4-flash-free,openai-compatible=gw:v2")
        self.assertEqual([p.provider for p in passes],
                         ["opencode", "openai-compatible"])
        self.assertEqual(passes[1].model, "gw:v2")

    def test_a_bare_provider_uses_its_default_model(self):
        passes = panel_mod.parse_panel("claude,opencode")
        self.assertEqual(passes[0].model, fc.DEFAULT_CLAUDE_MODEL)
        self.assertIsNone(passes[1].model)  # resolved by the provider spec

    def test_a_duplicate_pass_is_rejected(self):
        """Two identical passes are correlated repetition dressed as consensus.

        Worse than useless: the duplicate would raise `agreement` to 2 on every
        finding, making one model's opinion read as corroborated.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("opencode=ds-flash,opencode=ds-flash")
        self.assertIn("INDEPENDENT", str(ctx.exception))

    def test_a_panel_of_one_is_rejected(self):
        """`--panel opencode` would certify as `external-layer-c-panel`.

        That name is read at the install gate as "several independent models
        looked". A single entry makes it mintable by exactly the single-critic
        pass whose false negative this module exists to stop — the label would
        promise corroboration that never happened.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("opencode")
        self.assertIn("at least 2", str(ctx.exception))

    def test_unknown_provider_and_empty_spec_are_rejected(self):
        with self.assertRaises(ValueError):
            panel_mod.parse_panel("deepsek")
        with self.assertRaises(ValueError):
            panel_mod.parse_panel("  , ")


class MergeFindingsTests(unittest.TestCase):
    def test_a_finding_from_one_pass_alone_survives_the_merge(self):
        """THE load-bearing test of this whole feature.

        The bug being fixed is a FALSE NEGATIVE — defects no critic reported. A
        majority vote would suppress exactly the finding that only one model was
        sharp enough to catch, making false negatives MORE likely. If this test
        ever fails, the panel has become a mute button and is worse than the
        single critic it replaced.
        """
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "the keyed answer is wrong")],
            "b": [],
            "c": [],
        })
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["qid"], "q1")
        self.assertEqual(merged[0]["agreement"], 1)
        self.assertEqual(merged[0]["sources"], ["a"])

    def test_agreement_counts_distinct_passes_on_the_same_qid_and_severity(self):
        """Agreement is grouped at (qid, severity), not per issue string.

        Two models essentially never phrase a defect identically, so a
        string-equality notion of agreement would report 1 for everything.
        """
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "answer B is actually correct")],
            "b": [_finding("q1", "the key points at the wrong option")],
        })
        self.assertEqual(len(merged), 2)          # union: both wordings kept
        self.assertTrue(all(f["agreement"] == 2 for f in merged))

    def test_near_verbatim_restatements_collapse_but_keep_both_sources(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "The keyed answer is wrong.")],
            "b": [_finding("q1", "the keyed answer is wrong")],
        })
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["sources"], ["a", "b"])
        self.assertEqual(merged[0]["agreement"], 2)

    def test_merging_keeps_the_worst_confidence_never_the_mildest(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "same issue", confidence="low")],
            "b": [_finding("q1", "same issue", confidence="high")],
        })
        self.assertEqual(merged[0]["confidence"], "high")

    def test_merging_prefers_the_more_specific_correction(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "same issue", correction="")],
            "b": [_finding("q1", "same issue",
                           correction="key should be option C, per NIST SP 800-63B")],
        })
        self.assertIn("800-63B", merged[0]["correction"])

    def test_different_severities_on_one_qid_stay_separate(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "wrong key", severity="wrong-answer")],
            "b": [_finding("q1", "sloppy wording", severity="nit")],
        })
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(f["agreement"] == 1 for f in merged))

    def test_merge_order_is_deterministic(self):
        per_pass = {
            "b": [_finding("q2", "beta"), _finding("q1", "alpha")],
            "a": [_finding("q3", "gamma")],
        }
        first = panel_mod.merge_findings(per_pass)
        second = panel_mod.merge_findings(dict(reversed(list(per_pass.items()))))
        self.assertEqual([f["qid"] for f in first], ["q1", "q2", "q3"])
        self.assertEqual(first, second)

    def test_solo_qids_flags_uncorroborated_questions_without_removing_them(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "shared"), _finding("q2", "solo")],
            "b": [_finding("q1", "shared")],
        })
        solo = panel_mod.solo_qids(merged)
        self.assertEqual(solo, ["q2"])
        # The solo finding is still in the union — flagged, not filtered.
        self.assertIn("q2", [f["qid"] for f in merged])


class RunPanelTests(unittest.TestCase):
    QUESTIONS = [{"id": "q1", "prompt": "a"}, {"id": "q2", "prompt": "b"}]

    def _reply(self, findings, checked=2, model="m"):
        return cp.CriticReply(_critic_json(findings, checked), model, "opencode")

    def test_a_failing_pass_does_not_abort_the_others(self):
        """A misconfigured cheap pass must not take the panel down.

        If it did, the rational response would be to stop adding passes — which
        is exactly the single-critic review this feature replaces.
        """
        calls = {"n": 0}

        def _run(provider, prompt, model, timeout):
            calls["n"] += 1
            if provider == "openai-compatible":
                raise RuntimeError("connection refused")
            return self._reply([_finding("q1", "bad key")])

        passes = [panel_mod.PassSpec("openai-compatible", "gw-model"),
                  panel_mod.PassSpec("opencode", "ds-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["coverage_ok"])
        self.assertTrue(result["passes"][1]["coverage_ok"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(panel_mod.panel_coverage_ok(result))

    def test_a_preflight_failure_is_recorded_not_raised(self):
        passes = [panel_mod.PassSpec("opencode", None)]
        with patch.object(cp, "preflight", return_value="`opencode` CLI not on PATH"):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["ok"])
        self.assertIn("opencode", result["errors"][0])
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_unchecked_is_the_minimum_across_passes_not_the_sum(self):
        """One complete pass means the pack WAS reviewed in full.

        A second pass timing out cannot un-review it, so summing (or maxing)
        would invent uncovered questions that were in fact graded.
        """
        def _run(provider, prompt, model, timeout):
            if provider == "openai-compatible":
                raise RuntimeError("timed out")
            return self._reply([], checked=2)

        passes = [panel_mod.PassSpec("openai-compatible", "gw-model"),
                  panel_mod.PassSpec("opencode", "ds-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertEqual(result["questions_unchecked"], 0)

    def test_panel_coverage_is_false_when_no_pass_completed(self):
        with patch.object(cp, "run", side_effect=RuntimeError("down")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5)
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_progress_is_emitted_for_every_pass(self):
        """INV-1: a multi-pass network wait must never be a silent block."""
        events = []
        with patch.object(cp, "run", return_value=self._reply([])), \
             patch.object(cp, "preflight", return_value=None):
            panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5,
                on_event=lambda kind, **info: events.append(kind))
        self.assertIn("pass_start", events)
        self.assertIn("batch", events)
        self.assertIn("pass_done", events)

    def test_a_panel_level_variant_reaches_opencode_and_skips_claude(self):
        """One --variant flag on a mixed panel must not blow up the claude pass.

        run_critic raises for claude+variant, so run_panel has to withhold the
        flag from any pass that cannot use it rather than forwarding it
        uniformly."""
        seen_variant = {}

        def _run(provider, prompt, model, timeout, variant=None):
            seen_variant[provider] = variant
            return self._reply([])

        claude_stdout = json.dumps({"type": "result",
                                    "result": _critic_json([]),
                                    "modelUsage": {"claude-sonnet-5": {}}})
        passes = [panel_mod.PassSpec("opencode", "ds-flash"),
                  panel_mod.PassSpec("claude", "claude-sonnet-5")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None), \
             patch.object(fc, "run_claude", return_value=claude_stdout):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5,
                                         variant="max")
        self.assertEqual(seen_variant["opencode"], "max")
        self.assertTrue(result["passes"][1]["ok"])  # claude pass did not error

    def test_summary_records_observed_models_for_the_certification(self):
        with patch.object(cp, "run",
                          return_value=self._reply([], model="ds-flash-x")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5)
        summary = panel_mod.panel_summary(result)
        self.assertEqual(summary["passes_attempted"], 1)
        self.assertEqual(summary["passes_completed"], 1)
        self.assertEqual(summary["passes"][0]["model_observed"],
                         "ds-flash-x")
        self.assertEqual(summary["passes"][0]["model_requested"],
                         "ds-flash")

    def test_subject_reaches_the_actual_critic_prompt(self):
        """run_panel must forward `subject` all the way through
        collect_findings/_run_one_batch/build_prompt into what the provider
        actually receives — not just accept the parameter and drop it."""
        captured = {}

        def _run(provider, prompt, model, timeout):
            captured["prompt"] = prompt
            return self._reply([])

        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            panel_mod.run_panel(
                self.QUESTIONS, [panel_mod.PassSpec("opencode", "ds-flash")],
                12, 5, subject="CISSP")
        self.assertIn("CISSP", captured["prompt"])
        self.assertNotIn("Security+", captured["prompt"])


class WhoMayCertifyTests(unittest.TestCase):
    """Adding `--provider` must not make the certification cheaper to mint.

    Before the provider seam existed, `external-layer-c-strict` was reachable
    only through the project's designated external critic. `--provider` pointed
    Layer C at arbitrary endpoints; if that path kept minting the same
    review_method, the install gate could no longer tell a frontier-model
    certification from a 1B local one — or from an HTTP stub that answers
    `{"findings": []}` to everything. That is the self-attestation INV-7 was
    rewritten to refuse, and it would have been a NET WEAKENING of the gate
    shipped inside the change meant to strengthen it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pack = Path(self._tmp.name) / "pack.json"
        self.pack.write_text(json.dumps({
            **NATIVE_METADATA,
            "pack_id": "who-may-certify",
            "questions": [dict(CLEAN_Q)],
            "coverage_blueprint": [{"topic": "math", "min": 1}],
        }))

    def tearDown(self):
        self._tmp.cleanup()

    def _rubber_stamp(self, provider, prompt, model, timeout, variant=None):
        """A critic that approves everything without looking — the threat model."""
        return cp.CriticReply(_critic_json([], checked=1), model, provider)

    def _run(self, argv, claude_stdout=None):
        out, err = io.StringIO(), io.StringIO()
        claude = (patch.object(fc, "run_claude", return_value=claude_stdout)
                  if claude_stdout is not None else
                  patch.object(fc, "run_claude",
                               side_effect=AssertionError("claude must not run")))
        with patch.object(cp, "run", side_effect=self._rubber_stamp), \
             patch.object(cp, "preflight", return_value=None), claude:
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _cert(self):
        return json.loads(self.pack.read_text()).get("certification")

    def test_a_single_non_default_provider_does_not_certify(self):
        rc, _out, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                                "--model", "tiny:1b"])
        self.assertEqual(rc, 3)                 # reviewed, explicitly NOT certified
        self.assertIsNone(self._cert())
        self.assertFalse(
            pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_codex_is_review_only_when_called_directly(self):
        rc, _, _ = self._run([str(self.pack), "--provider", "codex",
                              "--model", "gpt-test", "--variant", "high"])
        self.assertEqual(rc, 3)
        self.assertIsNone(self._cert())

    def test_it_says_why_and_names_the_certifying_command(self):
        """An unexplained exit 3 is what sends someone hunting for a bypass."""
        _, out, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                               "--model", "tiny:1b"])
        self.assertIn("REVIEW PASSED", out)
        self.assertNotIn("PACK READY", out)
        self.assertIn("hybrid_verify.py", out)

    def test_a_non_default_provider_cannot_recertify_via_only_either(self):
        """`--only` has its own certification path; it must honour the same rule."""
        rc, _, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                              "--model", "tiny:1b", "--only", "q1"])
        self.assertEqual(rc, 3)
        self.assertIsNone(self._cert())

    def test_variant_is_rejected_at_the_cli_for_the_default_provider(self):
        """--variant with no --panel and the default (claude) provider is a
        caller error caught before any critic runs, not a silent no-op."""
        rc, _, err = self._run([str(self.pack), "--variant", "max"])
        self.assertEqual(rc, 1)
        self.assertIn("--variant", err)
        self.assertIn("opencode", err)
        self.assertIsNone(self._cert())

    def test_variant_is_accepted_at_the_cli_for_the_opencode_provider(self):
        seen = {}

        def _run(provider, prompt, model, timeout, variant=None):
            seen["variant"] = variant
            return cp.CriticReply(_critic_json([], checked=1), model, provider)

        out, err = io.StringIO(), io.StringIO()
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(self.pack), "--provider", "opencode",
                             "--variant", "max"])
        self.assertEqual(rc, 3)  # reviews, does not certify — unrelated to variant
        self.assertEqual(seen["variant"], "max")

    def test_the_default_provider_is_review_only_when_called_directly(self):
        """Direct invocation cannot mint the certification any longer."""
        stdout = json.dumps({
            "type": "result", "result": _critic_json([], checked=1),
            "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        rc, _out, _ = self._run([str(self.pack)], claude_stdout=stdout)
        self.assertEqual(rc, 3)
        self.assertIsNone(self._cert())

    def test_panel_route_is_rejected_by_the_internal_primitive(self):
        rc, _, _ = self._run(
            [str(self.pack), "--panel", "openai-compatible=tiny-1b,opencode"])
        self.assertEqual(rc, 1)
        self.assertIsNone(self._cert())

    def test_what_the_gate_accepts_equals_what_the_gate_can_write(self):
        """No accepted-but-unwritable review method.

        A method `certification_fresh` honours but no code path produces is a
        cert shape only a hand-edit could have made — the gate would trust it
        precisely because nothing legitimate creates it. This caught the dead
        `external-layer-c-standard` entry.
        """
        self.assertEqual(vp.CERTIFYING_REVIEW_METHODS,
                         frozenset({"external-layer-c-strict"}))
        self.assertNotIn("external-layer-c-panel", pack_cert.APPROVED_REVIEW_METHODS)

    def test_a_one_entry_panel_is_refused_at_the_cli(self):
        """parse_panel enforces it; this pins the CLI wiring that calls it."""
        rc, _, err = self._run([str(self.pack), "--panel", "opencode"])
        self.assertEqual(rc, 1)
        self.assertIn("retired", err)
        self.assertIsNone(self._cert())


if __name__ == "__main__":
    unittest.main()
