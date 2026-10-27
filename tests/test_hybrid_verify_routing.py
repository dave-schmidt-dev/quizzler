"""Argument-routing, taxonomy and advisory tests for ``hybrid_verify``."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tests.hybrid_verify_fixtures import _Base, _capture, hv, vp

class RunHybridArgvTests(_Base):
    def test_roster_selector_and_variant_are_forwarded_to_advisory_pass(self):
        calls, fake_main = _capture([3, 0])
        roster_result = hv.subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({
                "target": "opencode-go", "harness": "opencode",
                "requiredCapability": "standard", "selector": "roster/selected",
                "variant": "reasoning",
            }),
            stderr="",
        )
        with patch.object(hv.subprocess, "run", return_value=roster_result), \
             patch.object(vp, "main", side_effect=fake_main):
            hv.main([str(self.pack), "--no-certify"])
        advisory_argv = calls[0]
        self.assertEqual(advisory_argv[advisory_argv.index("--model") + 1], "roster/selected")
        self.assertEqual(advisory_argv[advisory_argv.index("--variant") + 1], "reasoning")

    def test_high_only_full_json_census_skips_advisory(self):
        calls = []
        progress = []

        def fake_main(argv, **kwargs):
            calls.append((argv, kwargs))
            print(json.dumps({"ready": False, "outcome": "review_ok"}))
            return 3

        snapshot = hv.certification_campaign.build_snapshot(self.pack)["fingerprint"]
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                skip_advisory=True,
                campaign_snapshot=snapshot, progress=progress.append)

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("opencode", calls[0][0])
        self.assertNotIn("_hybrid_certifier", calls[0][1])
        self.assertEqual(result["snapshot_fingerprint"], snapshot)
        self.assertIn("explicitly skipped", result["advisory"]["report_error"])
        self.assertIn("non-certifying full-pack census", progress[0])
        self.assertNotIn("sole certifying", progress[0])

    def test_evidence_output_inside_question_packs_is_rejected_before_review(self):
        output = hv.QUESTION_PACKS_DIR / "course" / "campaign.json"
        snapshot = hv.certification_campaign.build_snapshot(self.pack)["fingerprint"]
        with patch.object(vp, "main") as verify_main:
            rc, out, err = self.run_main([
                "--json", "--no-certify", "--campaign-snapshot", snapshot,
                "--evidence-output", str(output),
            ])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("must not be inside question-packs", err)
        verify_main.assert_not_called()

    def test_evidence_output_writes_machine_report_to_explicit_safe_path(self):
        output = self.tmp_path / ".logs" / "campaign.json"

        def fake_main(_argv, **_kwargs):
            print(json.dumps({"outcome": "reviewed"}))
            return 3

        with patch.object(vp, "main", side_effect=fake_main):
            rc, out, _err = self.run_main([
                "--json", "--no-certify", "--evidence-output", str(output),
            ])
        self.assertEqual(rc, 3)
        self.assertEqual(json.loads(output.read_text()), json.loads(out))

    def test_repository_pack_json_defaults_evidence_under_logs(self):
        pack = hv.QUESTION_PACKS_DIR / "course" / "round-1.json"
        self.assertEqual(
            hv._default_evidence_output(pack),
            hv.EVIDENCE_LOG_DIR / "course" / "round-1.json",
        )

    def test_full_json_census_requires_snapshot_binding(self):
        with self.assertRaisesRegex(ValueError, "JSON discovery requires"):
            hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True)

    """Mock verify_pack.main directly so each pass's argv can be inspected
    without going through any real (or even mocked-at-the-transport) critic
    call."""

    def test_advisory_pass_argv_shape(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="opencode-go/mimo-v2.5",
                          advisory_variant="max", verifier_profile="codex-terra-high",
                          batch_size=12, timeout=180, jobs=6, strict=False)
        advisory_argv = calls[0]
        self.assertEqual(advisory_argv[0], str(self.pack))
        self.assertIn("--provider", advisory_argv)
        self.assertEqual(advisory_argv[advisory_argv.index("--provider") + 1], "opencode")
        self.assertIn("--model", advisory_argv)
        self.assertEqual(advisory_argv[advisory_argv.index("--model") + 1],
                         "opencode-go/mimo-v2.5")
        self.assertIn("--variant", advisory_argv)
        self.assertEqual(advisory_argv[advisory_argv.index("--variant") + 1], "max")
        self.assertIn("--batch-size", advisory_argv)
        self.assertIn("--timeout", advisory_argv)
        self.assertIn("--jobs", advisory_argv)
        self.assertNotIn("--strict", advisory_argv)

    def test_verifier_pass_argv_shape(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="opencode-go/mimo-v2.5",
                          advisory_variant="max", verifier_profile="codex-terra-high",
                          batch_size=5, timeout=42, jobs=3, strict=True)
        verifier_argv = calls[1]
        self.assertEqual(verifier_argv[0], str(self.pack))
        self.assertEqual(verifier_argv[verifier_argv.index("--provider") + 1], "codex")
        self.assertEqual(verifier_argv[verifier_argv.index("--model") + 1], "gpt-5.6-terra")
        self.assertEqual(verifier_argv[verifier_argv.index("--variant") + 1], "high")
        self.assertIn("--batch-size", verifier_argv)
        self.assertEqual(verifier_argv[verifier_argv.index("--batch-size") + 1], "5")
        self.assertIn("--timeout", verifier_argv)
        self.assertEqual(verifier_argv[verifier_argv.index("--timeout") + 1], "42")
        self.assertIn("--jobs", verifier_argv)
        self.assertEqual(verifier_argv[verifier_argv.index("--jobs") + 1], "3")
        self.assertIn("--strict", verifier_argv)

    def test_skip_advisory_runs_only_discovery_verifier(self):
        calls, fake_main = _capture([0])
        progress = []
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=5,
                timeout=42, jobs=3, strict=False, skip_advisory=True,
                progress=progress.append)

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index("--provider") + 1], "codex")
        self.assertNotIn("opencode", calls[0])
        self.assertIn("non-certifying full-pack census", progress[0])
        self.assertIn("explicitly skipped", report)

    def test_skip_advisory_rejects_targeted_mode(self):
        with self.assertRaisesRegex(ValueError, "without --only"):
            hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=5,
                timeout=42, jobs=3, strict=False,
                skip_advisory=True, only="q1")

    def test_unexpected_ds_exception_is_fail_closed_and_verifier_runs(self):
        calls = []

        def fake_main(argv, **_kwargs):
            calls.append(argv)
            if len(calls) == 1:
                raise RuntimeError("provider secret should not escape")
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False)

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("unexpected exception", report)
        self.assertNotIn("provider secret", report)

    def test_system_exit_is_fail_closed_and_keyboard_interrupt_is_preserved(self):
        calls = []

        def raises_system_exit(argv, **_kwargs):
            calls.append(argv)
            if len(calls) == 1:
                raise SystemExit("provider secret should not escape")
            return 0

        with patch.object(vp, "main", side_effect=raises_system_exit):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False)

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("SystemExit", report)
        self.assertNotIn("provider secret", report)

        with patch.object(vp, "main", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                hv.run_hybrid(
                    self.pack, advisory_model="d", advisory_variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False)

    def test_advisory_and_verifier_jobs_split_the_two_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="codex-terra-high", batch_size=5,
                          timeout=42, jobs=9, advisory_jobs=3, verifier_jobs=1,
                          strict=False)

        self.assertEqual(calls[0][calls[0].index("--jobs") + 1], "3")
        self.assertEqual(calls[1][calls[1].index("--jobs") + 1], "1")

    def test_advisory_and_verifier_batch_sizes_split_the_two_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="codex-terra-high", batch_size=9,
                          advisory_batch_size=3, verifier_batch_size=1,
                          timeout=42, jobs=6, strict=False)

        self.assertEqual(calls[0][calls[0].index("--batch-size") + 1], "3")
        self.assertEqual(calls[1][calls[1].index("--batch-size") + 1], "1")

    def test_omitted_ds_jobs_default_to_one_verifier_keeps_shared_jobs(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="codex-terra-high", batch_size=5,
                          timeout=42, jobs=7, strict=False)

        self.assertEqual(calls[0][calls[0].index("--jobs") + 1], "1")
        self.assertEqual(calls[1][calls[1].index("--jobs") + 1], "7")
        self.assertIn("--no-retry-incomplete", calls[0])
        self.assertNotIn("--no-retry-incomplete", calls[1])

    def test_omitted_pass_batch_sizes_fall_back_to_batch_size(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="codex-terra-high", batch_size=7,
                          timeout=42, jobs=6, strict=False)

        self.assertEqual(calls[0][calls[0].index("--batch-size") + 1], "7")
        self.assertEqual(calls[1][calls[1].index("--batch-size") + 1], "7")

    def test_only_is_forwarded_to_both_passes(self):
        calls, fake_main = _capture([3, 3])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, _ = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, only="q1,q2")

        self.assertEqual(rc, 3)
        for pass_argv in calls:
            self.assertIn("--only", pass_argv)
            self.assertEqual(pass_argv[pass_argv.index("--only") + 1], "q1,q2")

    def test_omitted_only_is_not_forwarded_to_either_pass(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="codex-terra-high", batch_size=7,
                          timeout=42, jobs=6, strict=False)

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("--only" not in pass_argv for pass_argv in calls))

    def test_live_review_never_uses_hybrid_certifier(self):
        calls: list[tuple[list[str], dict]] = []

        def fake_main(argv, **kwargs):
            calls.append((argv, kwargs))
            return 3 if len(calls) == 1 else 0

        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False)

        self.assertTrue(all("_hybrid_certifier" not in call[1] for call in calls))

        calls.clear()
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, only="q1,q2")

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("_hybrid_certifier" not in call[1] for call in calls))
        for pass_argv, _kwargs in calls:
            self.assertEqual(pass_argv[pass_argv.index("--only") + 1], "q1,q2")

    def test_json_is_forwarded_to_both_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("--json" in pass_argv for pass_argv in calls))

    def test_targeted_json_canonicalizes_ids_and_binds_snapshot(self):
        calls, fake_main = _capture([3, 3])
        snapshot = hv.certification_campaign.build_snapshot(
            self.pack, verifier_profile="codex-terra-high"
        )["fingerprint"]
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                only=" q2, ,q1,q2, ",
                campaign_snapshot=snapshot)

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(result["target_qids"], ["q1", "q2"])
        self.assertEqual(result["snapshot_fingerprint"], snapshot)
        for pass_argv in calls:
            self.assertEqual(pass_argv[pass_argv.index("--only") + 1], "q1,q2")

    def test_blank_targeted_json_normalizes_to_full_discovery(self):
        calls, fake_main = _capture([3, 3])
        snapshot = hv.certification_campaign.build_snapshot(
            self.pack, verifier_profile="codex-terra-high"
        )["fingerprint"]
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                only=" , , ",
                campaign_snapshot=snapshot)

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertIsNone(result["target_qids"])
        self.assertEqual(result["snapshot_fingerprint"], snapshot)
        self.assertTrue(all("--only" not in pass_argv for pass_argv in calls))

    def test_full_json_rejects_mismatched_snapshot_before_passes(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "campaign snapshot mismatch"):
                hv.run_hybrid(
                    self.pack, advisory_model="d", advisory_variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    campaign_snapshot="sha256:" + "a" * 64)
        self.assertEqual(calls, [])

    def test_targeted_json_rejects_mismatched_snapshot_before_passes(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "campaign snapshot mismatch"):
                hv.run_hybrid(
                    self.pack, advisory_model="d", advisory_variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    only="q1",
                    campaign_snapshot="sha256:" + "a" * 64)
        self.assertEqual(calls, [])

    def test_targeted_json_rejects_missing_or_malformed_snapshot_before_passes(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "requires --campaign-snapshot"):
                hv.run_hybrid(
                    self.pack, advisory_model="d", advisory_variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    only="q1")
            with self.assertRaisesRegex(ValueError, "64 lowercase hex"):
                hv.run_hybrid(
                    self.pack, advisory_model="d", advisory_variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    only="q1",
                    campaign_snapshot="sha256:not-a-digest")

        self.assertEqual(calls, [])

    def test_registered_claude_profile_omits_codex_effort_flags(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, advisory_model="d", advisory_variant="max",
                          verifier_profile="claude-opus-high",
                          batch_size=5, timeout=42, jobs=3, strict=False)
        verifier_argv = calls[1]
        self.assertEqual(verifier_argv[verifier_argv.index("--provider") + 1], "claude")
        self.assertEqual(verifier_argv[verifier_argv.index("--model") + 1], "opus")
        self.assertNotIn("--variant", verifier_argv)


class FindingTaxonomyTests(unittest.TestCase):
    """Keep the documented blocking/advisory Layer-C taxonomy executable."""

    @staticmethod
    def finding(category: str, *, confidence: str = "high", **extra) -> dict:
        finding = {
            "qid": "q1", "category": category, "severity": category,
            "issue": "test", "correction": "fix", "confidence": confidence,
        }
        finding.update(extra)
        return finding

    def test_factual_blockers_and_structured_ambiguity(self):
        self.assertTrue(hv.factcheck_pack.is_blocking(
            self.finding("wrong-answer", confidence="low")))
        self.assertTrue(hv.factcheck_pack.is_blocking(
            self.finding("misleading-explanation")))
        self.assertFalse(hv.factcheck_pack.is_blocking(
            self.finding("misleading-explanation", confidence="medium")))
        self.assertFalse(hv.factcheck_pack.is_blocking(
            self.finding("ambiguous")))
        self.assertTrue(hv.factcheck_pack.is_blocking(self.finding(
            "ambiguous", ambiguity_evidence={
                "multiple_defensible_answers": True, "option_indices": [0, 1],
            })))

    def test_quality_categories_stay_advisory_even_at_high_confidence(self):
        for category in ("nit", "duplicate", "option-quality", "off-axis", "cue"):
            with self.subTest(category=category):
                self.assertFalse(hv.factcheck_pack.is_blocking(
                    self.finding(category, severity="nit")))


class AdvisoryDsTests(_Base):
    """advisory is advisory; every loadable advisory outcome reaches Codex."""

    def test_advisory_outcomes_always_run_codex_and_codex_decides(self):
        for advisory_rc in (1, 2, 3):
            with self.subTest(advisory_rc=advisory_rc):
                calls, fake_main = _capture([advisory_rc, 0])
                with patch.object(vp, "main", side_effect=fake_main):
                    rc, report = hv.run_hybrid(
                        self.pack, advisory_model="d", advisory_variant="max",
                        verifier_profile="codex-terra-high", batch_size=12,
                        timeout=180, jobs=6, strict=False)
                self.assertEqual(rc, 0)
                self.assertEqual(len(calls), 2)
                self.assertIn("advisory", report)

    def test_advisory_clean_then_codex_ready_runs_both_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, _report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max", verifier_profile="codex-terra-high",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)

    def test_advisory_advisory_finding_then_codex_not_ready_reports_decision(self):
        """The high-capability verifier decides even when advisory is clean."""
        calls, fake_main = _capture([3, 2])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, advisory_model="d", advisory_variant="max", verifier_profile="codex-terra-high",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("advisory pass is advisory and does not certify", report)

    def test_unloadable_pack_skips_both_passes(self):
        missing = self.tmp_path / "missing.json"
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                missing, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=12,
                timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertIn("pack not found", report)


if __name__ == "__main__":
    unittest.main()
