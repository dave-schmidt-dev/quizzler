"""Unit tests for ``scripts/hybrid_verify.py`` — the two-pass hybrid Layer-C
verify (advisory DeepSeek(-go) review pass, followed by the sole
certifying configurable high-capability verifier).

hybrid_verify calls scripts/verify_pack.py's ``main()`` IN-PROCESS, up to
twice per invocation. Every test here MOCKS either ``verify_pack.main``
directly (to assert the exact argv each pass is built with, and that the
second call is skipped whenever it must be) or the underlying critic
transports (``factcheck_pack.run_claude`` / ``critic_providers.run_opencode``
and ``critic_providers.shutil.which``) for a couple of true end-to-end runs
through verify_pack's real certifying logic — exactly the split
tests/test_recert_sweep.py uses. NO real LLM or network call happens, and no
live/paid pass is ever run.

Run from the project root::

    python3 -m unittest tests.test_hybrid_verify -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"

_spec = importlib.util.spec_from_file_location("hybrid_verify", SCRIPT_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# hybrid_verify imports verify_pack (which imports factcheck_pack and
# critic_providers) by path; reach the SAME module objects here so patches
# land where verify_pack.run_layer_c actually looks them up (mirrors
# test_recert_sweep.py's `vp = rs.verify_pack; fc = vp.factcheck_pack`).
vp = hv.verify_pack
fc = vp.factcheck_pack
cp = vp.critic_providers


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def _coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def codex_reply(findings: list[dict], checked: int = 1) -> cp.CriticReply:
    """Canned Codex final message; Codex does not attest the served model."""
    return cp.CriticReply(
        text=json.dumps({"findings": findings, "checked": checked}),
        model=None, provider="codex")


def ds_reply(findings: list[dict], checked: int = 1) -> cp.CriticReply:
    """Canned opencode CriticReply — model is always None, mirroring the real
    run_opencode contract (opencode's event stream never attests a model)."""
    text = json.dumps({"findings": findings, "checked": checked})
    return cp.CriticReply(text=text, model=None, provider="opencode")


# See L29: a fixture pack that the native decoder would refuse fails Layer A,
# which is not what these end-to-end tests are exercising.
NATIVE_METADATA = {"subject": "Math", "title": "Hybrid verify fixture", "version": 1}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.pack = self.tmp_path / "ch01.json"
        payload = {
            **NATIVE_METADATA,
            "pack_id": "ch01",
            "questions": [dict(CLEAN_Q)],
        }
        payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        self.pack.write_text(json.dumps(payload))

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, extra_argv: list[str]):
        extra_argv = list(extra_argv)
        if "--json" in extra_argv and "--only" not in extra_argv \
                and "--campaign-snapshot" not in extra_argv:
            extra_argv += [
                "--campaign-snapshot",
                hv.certification_campaign.build_snapshot(self.pack)["fingerprint"],
            ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = hv.main([str(self.pack)] + extra_argv)
        return rc, out.getvalue(), err.getvalue()


class FrozenCampaignCertificationTests(_Base):
    """The final campaign route stamps saved evidence and never reviews."""

    def _ledger(self):
        snapshot = hv.certification_campaign.build_snapshot(self.pack)
        ledger = hv.certification_campaign.new_ledger(snapshot)

        def pass_report():
            return {
                "ready": False, "outcome": "review_ok", "partial": False,
                "layer_a": {"live": []},
                "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                            "questions_unchecked": 0, "total": len(snapshot["question_ids"])},
            }

        hv.certification_campaign.record_hybrid_discovery(ledger, {
            "schema_version": hv.JSON_SCHEMA_VERSION, "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "ds": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()}, "exit_code": 3,
        })
        path = self.tmp_path / "campaign.json"
        hv.certification_campaign.save_ledger(path, ledger)
        return path

    def test_valid_campaign_stamps_without_invoking_reviewer(self):
        ledger = self._ledger()
        with patch.object(vp, "main", side_effect=AssertionError("reviewer invoked")):
            rc, out, _err = self.run_main(["--certify-campaign", str(ledger)])
        self.assertEqual(rc, 0)
        self.assertIn("frozen-campaign-evidence", out)
        cert = json.loads(self.pack.read_text())["certification"]
        self.assertEqual(cert["provenance"]["evidence_policy"], "no-new-llm-call")
        self.assertTrue(vp.pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_stale_campaign_fails_closed_without_reviewer(self):
        ledger = self._ledger()
        payload = json.loads(self.pack.read_text())
        payload["questions"][0]["prompt"] = "Changed after campaign"
        self.pack.write_text(json.dumps(payload))
        with patch.object(vp, "main", side_effect=AssertionError("reviewer invoked")):
            rc, out, _err = self.run_main(["--certify-campaign", str(ledger)])
        self.assertEqual(rc, 2)
        self.assertIn("snapshot", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_malformed_campaign_fails_closed_without_reviewer(self):
        ledger = self._ledger()
        value = json.loads(ledger.read_text())
        value["discoveries"][1]["examined_qids"] = []
        ledger.write_text(json.dumps(value))
        with patch.object(vp, "main", side_effect=AssertionError("reviewer invoked")):
            rc, out, _err = self.run_main(["--certify-campaign", str(ledger)])
        self.assertEqual(rc, 2)
        self.assertIn("campaign certification refused", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_malformed_campaign_provenance_is_not_fresh(self):
        ledger = self._ledger()
        with patch.object(vp, "main", side_effect=AssertionError("reviewer invoked")):
            rc, _out, _err = self.run_main(["--certify-campaign", str(ledger)])
        self.assertEqual(rc, 0)
        data = json.loads(self.pack.read_text())
        data["certification"]["provenance"]["evidence_policy"] = "reviewer-called"
        self.assertFalse(vp.pack_cert.certification_fresh(data))


class ArgParserTests(unittest.TestCase):
    def test_help_lists_all_flags(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            hv.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        helptext = out.getvalue()
        for flag in ("--ds-model", "--variant", "--verifier-profile",
                    "--batch-size", "--ds-batch-size",
                    "--verifier-batch-size", "--timeout", "--jobs",
                    "--ds-jobs", "--verifier-jobs", "--only", "--no-certify",
                    "--skip-advisory", "--json", "--campaign-snapshot",
                    "--evidence-output", "--strict"):
            self.assertIn(flag, helptext)

    def test_defaults(self):
        args = hv.build_arg_parser().parse_args(["some/pack.json"])
        self.assertEqual(args.ds_model, hv.DEFAULT_DS_MODEL)
        self.assertEqual(args.ds_model, "opencode-go/deepseek-v4-flash")
        self.assertEqual(args.variant, "max")
        self.assertEqual(args.verifier_profile, "codex-terra-high")
        self.assertEqual(args.verifier_profile, hv.DEFAULT_VERIFIER_PROFILE)
        self.assertEqual(args.batch_size, 12)
        self.assertIsNone(args.ds_batch_size)
        self.assertIsNone(args.verifier_batch_size)
        self.assertEqual(args.timeout, 180)
        self.assertEqual(args.jobs, fc.DEFAULT_JOBS)
        self.assertIsNone(args.ds_jobs)
        self.assertIsNone(args.verifier_jobs)
        self.assertIsNone(args.only)
        self.assertFalse(args.no_certify)
        self.assertFalse(args.skip_advisory)
        self.assertFalse(args.json)
        self.assertIsNone(args.campaign_snapshot)
        self.assertFalse(args.strict)

    def test_only_accepts_comma_separated_question_ids(self):
        args = hv.build_arg_parser().parse_args(
            ["some/pack.json", "--only", "q1,q2"])
        self.assertEqual(args.only, "q1,q2")

    def test_no_pack_is_argparse_usage_error(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                hv.main([])
        self.assertNotEqual(cm.exception.code, 0)


def _capture(rc_sequence: list[int]):
    """Build a fake verify_pack.main that returns rc_sequence[call_index] and
    records every argv it was called with, in order."""
    calls: list[list[str]] = []

    def fake_main(argv, **kwargs):
        calls.append(argv)
        return rc_sequence[len(calls) - 1]

    return calls, fake_main


class RunHybridArgvTests(_Base):
    def test_high_only_full_json_census_skips_deepseek(self):
        calls = []
        progress = []

        def fake_main(argv, **kwargs):
            calls.append((argv, kwargs))
            print(json.dumps({"ready": False, "outcome": "review_ok"}))
            return 3

        snapshot = hv.certification_campaign.build_snapshot(self.pack)["fingerprint"]
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, skip_advisory=True,
                campaign_snapshot=snapshot, progress=progress.append)

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("opencode", calls[0][0])
        self.assertIsNone(calls[0][1]["_hybrid_certifier"])
        self.assertEqual(result["snapshot_fingerprint"], snapshot)
        self.assertIn("explicitly skipped", result["ds"]["report_error"])
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
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False)

    """Mock verify_pack.main directly so each pass's argv can be inspected
    without going through any real (or even mocked-at-the-transport) critic
    call — mirrors test_recert_sweep.py's CertifyOneTests."""

    def test_ds_pass_argv_shape(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="opencode-go/deepseek-v4-flash",
                          variant="max", verifier_profile="codex-terra-high",
                          batch_size=12, timeout=180, jobs=6, strict=False)
        ds_argv = calls[0]
        self.assertEqual(ds_argv[0], str(self.pack))
        self.assertIn("--provider", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--provider") + 1], "opencode")
        self.assertIn("--model", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--model") + 1],
                         "opencode-go/deepseek-v4-flash")
        self.assertIn("--variant", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--variant") + 1], "max")
        self.assertIn("--batch-size", ds_argv)
        self.assertIn("--timeout", ds_argv)
        self.assertIn("--jobs", ds_argv)
        self.assertNotIn("--strict", ds_argv)

    def test_verifier_pass_argv_shape(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="opencode-go/deepseek-v4-flash",
                          variant="max", verifier_profile="codex-terra-high",
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
                self.pack, ds_model="d", variant="max",
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
                self.pack, ds_model="d", variant="max",
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
                self.pack, ds_model="d", variant="max",
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
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False)

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("SystemExit", report)
        self.assertNotIn("provider secret", report)

        with patch.object(vp, "main", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                hv.run_hybrid(
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False)

    def test_ds_and_verifier_jobs_split_the_two_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
                          verifier_profile="codex-terra-high", batch_size=5,
                          timeout=42, jobs=9, ds_jobs=3, verifier_jobs=1,
                          strict=False)

        self.assertEqual(calls[0][calls[0].index("--jobs") + 1], "3")
        self.assertEqual(calls[1][calls[1].index("--jobs") + 1], "1")

    def test_ds_and_verifier_batch_sizes_split_the_two_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
                          verifier_profile="codex-terra-high", batch_size=9,
                          ds_batch_size=3, verifier_batch_size=1,
                          timeout=42, jobs=6, strict=False)

        self.assertEqual(calls[0][calls[0].index("--batch-size") + 1], "3")
        self.assertEqual(calls[1][calls[1].index("--batch-size") + 1], "1")

    def test_omitted_ds_jobs_default_to_one_verifier_keeps_shared_jobs(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
                          verifier_profile="codex-terra-high", batch_size=5,
                          timeout=42, jobs=7, strict=False)

        self.assertEqual(calls[0][calls[0].index("--jobs") + 1], "1")
        self.assertEqual(calls[1][calls[1].index("--jobs") + 1], "7")
        self.assertIn("--no-retry-incomplete", calls[0])
        self.assertNotIn("--no-retry-incomplete", calls[1])

    def test_omitted_pass_batch_sizes_fall_back_to_batch_size(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
                          verifier_profile="codex-terra-high", batch_size=7,
                          timeout=42, jobs=6, strict=False)

        self.assertEqual(calls[0][calls[0].index("--batch-size") + 1], "7")
        self.assertEqual(calls[1][calls[1].index("--batch-size") + 1], "7")

    def test_only_is_forwarded_to_both_passes(self):
        calls, fake_main = _capture([3, 3])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, _ = hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, only="q1,q2")

        self.assertEqual(rc, 3)
        for pass_argv in calls:
            self.assertIn("--only", pass_argv)
            self.assertEqual(pass_argv[pass_argv.index("--only") + 1], "q1,q2")

    def test_omitted_only_is_not_forwarded_to_either_pass(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
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
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False)

        self.assertIsNone(calls[0][1]["_hybrid_certifier"])
        self.assertTrue(all(call[1]["_hybrid_certifier"] is None for call in calls))

        calls.clear()
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, certifying=False,
                only="q1,q2")

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[1]["_hybrid_certifier"] is None for call in calls))
        for pass_argv, _kwargs in calls:
            self.assertEqual(pass_argv[pass_argv.index("--only") + 1], "q1,q2")

    def test_json_is_forwarded_to_both_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, campaign_snapshot=hv.certification_campaign
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
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, only=" q2, ,q1,q2, ",
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
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, only=" , , ",
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
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    certifying=False, campaign_snapshot="sha256:" + "a" * 64)
        self.assertEqual(calls, [])

    def test_targeted_json_rejects_mismatched_snapshot_before_passes(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "campaign snapshot mismatch"):
                hv.run_hybrid(
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    certifying=False, only="q1",
                    campaign_snapshot="sha256:" + "a" * 64)
        self.assertEqual(calls, [])

    def test_targeted_json_rejects_missing_or_malformed_snapshot_before_passes(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "requires --campaign-snapshot"):
                hv.run_hybrid(
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    certifying=False, only="q1")
            with self.assertRaisesRegex(ValueError, "64 lowercase hex"):
                hv.run_hybrid(
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, json_output=True,
                    certifying=False, only="q1",
                    campaign_snapshot="sha256:not-a-digest")

        self.assertEqual(calls, [])

    def test_retired_live_certifying_route_rejects_before_either_pass_runs(self):
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            with self.assertRaisesRegex(ValueError, "live reviewer certification is retired"):
                hv.run_hybrid(
                    self.pack, ds_model="d", variant="max",
                    verifier_profile="codex-terra-high", batch_size=7,
                    timeout=42, jobs=6, strict=False, certifying=True)

        self.assertEqual(calls, [])

    def test_registered_claude_profile_omits_codex_effort_flags(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="d", variant="max",
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
    """DeepSeek is advisory; every loadable DS outcome reaches Codex."""

    def test_ds_outcomes_always_run_codex_and_codex_decides(self):
        for ds_rc in (1, 2, 3):
            with self.subTest(ds_rc=ds_rc):
                calls, fake_main = _capture([ds_rc, 0])
                with patch.object(vp, "main", side_effect=fake_main):
                    rc, report = hv.run_hybrid(
                        self.pack, ds_model="d", variant="max",
                        verifier_profile="codex-terra-high", batch_size=12,
                        timeout=180, jobs=6, strict=False)
                self.assertEqual(rc, 0)
                self.assertEqual(len(calls), 2)
                self.assertIn("advisory", report)

    def test_ds_clean_then_codex_ready_runs_both_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, _report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", verifier_profile="codex-terra-high",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)

    def test_ds_advisory_finding_then_codex_not_ready_reports_decision(self):
        """The high-capability verifier decides even when DS is clean."""
        calls, fake_main = _capture([3, 2])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", verifier_profile="codex-terra-high",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("DS pass is advisory and does not certify", report)

    def test_unloadable_pack_skips_both_passes(self):
        missing = self.tmp_path / "missing.json"
        calls, fake_main = _capture([])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                missing, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=12,
                timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertIn("pack not found", report)


class EndToEndTests(_Base):
    """Real verify_pack logic end to end (no mocked verify_pack.main): mocks
    only the critic transports, so this catches argv-wiring mistakes an
    argv-capture test would miss (e.g. a typo'd flag name verify_pack itself
    would reject)."""

    def test_ds_blocking_finding_does_not_block_high_verifier_discovery(self):
        finding = {"qid": "q1", "severity": "wrong-answer", "issue": "wrong",
                   "correction": "fix", "confidence": "high"}

        with patch.object(cp, "run_opencode",
                          return_value=ds_reply([finding])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main([])

        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_clean_default_live_review_cannot_stamp(self):
        with patch.object(cp, "run_opencode", return_value=ds_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main([])

        self.assertEqual(rc, 3)
        data = json.loads(self.pack.read_text())
        self.assertNotIn("certification", data)

    def test_skip_advisory_diagnostic_cannot_stamp(self):
        with patch.object(cp, "run_opencode",
                          side_effect=AssertionError("advisory must be skipped")), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main(["--skip-advisory"])

        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_ds_go_model_is_reached_not_the_free_tier(self):
        """Confirms the DS pass reaches the go-tier model reference and NOT
        opencode's free-tier default, end to end through real verify_pack /
        critic_providers dispatch."""
        seen_models = []

        def _spy_run_opencode(prompt, model, timeout, variant=None):
            seen_models.append(model)
            return ds_reply([])

        with patch.object(cp, "run_opencode", side_effect=_spy_run_opencode), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main([])

        self.assertEqual(rc, 3)
        self.assertEqual(seen_models, ["opencode-go/deepseek-v4-flash"])

    def test_clean_targeted_recheck_does_not_certify(self):
        second = dict(CLEAN_Q, id="q2", prompt="What is 3+3?",
                      options=["6", "5", "7", "8"], answer=0,
                      explanation="Three plus three is six.")
        payload = {**NATIVE_METADATA, "pack_id": "ch01", "questions": [dict(CLEAN_Q), second]}
        payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        self.pack.write_text(json.dumps(payload))
        with patch.object(cp, "run_opencode", return_value=ds_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main(["--only", "q1"])

        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_clean_discovery_full_review_does_not_certify(self):
        with patch.object(cp, "run_opencode", return_value=ds_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, _err = self.run_main(["--no-certify"])

        self.assertEqual(rc, 3)
        self.assertIn("discovery mode", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_json_wrapper_has_both_parsed_pass_reports(self):
        with patch.object(cp, "run_opencode", return_value=ds_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, _err = self.run_main(["--json", "--no-certify"])

        self.assertEqual(rc, 3)
        result = json.loads(out)
        self.assertEqual(result["schema_version"], hv.JSON_SCHEMA_VERSION)
        self.assertFalse(result["certifying"])
        self.assertIsNone(result["target_qids"])
        self.assertEqual(
            result["snapshot_fingerprint"],
            hv.certification_campaign.build_snapshot(self.pack)["fingerprint"],
        )
        self.assertEqual(result["verifier_profile"], "codex-terra-high")
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["ds"]["exit_code"], 3)
        self.assertEqual(result["verifier"]["exit_code"], 3)
        self.assertIn("report", result["ds"])
        self.assertIn("report", result["verifier"])
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_cli_rejects_certifying_json_before_review_or_pack_mutation(self):
        before = self.pack.read_text()
        with patch.object(vp, "main") as verify_main:
            rc, out, err = self.run_main(["--json"])

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("--json requires --no-certify", err)
        verify_main.assert_not_called()
        self.assertEqual(self.pack.read_text(), before)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_cli_rejects_targeted_json_without_campaign_snapshot_before_review(self):
        before = self.pack.read_text()
        with patch.object(vp, "main") as verify_main:
            rc, out, err = self.run_main(["--json", "--no-certify", "--only", "q1"])

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("requires --campaign-snapshot", err)
        verify_main.assert_not_called()
        self.assertEqual(self.pack.read_text(), before)

    def test_json_wrapper_reports_malformed_pass_output_fail_closed(self):
        calls = 0

        def fake_main(_argv, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                print(json.dumps({"outcome": "reviewed"}))
                return 3
            print("not json")
            return 0

        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        self.assertEqual(rc, 3)
        result = json.loads(report)
        self.assertFalse(result["certifying"])
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["ds"]["report"], {"outcome": "reviewed"})
        self.assertIn("report_error", result["verifier"])
        self.assertNotIn("report", result["verifier"])
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_json_wrapper_redacts_stderr_diagnostic_for_malformed_output(self):
        calls = 0

        def fake_main(_argv, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                print("provider timed out with token=super-secret-value", file=sys.stderr)
                print("not json")
                return 1
            print(json.dumps({"outcome": "reviewed"}))
            return 3

        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(result["ds"]["diagnostic"], "stderr indicated a timeout")
        self.assertNotIn("super-secret-value", report)
        self.assertNotIn("token=", report)

    def test_json_wrapper_reports_safe_in_process_exception_and_runs_verifier(self):
        calls = 0

        def fake_main(_argv, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("provider token=super-secret-value")
            print(json.dumps({"outcome": "reviewed"}))
            return 3

        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                certifying=False, campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, 2)
        self.assertIn("report_error", result["ds"])
        self.assertIn("diagnostic", result["ds"])
        self.assertNotIn("super-secret-value", report)
        self.assertNotIn("token=", report)


if __name__ == "__main__":
    unittest.main()
