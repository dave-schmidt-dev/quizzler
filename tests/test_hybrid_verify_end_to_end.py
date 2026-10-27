"""End-to-end hybrid verify tests using the real ``verify_pack`` logic."""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch

from tests.hybrid_verify_fixtures import (
    CLEAN_Q,
    NATIVE_METADATA,
    _Base,
    _coverage_blueprint,
    advisory_reply,
    codex_reply,
    cp,
    hv,
    vp,
)

class EndToEndTests(_Base):
    """Real verify_pack logic end to end (no mocked verify_pack.main): mocks
    only the critic transports, so this catches argv-wiring mistakes an
    argv-capture test would miss (e.g. a typo'd flag name verify_pack itself
    would reject)."""

    def test_advisory_blocking_finding_does_not_block_high_verifier_discovery(self):
        finding = {"qid": "q1", "severity": "wrong-answer", "issue": "wrong",
                   "correction": "fix", "confidence": "high"}

        with patch.object(cp, "run_opencode",
                          return_value=advisory_reply([finding])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main([])

        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_clean_default_live_review_cannot_stamp(self):
        with patch.object(cp, "run_opencode", return_value=advisory_reply([])), \
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

    def test_advisory_go_model_is_reached_not_the_free_tier(self):
        """Confirms the advisory pass reaches the go-tier model reference and NOT
        opencode's free-tier default, end to end through real verify_pack /
        critic_providers dispatch."""
        seen_models = []

        def _spy_run_opencode(prompt, model, timeout, variant=None):
            seen_models.append(model)
            return advisory_reply([])

        with patch.object(cp, "run_opencode", side_effect=_spy_run_opencode), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main([])

        self.assertEqual(rc, 3)
        self.assertEqual(seen_models, ["opencode-go/mimo-v2.5"])

    def test_clean_targeted_recheck_does_not_certify(self):
        second = dict(CLEAN_Q, id="q2", prompt="What is 3+3?",
                      options=["6", "5", "7", "8"], answer=0,
                      explanation="Three plus three is six.")
        payload = {**NATIVE_METADATA, "pack_id": "ch01", "questions": [dict(CLEAN_Q), second]}
        payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        self.pack.write_text(json.dumps(payload))
        with patch.object(cp, "run_opencode", return_value=advisory_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, _out, _err = self.run_main(["--only", "q1"])

        self.assertEqual(rc, 3)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_clean_discovery_full_review_does_not_certify(self):
        with patch.object(cp, "run_opencode", return_value=advisory_reply([])), \
             patch.object(cp, "run_codex", return_value=codex_reply([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, _err = self.run_main(["--no-certify"])

        self.assertEqual(rc, 3)
        self.assertIn("discovery mode", out)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_json_wrapper_has_both_parsed_pass_reports(self):
        with patch.object(cp, "run_opencode", return_value=advisory_reply([])), \
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
        self.assertEqual(result["advisory"]["exit_code"], 3)
        self.assertEqual(result["verifier"]["exit_code"], 3)
        self.assertIn("report", result["advisory"])
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
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        self.assertEqual(rc, 3)
        result = json.loads(report)
        self.assertFalse(result["certifying"])
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["advisory"]["report"], {"outcome": "reviewed"})
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
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(result["advisory"]["diagnostic"], "stderr indicated a timeout")
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
                self.pack, advisory_model="d", advisory_variant="max",
                verifier_profile="codex-terra-high", batch_size=7,
                timeout=42, jobs=6, strict=False, json_output=True,
                campaign_snapshot=hv.certification_campaign
                .build_snapshot(self.pack, verifier_profile="codex-terra-high")["fingerprint"])

        result = json.loads(report)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, 2)
        self.assertIn("report_error", result["advisory"])
        self.assertIn("diagnostic", result["advisory"])
        self.assertNotIn("super-secret-value", report)
        self.assertNotIn("token=", report)


if __name__ == "__main__":
    unittest.main()
