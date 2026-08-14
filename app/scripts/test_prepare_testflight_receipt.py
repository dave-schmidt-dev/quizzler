#!/usr/bin/env python3
"""Contracts for read-only TestFlight receipt preparation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_testflight_receipt as module


def response(resource_type: str, resource_id: str, attributes: dict[str, object]) -> dict[str, object]:
    return {"data": [{"type": resource_type, "id": resource_id, "attributes": attributes}]}


class ReceiptPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temporary.name) / "evidence"
        self.responses = [
            response("apps", "app-1", {"bundleId": "com.zerodelta.quizzler"}),
            response("betaGroups", "group-1", {"isInternalGroup": True}),
            response("appEncryptionDeclarations", "declaration-1", {"appEncryptionDeclarationState": "APPROVED"}),
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, method: str, path: str, body: dict[str, object] | None) -> dict[str, object]:
        self.assertEqual(method, "GET")
        self.assertIsNone(body)
        return self.responses.pop(0)

    def test_writes_public_receipts_only_after_exact_read_only_resolution(self) -> None:
        events: list[str] = []
        with patch.object(module, "EVIDENCE_DIRECTORY", self.evidence), patch.object(module, "GROUP_PATH", self.evidence / "group.json"), patch.object(module, "COMPLIANCE_PATH", self.evidence / "compliance.json"):
            result = module.prepare_receipts(self.request, status=events.append)
            self.assertEqual(result, {"appId": "app-1", "groupId": "group-1", "declarationId": "declaration-1"})
            group = json.loads((self.evidence / "group.json").read_text())
            compliance = json.loads((self.evidence / "compliance.json").read_text())
        self.assertEqual(group["groupId"], "group-1")
        self.assertTrue(group["isInternalGroup"])
        self.assertEqual(compliance["declarationId"], "declaration-1")
        self.assertEqual(events, ["asc-app-discovery-started", "asc-internal-group-discovery-started", "asc-compliance-discovery-started", "testflight-receipts-captured"])

    def test_ambiguous_group_response_writes_nothing(self) -> None:
        cases = [
            [response("apps", "one", {"bundleId": "com.zerodelta.quizzler"}), response("betaGroups", "a", {"isInternalGroup": True}), response("betaGroups", "b", {"isInternalGroup": True})],
        ]
        for replies in cases:
            with self.subTest(replies=replies), patch.object(module, "EVIDENCE_DIRECTORY", self.evidence), patch.object(module, "GROUP_PATH", self.evidence / "group.json"), patch.object(module, "COMPLIANCE_PATH", self.evidence / "compliance.json"):
                self.responses = list(replies)
                with self.assertRaises(module.ReceiptPreparationError):
                    module.prepare_receipts(self.request)
                self.assertFalse((self.evidence / "group.json").exists())
                self.assertFalse((self.evidence / "compliance.json").exists())

    def test_missing_approved_declaration_still_captures_the_internal_group(self) -> None:
        self.responses = [
            response("apps", "app-1", {"bundleId": "com.zerodelta.quizzler"}),
            response("betaGroups", "group-1", {"isInternalGroup": True}),
            {"data": []},
        ]
        with patch.object(module, "EVIDENCE_DIRECTORY", self.evidence), patch.object(module, "GROUP_PATH", self.evidence / "group.json"), patch.object(module, "COMPLIANCE_PATH", self.evidence / "compliance.json"):
            result = module.prepare_receipts(self.request)
        self.assertEqual(result, {"appId": "app-1", "groupId": "group-1"})
        self.assertTrue((self.evidence / "group.json").is_file())
        self.assertFalse((self.evidence / "compliance.json").exists())

    def test_ambiguous_approved_declarations_write_nothing(self) -> None:
        self.responses = [
            response("apps", "app-1", {"bundleId": "com.zerodelta.quizzler"}),
            response("betaGroups", "group-1", {"isInternalGroup": True}),
            {"data": [
                {"type": "appEncryptionDeclarations", "id": "one", "attributes": {"appEncryptionDeclarationState": "APPROVED"}},
                {"type": "appEncryptionDeclarations", "id": "two", "attributes": {"appEncryptionDeclarationState": "APPROVED"}},
            ]},
        ]
        with patch.object(module, "EVIDENCE_DIRECTORY", self.evidence), patch.object(module, "GROUP_PATH", self.evidence / "group.json"), patch.object(module, "COMPLIANCE_PATH", self.evidence / "compliance.json"):
            with self.assertRaisesRegex(module.ReceiptPreparationError, "asc-approved-compliance-cardinality"):
                module.prepare_receipts(self.request)
        self.assertFalse((self.evidence / "group.json").exists())

    def test_exact_bundle_match_is_required(self) -> None:
        self.responses = [response("apps", "wrong", {"bundleId": "com.example.other"})]
        with patch.object(module, "EVIDENCE_DIRECTORY", self.evidence), patch.object(module, "GROUP_PATH", self.evidence / "group.json"), patch.object(module, "COMPLIANCE_PATH", self.evidence / "compliance.json"):
            with self.assertRaisesRegex(module.ReceiptPreparationError, "asc-app-cardinality"):
                module.prepare_receipts(self.request)
        self.assertFalse(self.evidence.exists())

    def test_cli_preserves_only_a_safe_preparation_stop_code(self) -> None:
        with patch.dict(module.os.environ, {module.MARKER: module.CONSUMER}), patch.object(module, "_jwt_token", return_value="token"), patch.object(module, "prepare_receipts", side_effect=module.ReceiptPreparationError("asc-approved-compliance-cardinality")):
            result = module.main(["--attended"])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
