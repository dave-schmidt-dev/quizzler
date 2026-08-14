#!/usr/bin/env python3
"""Focused v2 candidate/readiness contracts."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_adapter  # noqa: E402
from release_adapter import AdapterError, bind_artifact_attestation, freeze_release  # noqa: E402
from release_readiness import ReadinessError, append_readiness_observation, evaluate_readiness  # noqa: E402
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402

NOW_TEXT = "2026-08-14T12:00:00Z"
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.source_digest = "a" * 64
        self.request = root / "request.json"
        write_json(self.request, {
            "formatVersion": "2.0.0", "marketingVersion": "1.2.3", "buildNumber": "17", "gitRevision": "head-a",
            "sourceDigest": self.source_digest, "adapterDigest": digest(Path(release_adapter.__file__)),
            "identityProofSha256": "c" * 64, "lane": "standard",
            "readinessRequirements": ["production-schema", "device-acceptance", "asc-build", "testflight-receipt"],
            "createdAt": NOW_TEXT,
        })
        self.manifest = freeze_release(self.request, state_directory=self.state, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.candidate = self.manifest.parent
        self.device = root / "evidence" / "device.json"
        self.preflight_digest = hashlib.sha256(b"signed-physical-preflight-build").hexdigest()
        self.signature_digest = hashlib.sha256(b"codesign-display-evidence").hexdigest()
        self.entitlements_digest = hashlib.sha256(b"production-entitlements-evidence").hexdigest()
        self.device_document = {
            "formatVersion": "2.0.0", "candidateId": "1.2.3-17", "marketingVersion": "1.2.3", "buildNumber": "17",
            "gitRevision": "head-a", "sourceDigest": self.source_digest, "capturedAt": NOW_TEXT,
            "preflightBuild": {
                "signedBuildSha256": self.preflight_digest, "codeSignatureSha256": self.signature_digest,
                "entitlementsSha256": self.entitlements_digest, "bundleIdentifier": "com.zerodelta.quizzler",
                "teamIdentifier": "4CJ49V6QHW", "cloudKitContainerIdentifiers": ["iCloud.com.zerodelta.quizzler"],
                "cloudKitContainerEnvironment": "Production",
            },
            "devices": [{
                "deviceEvidenceId": "device-a", "platform": "physical", "sourceDigest": self.source_digest,
                "signedBuildSha256": self.preflight_digest, "codeSignatureSha256": self.signature_digest,
                "entitlementsSha256": self.entitlements_digest, "cloudKitContainerIdentifier": "iCloud.com.zerodelta.quizzler",
                "cloudKitContainerEnvironment": "Production", "observedAt": NOW_TEXT,
            }],
        }
        write_json(self.device, self.device_document)
        # Readiness observations are deliberately appendable before the final IPA exists.
        self.device_record = append_readiness_observation(self.manifest, "device", self.device, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.schema = root / "evidence" / "production-schema.json"
        schema_body = {"containerIdentifier": "iCloud.com.zerodelta.quizzler", "recordTypes": {"Progress": {"fields": {"revision": {"type": "INT64"}}}}}
        self.production = {"formatVersion": "2.0.0", "candidateId": "1.2.3-17", "marketingVersion": "1.2.3", "buildNumber": "17", "gitRevision": "head-a", "sourceDigest": self.source_digest, "environment": "Production", "schema": schema_body, "schemaDigest": hashlib.sha256(json.dumps(schema_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "capturedAt": NOW_TEXT}
        write_json(self.schema, self.production)
        self.production_record = append_readiness_observation(self.manifest, "production-schema", self.schema, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.readiness = root / "readiness.json"
        write_json(self.readiness, {"formatVersion": "2.0.0", "candidateManifest": self.manifest.relative_to(root).as_posix(), "evidence": {"productionSchema": {"path": self.schema.relative_to(root).as_posix(), "sha256": digest(self.schema)}, "device": {"path": self.device.relative_to(root).as_posix(), "sha256": digest(self.device)}}})
        self.artifact = self.candidate / "artifact" / "QuizzleriOS.ipa"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"signed-production-artifact")
        bind_artifact_attestation(self.manifest, self.artifact, runtime=DEFAULT_DESTINATION, captured_at=NOW_TEXT)


class ReleaseReadinessTests(unittest.TestCase):
    def test_require_accepts_a_sequence_of_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            report = evaluate_readiness(
                fixture.readiness,
                repository_root=fixture.root,
                runtime=DEFAULT_DESTINATION,
                now=NOW,
                require=["production-schema", "device-acceptance"],
            )
            self.assertEqual(report["decision"], "ready")

    def test_v2_evidence_derives_ready_with_preflight_device_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            report = evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW, require=frozenset({"production-schema", "device-acceptance"}))
            self.assertEqual(report["decision"], "ready")
            observation = fixture.device_record["observation"]
            self.assertEqual(observation["signedBuildSha256"], fixture.preflight_digest)
            self.assertNotIn("artifactSha256", observation)
            self.assertNotIn("artifactSha256", fixture.production_record["observation"])

    def test_production_schema_rejects_container_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            production = json.loads(fixture.schema.read_text(encoding="utf-8"))
            production["schema"]["containerIdentifier"] = "iCloud.com.example.other"
            production["schemaDigest"] = hashlib.sha256(
                json.dumps(production["schema"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            write_json(fixture.schema, production)
            readiness = json.loads(fixture.readiness.read_text(encoding="utf-8"))
            readiness["evidence"]["productionSchema"]["sha256"] = digest(fixture.schema)
            write_json(fixture.readiness, readiness)
            with self.assertRaisesRegex(ReadinessError, "production-schema-evidence-invalid"):
                evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW)

    def test_prebuild_readiness_does_not_require_final_ipa_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            (fixture.candidate / "artifact-attestation.json").unlink()
            fixture.artifact.unlink()
            report = evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW, require=frozenset({"production-schema", "device-acceptance"}))
            self.assertEqual(report["decision"], "ready")

    def test_v1_readiness_and_manifest_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "v1.json"
            write_json(request, {"formatVersion": "1.0.0"})
            with self.assertRaisesRegex(AdapterError, "release-request-invalid"):
                freeze_release(request, state_directory=root / "state", repository_root=root, runtime=DEFAULT_DESTINATION)

    def test_artifact_drift_and_single_device_requirement_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.artifact.write_bytes(b"drift")
            with self.assertRaisesRegex(ReadinessError, "artifact-attestation-artifact-drift"):
                fixture.readiness and evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW, require=frozenset({"asc-build"}))
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            value = json.loads(fixture.device.read_text())
            value["devices"].append(dict(value["devices"][0], deviceEvidenceId="device-b"))
            write_json(fixture.device, value)
            with self.assertRaisesRegex(ReadinessError, "evidence-hash-mismatch"):
                evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW)

    def test_device_preflight_rejects_nonproduction_entitlements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture(root)
            value = json.loads(fixture.device.read_text())
            value["preflightBuild"]["cloudKitContainerEnvironment"] = "Development"
            write_json(fixture.device, value)
            with self.assertRaisesRegex(ReadinessError, "device-preflight-attestation-invalid"):
                append_readiness_observation(fixture.manifest, "device", fixture.device, repository_root=root, runtime=DEFAULT_DESTINATION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
