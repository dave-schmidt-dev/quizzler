#!/usr/bin/env python3
"""Focused v2 candidate/readiness contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import pack_cert  # noqa: E402

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
        self.pack = root / "question-packs" / "cissp" / "cissp-core.json"
        pack = {
            "subject": "CISSP",
            "questions": [{"id": "q1", "type": "multiple_choice", "prompt": "What is 2+2?", "options": ["3", "4"], "answer": 1, "explanation": "Four."}],
        }
        pack["certification"] = {
            "certified": True,
            "hash_schema_version": pack_cert.HASH_SCHEMA_VERSION,
            "critic_contract_version": pack_cert.CRITIC_CONTRACT_VERSION,
            "verified_at": NOW_TEXT,
            "questions_hash": pack_cert.questions_hash(pack),
            "critic_model": "fixture-reviewer",
            "review_method": "external-layer-c-strict",
            "blocking_count": 0,
            "questions_examined": 1,
            "question_stamps": pack_cert.build_question_stamps(pack),
        }
        write_json(self.pack, pack)
        self.device = root / "evidence" / "device.json"
        self.preflight_digest = hashlib.sha256(b"signed-physical-preflight-build").hexdigest()
        self.signature_digest = hashlib.sha256(b"codesign-display-evidence").hexdigest()
        self.entitlements_digest = hashlib.sha256(b"production-entitlements-evidence").hexdigest()
        self.device_ids = [hashlib.sha256(value).hexdigest() for value in (b"device-a", b"device-b")]
        self.semantic_state_digest = hashlib.sha256(b"canonical-shared-progress-state").hexdigest()
        self.device_document = {
            "formatVersion": "2.0.0", "candidateId": "1.2.3-17", "marketingVersion": "1.2.3", "buildNumber": "17",
            "gitRevision": "head-a", "sourceDigest": self.source_digest, "capturedAt": NOW_TEXT,
            "preflightBuild": {
                "signedBuildSha256": self.preflight_digest, "codeSignatureSha256": self.signature_digest,
                "entitlementsSha256": self.entitlements_digest, "bundleIdentifier": "com.zerodelta.quizzler",
                "teamIdentifier": "4CJ49V6QHW", "cloudKitContainerIdentifiers": ["iCloud.com.zerodelta.quizzler.dev"],
                "cloudKitContainerEnvironment": "Production",
            },
            "devices": [{
                "deviceEvidenceId": self.device_ids[0], "platform": "physical", "sourceDigest": self.source_digest,
                "signedBuildSha256": self.preflight_digest, "codeSignatureSha256": self.signature_digest,
                "entitlementsSha256": self.entitlements_digest, "cloudKitContainerIdentifier": "iCloud.com.zerodelta.quizzler.dev",
                "cloudKitContainerEnvironment": "Production", "semanticStateSha256": self.semantic_state_digest,
                "observedAt": NOW_TEXT,
            }, {
                "deviceEvidenceId": self.device_ids[1], "platform": "physical", "sourceDigest": self.source_digest,
                "signedBuildSha256": self.preflight_digest, "codeSignatureSha256": self.signature_digest,
                "entitlementsSha256": self.entitlements_digest, "cloudKitContainerIdentifier": "iCloud.com.zerodelta.quizzler.dev",
                "cloudKitContainerEnvironment": "Production", "semanticStateSha256": self.semantic_state_digest,
                "observedAt": NOW_TEXT,
            }],
            "convergence": {
                "candidateId": "1.2.3-17", "sourceDigest": self.source_digest,
                "cloudKitContainerIdentifier": "iCloud.com.zerodelta.quizzler.dev",
                "cloudKitContainerEnvironment": "Production", "deviceEvidenceIds": self.device_ids,
                "semanticStateSha256": self.semantic_state_digest,
            },
        }
        write_json(self.device, self.device_document)
        # Readiness observations are deliberately appendable before the final IPA exists.
        self.device_record = append_readiness_observation(self.manifest, "device", self.device, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.schema = root / "evidence" / "production-schema.json"
        schema_body = {"containerIdentifier": "iCloud.com.zerodelta.quizzler.dev", "recordTypes": {"Progress": {"fields": {"revision": {"type": "INT64"}}}}}
        self.production = {"formatVersion": "2.0.0", "candidateId": "1.2.3-17", "marketingVersion": "1.2.3", "buildNumber": "17", "gitRevision": "head-a", "sourceDigest": self.source_digest, "environment": "Production", "schema": schema_body, "schemaDigest": hashlib.sha256(json.dumps(schema_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "capturedAt": NOW_TEXT}
        write_json(self.schema, self.production)
        self.production_record = append_readiness_observation(self.manifest, "production-schema", self.schema, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.inv8 = root / "evidence" / "inv8-certification.json"
        certification = json.loads(self.pack.read_text(encoding="utf-8"))["certification"]
        self.inv8_document = {
            "formatVersion": "2.0.0", "candidateId": "1.2.3-17", "marketingVersion": "1.2.3", "buildNumber": "17",
            "gitRevision": "head-a", "sourceDigest": self.source_digest, "capturedAt": NOW_TEXT,
            "packs": [{
                "packPath": "question-packs/cissp/cissp-core.json", "packSha256": digest(self.pack),
                "questionsHash": certification["questions_hash"], "certificationSha256": hashlib.sha256(json.dumps(certification, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "independentReview": {"reviewedAt": NOW_TEXT, "reviewerModel": "separate-model", "evidenceSha256": "f" * 64, "packSha256": digest(self.pack), "questionsHash": certification["questions_hash"]},
                "humanSpotCheck": {"reviewedAt": NOW_TEXT, "reviewerSha256": "d" * 64, "evidenceSha256": "e" * 64, "packSha256": digest(self.pack), "questionsHash": certification["questions_hash"]},
            }],
        }
        write_json(self.inv8, self.inv8_document)
        self.inv8_record = append_readiness_observation(self.manifest, "inv8-certification", self.inv8, repository_root=root, runtime=DEFAULT_DESTINATION)
        self.readiness = root / "readiness.json"
        write_json(self.readiness, {"formatVersion": "2.0.0", "candidateManifest": self.manifest.relative_to(root).as_posix(), "evidence": {"inv8Certification": {"path": self.inv8.relative_to(root).as_posix(), "sha256": digest(self.inv8)}, "productionSchema": {"path": self.schema.relative_to(root).as_posix(), "sha256": digest(self.schema)}, "device": {"path": self.device.relative_to(root).as_posix(), "sha256": digest(self.device)}}})
        self.artifact = self.candidate / "artifact" / "QuizzleriOS.ipa"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"signed-production-artifact")
        bind_artifact_attestation(self.manifest, self.artifact, runtime=DEFAULT_DESTINATION, captured_at=NOW_TEXT)


class ReleaseReadinessTests(unittest.TestCase):
    def test_current_candidate_cli_fails_on_missing_inv8_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "app" / "releases" / "state" / "current-readiness.json"
            write_json(current, {
                "formatVersion": "2.0.0",
                "candidateManifest": "app/releases/state/candidates/legacy/manifest.json",
                "evidence": {
                    "productionSchema": {"path": "missing", "sha256": "0" * 64},
                    "device": {"path": "missing", "sha256": "0" * 64},
                },
            })
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("release_readiness.py")), "--repository", str(root), "--candidate", "current"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("BLOCKED inv8-certification-missing", result.stderr)

    def test_inv8_missing_stale_partial_editable_and_pack_mismatch_fail_closed(self) -> None:
        cases = ("missing", "stale", "partial", "editable", "source-mismatch", "pack-mismatch")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                readiness = json.loads(fixture.readiness.read_text(encoding="utf-8"))
                if case == "missing":
                    del readiness["evidence"]["inv8Certification"]
                    write_json(fixture.readiness, readiness)
                    expected = "inv8-certification-missing"
                else:
                    evidence = json.loads(fixture.inv8.read_text(encoding="utf-8"))
                    if case == "stale":
                        evidence["capturedAt"] = "2020-01-01T00:00:00Z"
                    elif case == "partial":
                        del evidence["packs"][0]["humanSpotCheck"]
                    elif case == "editable":
                        evidence["passed"] = True
                    elif case == "source-mismatch":
                        evidence["sourceDigest"] = "e" * 64
                    else:
                        fixture.pack.write_text(fixture.pack.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                    if case != "pack-mismatch":
                        write_json(fixture.inv8, evidence)
                    readiness["evidence"]["inv8Certification"]["sha256"] = digest(fixture.inv8)
                    write_json(fixture.readiness, readiness)
                    expected = {
                        "stale": "evidence-stale",
                        "partial": "inv8-certification-partial",
                        "editable": "editable-pass-flag-forbidden",
                        "source-mismatch": "inv8-certification-source-mismatch",
                        "pack-mismatch": "inv8-pack-hash-mismatch",
                    }[case]
                with self.assertRaisesRegex(ReadinessError, expected):
                    evaluate_readiness(fixture.readiness, repository_root=fixture.root, runtime=DEFAULT_DESTINATION, now=NOW)

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
            statuses: list[str] = []
            report = evaluate_readiness(
                fixture.readiness,
                repository_root=fixture.root,
                runtime=DEFAULT_DESTINATION,
                now=NOW,
                require=frozenset({"production-schema", "device-acceptance"}),
                on_status=statuses.append,
            )
            self.assertEqual(report["decision"], "ready")
            self.assertEqual(
                statuses,
                [
                    "release-readiness-input-loaded",
                    "release-readiness-candidate-resolved",
                    "release-readiness-observation-validation-started",
                    "release-readiness-observation-validation-complete",
                    "release-readiness-evidence-validation-started",
                    "release-readiness-evidence-validation-complete",
                    "release-readiness-inv8-validation-started",
                    "release-readiness-inv8-validation-complete",
                    "release-readiness-schema-validation-started",
                    "release-readiness-schema-validation-complete",
                    "release-readiness-device-validation-started",
                    "release-readiness-device-validation-complete",
                ],
            )
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
            value["devices"].pop()
            write_json(fixture.device, value)
            readiness = json.loads(fixture.readiness.read_text())
            readiness["evidence"]["device"]["sha256"] = digest(fixture.device)
            write_json(fixture.readiness, readiness)
            with self.assertRaisesRegex(ReadinessError, "device-evidence-invalid"):
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
