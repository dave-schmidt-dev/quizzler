#!/usr/bin/env python3
"""Focused tests for the attended, hash-free Development probe evidence gate."""
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


module = load("development_probe_evidence", HERE / "development_probe_evidence.py")
resolver = load("resolve_xctestrun_app", HERE / "resolve_xctestrun_app.py")
sys.modules["resolve_xctestrun_app"] = resolver
binder = load("bind_development_probe_xctestrun", HERE / "bind_development_probe_xctestrun.py")


class DevelopmentProbeEvidenceTests(unittest.TestCase):
    def valid(self, **overrides):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        evidence = {
            "schema_version": 1,
            "kind": "cloudkit_development_probe_evidence",
            "status": "complete",
            "terminal": True,
            "configuration": "Debug",
            "signing": "Development",
            "completed_at": now.isoformat().replace("+00:00", "Z"),
            "operations": list(module.OPERATIONS),
            "test_identifier": module.PROBE_TEST_IDENTIFIER,
        }
        evidence.update(overrides)
        return evidence, now

    @staticmethod
    def result_sources(root: Path):
        xcresult = root / "Probe.xcresult"
        xcresult.mkdir()
        signed_app = root / "Quizzler.app"
        signed_app.mkdir()
        return xcresult, signed_app

    @staticmethod
    def fake_tool_results(finished: str, *, passed: bool = True, identity: bool = True):
        summary = {"result": "Passed" if passed else "Failed", "endTime": finished}
        test_name = "testProbeLifecycleIsOptInAndReportsMachineReadableTerminalResult" if identity else "testOther"
        tests = {
            "testNodes": [{
                "nodeIdentifierURL": f"test://QuizzleriOSTests/CloudKitDevelopmentProbeTests/{test_name}",
                "result": "Passed" if passed else "Failed",
            }],
        }
        plist = (
            '<?xml version="1.0"?><plist version="1.0"><dict>'
            '<key>com.apple.developer.icloud-container-identifiers</key><array>'
            '<string>iCloud.com.zerodelta.quizzler.dev</string></array>'
            '<key>aps-environment</key><string>development</string>'
            '</dict></plist>'
        )
        return [
            subprocess.CompletedProcess(["xcrun"], 0, json.dumps(summary), ""),
            subprocess.CompletedProcess(["xcrun"], 0, json.dumps(tests), ""),
            subprocess.CompletedProcess(["codesign", "--verify"], 0, "", ""),
            subprocess.CompletedProcess(["codesign", "-d"], 0, plist, ""),
        ]

    def test_passed_result_without_hash_fields_is_accepted(self):
        evidence, now = self.valid()
        self.assertFalse(any("sha" in key.lower() or "hash" in key.lower() for key in evidence))
        with tempfile.TemporaryDirectory() as directory:
            xcresult, signed_app = self.result_sources(Path(directory))
            with patch.object(module.subprocess, "run", side_effect=self.fake_tool_results(evidence["completed_at"])):
                self.assertEqual(module.validate_evidence(
                    evidence, now=now, xcresult_path=xcresult, signed_app_path=signed_app,
                ), [])

    def test_recorder_writes_hash_free_terminal_evidence(self):
        evidence, _ = self.valid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xcresult, signed_app = self.result_sources(root)
            output = root / "evidence.json"
            with patch.object(module.subprocess, "run", side_effect=(
                self.fake_tool_results(evidence["completed_at"])
                + self.fake_tool_results(evidence["completed_at"])
            )):
                module.record_evidence(output, xcresult, signed_app)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "complete")
            self.assertFalse(any("sha" in key.lower() or "hash" in key.lower() for key in saved))

    def test_failed_probe_result_blocks_even_without_hash_fields(self):
        evidence, now = self.valid()
        with tempfile.TemporaryDirectory() as directory:
            xcresult, signed_app = self.result_sources(Path(directory))
            with patch.object(module.subprocess, "run", side_effect=self.fake_tool_results(evidence["completed_at"], passed=False)):
                errors = module.validate_evidence(evidence, now=now, xcresult_path=xcresult, signed_app_path=signed_app)
            self.assertTrue(any("did not pass" in error for error in errors))

    def test_wrong_probe_test_identity_blocks(self):
        evidence, now = self.valid()
        with tempfile.TemporaryDirectory() as directory:
            xcresult, signed_app = self.result_sources(Path(directory))
            with patch.object(module.subprocess, "run", side_effect=self.fake_tool_results(evidence["completed_at"], identity=False)):
                errors = module.validate_evidence(evidence, now=now, xcresult_path=xcresult, signed_app_path=signed_app)
            self.assertTrue(any("unambiguous passed Development probe test" in error for error in errors))

    def test_missing_or_malformed_sources_block(self):
        evidence, _ = self.valid()
        self.assertIn("independent signed app/xcresult sources are required", module.validate_evidence(evidence))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xcresult = root / "missing.xcresult"
            signed_app = root / "Quizzler.app"
            signed_app.mkdir()
            errors = module.validate_evidence(evidence, xcresult_path=xcresult, signed_app_path=signed_app)
            self.assertTrue(any("artifact source verification failed" in error for error in errors))

    def test_stale_evidence_blocks(self):
        evidence, now = self.valid(completed_at=(now := datetime.now(timezone.utc).replace(microsecond=0) - module.MAX_AGE - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"))
        self.assertIn("evidence is stale", module.validate_evidence(evidence, now=now))

    def test_signed_app_entitlements_are_verified_without_hashing(self):
        plist = (
            '<?xml version="1.0"?><plist version="1.0"><dict>'
            '<key>com.apple.developer.icloud-container-identifiers</key><array>'
            '<string>iCloud.com.zerodelta.quizzler.dev</string></array>'
            '<key>aps-environment</key><string>development</string>'
            '</dict></plist>'
        )
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Quizzler.app"
            app.mkdir()
            results = [
                subprocess.CompletedProcess(["codesign", "--verify"], 0, "", ""),
                subprocess.CompletedProcess(["codesign", "-d"], 0, plist, ""),
            ]
            with patch.object(module.subprocess, "run", side_effect=results):
                self.assertIsNone(module.verify_signed_app(app))

    def test_xctestrun_binding_removes_retired_hash_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed_app = root / "Quizzler.app"
            signed_app.mkdir()
            source = root / "Quizzler.xctestrun"
            source.write_bytes(plistlib.dumps({"QuizzleriOSUITests": {
                "UITargetAppPath": "__TESTROOT__/Quizzler.app",
                "EnvironmentVariables": {
                    "KEEP_FOR_TEST": "value",
                    "QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE": "enabled",
                    "QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE_SIGNED_APP_SHA256": "a" * 64,
                },
            }}))
            output = root / "bound.xctestrun"
            binder.bind(source, output, signed_app, live_probe=True)
            target = plistlib.loads(output.read_bytes())["QuizzleriOSUITests"]
            self.assertEqual(target["EnvironmentVariables"], {
                "KEEP_FOR_TEST": "value", "QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE": "enabled",
            })

    def test_xctestrun_binding_rejects_unsafe_or_wrong_app_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed_app = root / "Quizzler.app"
            signed_app.mkdir()
            source = root / "Quizzler.xctestrun"
            source.write_bytes(plistlib.dumps({"QuizzleriOSUITests": {"UITargetAppPath": "__TESTROOT__/Other.app"}}))
            with self.assertRaisesRegex(ValueError, "absent|exact signed app"):
                binder.bind(source, root / "bound.xctestrun", signed_app)

    def test_probe_runtime_and_docs_contain_no_hash_gate(self):
        swift = (module.ROOT / "QuizzleriOS/QuizzlerApp.swift").read_text(encoding="utf-8")
        tests = (module.ROOT / "QuizzleriOSTests/CloudKitDevelopmentProbeTests.swift").read_text(encoding="utf-8")
        docs = (module.ROOT / "releases/evidence/README.md").read_text(encoding="utf-8")
        for source in (swift, tests, docs, (module.ROOT / "scripts/bind_development_probe_xctestrun.py").read_text(encoding="utf-8")):
            self.assertNotIn("signed_app_sha256", source)
            self.assertNotIn("app_sha256", source)
            self.assertNotIn("artifact_pair_sha256", source)
        self.assertNotIn("CryptoKit", swift)
        self.assertNotIn("XCTAttachment", tests)


if __name__ == "__main__":
    unittest.main()
