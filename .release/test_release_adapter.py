"""Offline Quizzler adapter contract tests. No Apple, broker, or device calls."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

_CENTRAL = Path(__file__).resolve().parents[2] / "apple_developer"
if str(_CENTRAL) not in sys.path:
    sys.path.insert(0, str(_CENTRAL))

from release_tools.adapter import adapter_rejection_codes, load_adapter  # noqa: E402
from release_tools.conformance import audit_conformance  # noqa: E402
from release_tools.scaffold import audit_scaffold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / ".release" / "release-adapter.json"
PLAN = ROOT / ".release" / "release-plan.json"
BROKER = ROOT / ".release" / "broker-consumer-request.json"


class ReleaseAdapterAdoptionTests(unittest.TestCase):
    def test_project_adapter_is_configured(self) -> None:
        report = audit_scaffold(ROOT)
        self.assertEqual(report.status, "passed", report.as_dict())

    def test_contract_is_conformant_and_adoption_stays_canary_gated(self) -> None:
        document = json.loads(ADAPTER.read_text(encoding="utf-8"))
        self.assertEqual(adapter_rejection_codes(document), ())
        loaded = load_adapter(document, repository_root=ROOT)
        self.assertEqual(loaded.product["productKey"], "quizzler-ios")
        self.assertEqual(loaded.product["bundleIdentifier"], "com.zerodelta.quizzler")
        self.assertEqual(loaded.product["teamIdentifier"], "4CJ49V6QHW")
        report = audit_conformance(adapter_path=ADAPTER, plan_path=PLAN, repository_root=ROOT)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["adoptionStatus"], "real-tool-canary-required", report)

    def test_broker_evidence_matches_the_registered_request(self) -> None:
        document = json.loads(ADAPTER.read_text(encoding="utf-8"))
        expected = hashlib.sha256(BROKER.read_bytes()).hexdigest()
        evidence = document["registeredConsumers"][0]["evidence"]
        self.assertEqual(evidence["descriptorSha256"], expected)
        self.assertEqual(evidence["consumer"], "quizzler-testflight-upload")

    def test_fixed_operations_are_offline_and_no_upload_is_declared(self) -> None:
        document = json.loads(ADAPTER.read_text(encoding="utf-8"))
        classes = {operation["class"] for operation in document["operations"]}
        self.assertNotIn("upload", classes)
        self.assertNotIn("assignment", classes)
        self.assertNotIn("identityAllocation", classes)
        for operation in document["operations"]:
            self.assertNotIn("command", operation)
            if operation["mode"] == "nonCredential":
                self.assertFalse(operation["environment"]["inherit"])
                self.assertEqual(operation["environment"]["literals"], {})

    def test_wrappers_are_fixed_and_reference_the_central_cli(self) -> None:
        for name in ("release-testflight", "release-status"):
            path = ROOT / "app" / name
            self.assertTrue(path.is_file(), path)
            source = path.read_text(encoding="utf-8")
            self.assertIn("release_tools", source)
            self.assertIn("--adapter", source)

    def test_deploy_testflight_requires_attended_and_wraps_the_central_cli(self) -> None:
        source = (ROOT / "app" / "deploy-testflight").read_text(encoding="utf-8")
        self.assertIn("--attended", source)
        self.assertIn("release_tools", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
