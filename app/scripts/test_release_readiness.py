#!/usr/bin/env python3
"""Focused v2 candidate/readiness contracts."""
from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_adapter  # noqa: E402
from release_adapter import AdapterError, bind_artifact_attestation, freeze_release  # noqa: E402
from release_readiness import ReadinessError, evaluate_readiness  # noqa: E402
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = "2026-08-14T12:00:00Z"

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

class Fixture:
    def __init__(self, root: Path) -> None:
        self.root, self.state = root, root / "state"
        self.source_digest = "a" * 64
        self.request = root / "request.json"
        write_json(self.request, {"formatVersion":"2.0.0", "marketingVersion":"1.2.3", "buildNumber":"17", "gitRevision":"head-a", "sourceDigest":self.source_digest, "adapterDigest":digest(Path(release_adapter.__file__)), "identityProofSha256":"c"*64, "lane":"standard", "readinessRequirements":["asc-build", "testflight-receipt"], "createdAt":NOW_TEXT})
        self.manifest = freeze_release(self.request, state_directory=self.state, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.candidate = self.manifest.parent
        self.readiness = root / "readiness.json"
        write_json(self.readiness, {"formatVersion":"2.0.0", "candidateManifest":self.manifest.relative_to(root).as_posix(), "evidence":{}})
        self.artifact = self.candidate / "artifact" / "QuizzleriOS.ipa"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"signed-production-artifact")
        bind_artifact_attestation(self.manifest, self.artifact, runtime=DEFAULT_DESTINATION, captured_at=NOW_TEXT)
        self.preflight_digest = hashlib.sha256(b"signed-physical-preflight-build").hexdigest()
        self.signature_digest = hashlib.sha256(b"codesign-display-evidence").hexdigest()
        self.entitlements_digest = hashlib.sha256(b"production-entitlements-evidence").hexdigest()
        self.device_id = hashlib.sha256(b"device-a").hexdigest()
        self.semantic_state_digest = hashlib.sha256(b"canonical-shared-progress-state").hexdigest()
        self.device = root / "evidence" / "device.json"
        self.device_document = {"formatVersion":"2.0.0", "candidateId":"1.2.3-17", "marketingVersion":"1.2.3", "buildNumber":"17", "gitRevision":"head-a", "sourceDigest":self.source_digest, "capturedAt":NOW_TEXT, "preflightBuild":{"signedBuildSha256":self.preflight_digest,"codeSignatureSha256":self.signature_digest,"entitlementsSha256":self.entitlements_digest,"bundleIdentifier":"com.zerodelta.quizzler","teamIdentifier":"4CJ49V6QHW","cloudKitContainerIdentifiers":["iCloud.com.zerodelta.quizzler.dev"],"cloudKitContainerEnvironment":"Production"}, "devices":[{"deviceEvidenceId":self.device_id,"platform":"physical","sourceDigest":self.source_digest,"signedBuildSha256":self.preflight_digest,"codeSignatureSha256":self.signature_digest,"entitlementsSha256":self.entitlements_digest,"cloudKitContainerIdentifier":"iCloud.com.zerodelta.quizzler.dev","cloudKitContainerEnvironment":"Production","semanticStateSha256":self.semantic_state_digest,"observedAt":NOW_TEXT}]}
        write_json(self.device, self.device_document)

class ReleaseReadinessTests(unittest.TestCase):
    def test_empty_evidence_packet_is_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            report = evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW)
            self.assertEqual((report["decision"], report["verifiedEvidence"]), ("ready", []))

    def test_obsolete_evidence_is_rejected_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); value = json.loads(fixture.readiness.read_text()); value["evidence"] = {"obsolete": {"path":"missing", "sha256":"0"*64}}; write_json(fixture.readiness, value)
            with self.assertRaisesRegex(ReadinessError, "readiness-evidence-set-invalid"):
                evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW)

    def test_artifact_attestation_remains_required_for_post_archive_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary)); fixture.artifact.write_bytes(b"drift")
            with self.assertRaisesRegex(ReadinessError, "artifact-attestation-artifact-drift"):
                evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW, require={"asc-build"})

    def test_v1_request_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); request = root / "v1.json"; write_json(request, {"formatVersion":"1.0.0"})
            with self.assertRaisesRegex(AdapterError, "release-request-invalid"):
                freeze_release(request, state_directory=root / "state", repository_root=root, runtime=DEFAULT_DESTINATION)

if __name__ == "__main__":
    unittest.main(verbosity=2)
