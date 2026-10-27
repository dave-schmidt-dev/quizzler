"""Unit tests for ``scripts/hybrid_verify.py`` — the two-pass hybrid Layer-C
verify (advisory advisory(-go) review pass, followed by the sole
certifying configurable high-capability verifier).

hybrid_verify calls scripts/verify_pack.py's ``main()`` IN-PROCESS, up to
twice per invocation. Every test here MOCKS either ``verify_pack.main``
directly (to assert the exact argv each pass is built with, and that the
second call is skipped whenever it must be) or the underlying critic
transports (``factcheck_pack.run_claude`` / ``critic_providers.run_opencode``
and ``critic_providers.shutil.which``) for a couple of true end-to-end runs
through verify_pack's real certifying logic. NO real LLM or network call happens, and no
live/paid pass is ever run.

Run from the project root::

    python3 -m unittest tests.test_hybrid_verify -v
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests.hybrid_verify_fixtures import _Base, fc, hv, vp

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
            "advisory": {"exit_code": 3, "report": pass_report()},
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

    def test_stamp_without_a_round_stays_fresh(self):
        """A pre-chain stamp carries no round number and must stay installable."""
        ledger = self._ledger()
        with patch.object(vp, "main", side_effect=AssertionError("reviewer invoked")):
            rc, _out, _err = self.run_main(["--certify-campaign", str(ledger)])
        self.assertEqual(rc, 0)
        payload = json.loads(self.pack.read_text())
        self.assertNotIn("remediation_round", payload["certification"]["provenance"])
        self.assertTrue(vp.pack_cert.certification_fresh(payload))

        payload["certification"]["provenance"]["remediation_round"] = 2
        self.assertTrue(vp.pack_cert.certification_fresh(payload))
        for bad in (0, -1, "2", 1.0, None):
            payload["certification"]["provenance"]["remediation_round"] = bad
            self.assertFalse(vp.pack_cert.certification_fresh(payload),
                             f"remediation_round {bad!r} must fail closed")

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
    def test_advisory_route_comes_from_the_open_code_standard_roster(self):
        result = hv.subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({
                "target": "opencode-go", "harness": "opencode",
                "requiredCapability": "standard", "selector": "opencode-go/mimo-v2.5",
                "variant": None,
            }),
            stderr="",
        )
        with patch.object(hv.subprocess, "run", return_value=result) as run:
            route = hv.resolve_advisory_route()
        self.assertEqual(route.selector, "opencode-go/mimo-v2.5")
        self.assertIsNone(route.variant)
        self.assertEqual(run.call_args.args[0][-2:], ["opencode-go", "standard"])

    def test_advisory_route_rejects_a_low_capability_roster_response(self):
        result = hv.subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({
                "target": "opencode-go", "harness": "opencode",
                "requiredCapability": "low", "selector": "opencode-go/mimo-v2.5",
                "variant": None,
            }),
            stderr="",
        )
        with patch.object(hv.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(hv.AdvisoryRouteError, "invalid route"):
                hv.resolve_advisory_route()

    def test_advisory_route_refuses_an_invalid_roster_response(self):
        result = hv.subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
        with patch.object(hv.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(hv.AdvisoryRouteError, "invalid route"):
                hv.resolve_advisory_route()

    def test_help_lists_all_flags(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            hv.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        helptext = out.getvalue()
        for flag in ("--verifier-profile", "--batch-size", "--advisory-batch-size",
                    "--verifier-batch-size", "--timeout", "--jobs",
                    "--advisory-jobs", "--verifier-jobs", "--only", "--no-certify",
                    "--skip-advisory", "--json", "--campaign-snapshot",
                    "--evidence-output", "--strict"):
            self.assertIn(flag, helptext)

    def test_defaults(self):
        args = hv.build_arg_parser().parse_args(["some/pack.json"])
        self.assertFalse(hasattr(args, "advisory_model"))
        self.assertFalse(hasattr(args, "variant"))
        self.assertEqual(args.verifier_profile, "codex-terra-high")
        self.assertEqual(args.verifier_profile, hv.DEFAULT_VERIFIER_PROFILE)
        self.assertEqual(args.batch_size, 12)
        self.assertIsNone(args.advisory_batch_size)
        self.assertIsNone(args.verifier_batch_size)
        self.assertEqual(args.timeout, 180)
        self.assertEqual(args.jobs, fc.DEFAULT_JOBS)
        self.assertIsNone(args.advisory_jobs)
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


if __name__ == "__main__":
    unittest.main()
