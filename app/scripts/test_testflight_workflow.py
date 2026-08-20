#!/usr/bin/env python3
"""Deterministic unit tests for the injected TestFlight workflow."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testflight_workflow import (  # noqa: E402
    ArchiveArtifact,
    IpaArtifact,
    PINNED_UPLOAD_CONSUMER,
    QuizzlerTestFlightProvider,
    ReleaseIdentity,
    WorkflowError,
    _call,
    _verified_signing_certificate_status,
    run_candidate_workflow,
    run_workflow,
)
from provision_signing import AscHTTPError  # noqa: E402
from test_release_readiness import Fixture  # noqa: E402
from release_candidate import source_snapshot  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
TEST_REVISION = "a" * 40
TEST_PROJECT = "// !$*UTF8*$!\n{\n\tobjects = {};\n}\n"
TEST_TREE = f"100644 blob {'b' * 40}\tapp/Quizzler.xcodeproj/project.pbxproj\0"


def provider_snapshot_digest() -> str:
    return source_snapshot(
        Path("/fixture"),
        TEST_REVISION,
        command=lambda arguments: TEST_PROJECT if arguments[0] == "show" else TEST_TREE,
    ).digest


def provider_manifest() -> str:
    return json.dumps({
        "formatVersion": 2,
        "candidateId": "candidate-17",
        "release": {"marketingVersion": "1.2.3", "buildNumber": "17", "gitRevision": TEST_REVISION},
        "sourceSnapshot": {"sha256": provider_snapshot_digest()},
    })


class FakeProvider:
    """A no-network provider with observable call ordering."""

    def __init__(self, root: Path, *, failure: str | None = None, build_id: str = "asc-build-17") -> None:
        self.root = root
        self.failure = failure
        self.build_id = build_id
        self.calls: list[str] = []
        self.identity = ReleaseIdentity("1.2.3-17", "1.2.3", "17", "head-a")
        self.archive_path = root / "Quizzler.xcarchive"
        self.ipa_path = root / "Quizzler.ipa"
        self.archive_path.write_bytes(b"final-signed-archive")
        self.ipa_path.write_bytes(b"final-signed-ipa")
        self.uploaded_ipa_paths: list[Path] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.failure == name:
            raise RuntimeError("api_token=must-not-escape")

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify_runtime(self) -> None: self._call("runtime")
    def verify_readiness(self) -> ReleaseIdentity:
        self._call("readiness")
        return self.identity
    def run_full_gate(self) -> None: self._call("gate")
    def verify_signing_ready(self, _: ReleaseIdentity) -> None: self._call("signing")
    def archive(self, _: ReleaseIdentity) -> ArchiveArtifact:
        self._call("archive")
        return ArchiveArtifact(self.archive_path, self._digest(self.archive_path))
    def inspect_archive(self, *_: object) -> None: self._call("inspect")
    def package_ipa(self, *_: object) -> IpaArtifact:
        self._call("package")
        return IpaArtifact(self.ipa_path, self._digest(self.ipa_path))
    def run_final_validation(self, *_: object) -> None: self._call("validation")
    def attended_upload(self, consumer: str, _: ReleaseIdentity, ipa: IpaArtifact) -> str:
        self._call("upload")
        if consumer != PINNED_UPLOAD_CONSUMER:
            raise AssertionError("wrong BWS consumer")
        self.uploaded_ipa_paths.append(ipa.ipa_path)
        return self.build_id
    def poll_exact_build(self, _: ReleaseIdentity, build_id: str, __: IpaArtifact) -> None:
        self._call(f"poll:{build_id}")
    def resolve_compliance(self, _: ReleaseIdentity, build_id: str) -> None: self._call(f"compliance:{build_id}")
    def assign_internal_group(self, _: ReleaseIdentity, build_id: str) -> None: self._call(f"group:{build_id}")
    def verify_receipt(self, _: ReleaseIdentity, build_id: str, __: IpaArtifact) -> None: self._call(f"receipt:{build_id}")
    def record_evidence(self, _: ReleaseIdentity, build_id: str, *__: object) -> None: self._call(f"evidence:{build_id}")
    def notify(self, _: ReleaseIdentity, build_id: str) -> None: self._call(f"notify:{build_id}")


class TestFlightWorkflowTests(unittest.TestCase):
    @staticmethod
    def _package_fixture(root: Path, export_result: int, *, stderr: str = "", rsync_version: str = "rsync version 3.2.7") -> tuple[QuizzlerTestFlightProvider, ReleaseIdentity, ArchiveArtifact, list[list[str]]]:
        identity = ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
        archive_path = root / "app" / "build" / "testflight" / identity.candidate_id / "Quizzler.xcarchive"
        app = archive_path / "Products" / "Applications" / "QuizzleriOS.app"
        app.mkdir(parents=True)
        (app / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.zerodelta.quizzler",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "17",
        }))
        (app / "_CodeSignature").mkdir()
        (app / "_CodeSignature" / "CodeResources").write_bytes(b"signature")
        commands: list[list[str]] = []

        def write_ipa(source: Path, destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as contents:
                for child in source.rglob("*"):
                    if child.is_file():
                        contents.write(child, child.relative_to(source.parent).as_posix())

        def run(arguments: list[str], **_kwargs: object) -> object:
            commands.append(arguments)
            if arguments[0] == "/usr/bin/xcodebuild" and export_result == 0:
                export = Path(arguments[arguments.index("-exportPath") + 1])
                payload = root / "Payload"
                shutil.copytree(app, payload / app.name)
                write_ipa(payload, export / "QuizzleriOS.ipa")
                shutil.rmtree(payload)
            elif arguments[0] == "/usr/bin/ditto":
                write_ipa(Path(arguments[-2]), Path(arguments[-1]))
            return type("Result", (), {
                "returncode": export_result if arguments[0] == "/usr/bin/xcodebuild" else 0,
                "stdout": rsync_version if arguments[0] == "/usr/bin/rsync" else "",
                "stderr": stderr if arguments[0] == "/usr/bin/xcodebuild" else "",
            })()

        provider = QuizzlerTestFlightProvider(root=root, run=run)
        return provider, identity, ArchiveArtifact(archive_path, "0" * 64), commands

    def test_package_ipa_preserves_normal_xcode_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider, identity, archive, commands = self._package_fixture(Path(temporary), 0)
            artifact = provider.package_ipa(identity, archive)
            self.assertTrue(artifact.ipa_path.is_file())
            self.assertEqual([command[0] for command in commands], ["/usr/bin/xcodebuild"])

    def test_package_ipa_uses_fallback_only_for_exact_rsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider, identity, archive, commands = self._package_fixture(root, 1, stderr="Copy failed", rsync_version="openrsync 2.6.9")
            log = archive.archive_path.parent / "IDEDistributionPipeline.log"
            log.write_text("rsync: on remote machine: --extended-attributes: unknown option\n", encoding="utf-8")
            artifact = provider.package_ipa(identity, archive)
            with zipfile.ZipFile(artifact.ipa_path) as contents:
                info = plistlib.loads(contents.read("Payload/QuizzleriOS.app/Info.plist"))
            self.assertEqual(info["CFBundleIdentifier"], "com.zerodelta.quizzler")
            self.assertEqual([command[0] for command in commands], ["/usr/bin/xcodebuild", "/usr/bin/rsync", "/usr/bin/ditto", "/usr/bin/codesign"])
            self.assertIn("--strict", commands[-1])

    def test_package_ipa_does_not_fallback_on_generic_export_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider, identity, archive, commands = self._package_fixture(Path(temporary), 1, stderr="Copy failed\nunrelated export error")
            with self.assertRaisesRegex(WorkflowError, "fixed-command-failed"):
                provider.package_ipa(identity, archive)
            self.assertEqual([command[0] for command in commands], ["/usr/bin/xcodebuild", "/usr/bin/rsync"])

    def test_signing_certificate_status_accepts_only_provisioned_successes(self) -> None:
        for status in ("reused-local-certificate", "reused-existing-profile"):
            with self.subTest(status=status):
                self.assertTrue(_verified_signing_certificate_status(status))
        for status in ("imported", "not-started", "installed", "", None, ["reused-local-certificate"]):
            with self.subTest(status=status):
                self.assertFalse(_verified_signing_certificate_status(status))

    def test_candidate_missing_readiness_stops_before_gate_and_archive_with_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.candidate.joinpath("artifact-attestation.json").unlink()

            class MissingReadinessProvider:
                calls: list[str]

                def __init__(self) -> None:
                    self.calls = []

                def verify_runtime(self) -> None:
                    self.calls.append("runtime")

                def verify_readiness(self) -> ReleaseIdentity:
                    self.calls.append("readiness")
                    raise WorkflowError("immutable-readiness-missing")

                def run_full_gate(self) -> None:
                    self.calls.append("gate")

                def archive(self, _: ReleaseIdentity) -> ArchiveArtifact:
                    self.calls.append("archive")
                    raise AssertionError("archive must not run")

            provider = MissingReadinessProvider()
            events: list[str] = []
            with self.assertRaisesRegex(WorkflowError, "immutable-readiness-missing"):
                run_candidate_workflow(provider, manifest_path=fixture.manifest, attended=True, on_status=events.append)
            self.assertEqual(provider.calls, ["runtime", "readiness"])
            self.assertEqual(events[:2], ["runtime-verification-started", "readiness-verification-started"])
            self.assertNotIn("full-gate-started", events)
            self.assertNotIn("archive-started", events)

    def test_candidate_workflow_stages_outside_ipa_before_attestation_and_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture(root)
            fixture.candidate.joinpath("artifact-attestation.json").unlink()
            provider = FakeProvider(root)

            run_candidate_workflow(provider, manifest_path=fixture.manifest, attended=True, on_status=lambda _: None)

            staged = fixture.candidate / "artifacts" / "QuizzleriOS.ipa"
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), provider.ipa_path.read_bytes())
            self.assertEqual(provider.uploaded_ipa_paths, [staged])
            attestation = json.loads(fixture.candidate.joinpath("artifact-attestation.json").read_text(encoding="utf-8"))
            self.assertEqual(attestation["artifactPath"], "artifacts/QuizzleriOS.ipa")
            self.assertEqual(attestation["artifactSha256"], hashlib.sha256(staged.read_bytes()).hexdigest())
            self.assertEqual(attestation["fileSize"], staged.stat().st_size)

    def test_candidate_workflow_rejects_mismatched_existing_staged_ipa_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = Fixture(root)
            fixture.candidate.joinpath("artifact-attestation.json").unlink()
            staged = fixture.candidate / "artifacts" / "QuizzleriOS.ipa"
            staged.parent.mkdir()
            staged.write_bytes(b"different-existing-artifact")
            provider = FakeProvider(root)

            with self.assertRaisesRegex(WorkflowError, "ipa-staged-artifact-mismatch"):
                run_candidate_workflow(provider, manifest_path=fixture.manifest, attended=True, on_status=lambda _: None)

            self.assertEqual(staged.read_bytes(), b"different-existing-artifact")
            self.assertEqual(provider.uploaded_ipa_paths, [])
            self.assertFalse(fixture.candidate.joinpath("artifact-attestation.json").exists())

    def test_full_workflow_emits_progress_and_uses_pinned_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = FakeProvider(root)
            events: list[str] = []
            state = run_workflow(provider, state_path=root / "state.json", attended=True, on_status=events.append)
            self.assertEqual(state["stage"], "complete")
            self.assertEqual(state["ascBuildId"], "asc-build-17")
            self.assertEqual(provider.calls, [
                "runtime", "readiness", "gate", "signing", "archive", "inspect", "package", "validation", "upload",
                "poll:asc-build-17", "compliance:asc-build-17", "group:asc-build-17", "receipt:asc-build-17",
                "evidence:asc-build-17", "notify:asc-build-17",
            ])
            self.assertIn("full-gate-started", events)
            self.assertIn("pre-upload-boundary-reached", events)
            self.assertEqual(events[-1], "testflight-complete")

    def test_unattended_invocation_does_not_touch_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            provider = FakeProvider(Path(temporary))
            with self.assertRaisesRegex(WorkflowError, "attended-invocation-required"):
                run_workflow(provider, state_path=Path(temporary) / "state.json", attended=False, on_status=lambda _: None)
            self.assertEqual(provider.calls, [])

    def test_pre_upload_failure_has_no_external_mutation_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = FakeProvider(root, failure="validation")
            with self.assertRaisesRegex(WorkflowError, "provider-operation-failed"):
                run_workflow(provider, state_path=root / "state.json", attended=True, on_status=lambda _: None)
            self.assertNotIn("upload", provider.calls)
            self.assertFalse((root / "state.json").exists())

    def test_asc_http_error_exposes_only_safe_status_code(self) -> None:
        provider = QuizzlerTestFlightProvider(
            asc_request=lambda *_: (_ for _ in ()).throw(AscHTTPError(422, "validation")),
            jwt=lambda: "in-memory-jwt",
        )
        with self.assertRaisesRegex(WorkflowError, "^asc-request-http-422$") as raised:
            provider._asc("POST", "/buildUploadFiles", {"secret": "must-not-escape"})
        self.assertNotIn("validation", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_real_asc_requests_refresh_provider_token_each_time(self) -> None:
        tokens: list[str] = []
        issued = iter(("provider-token-1", "provider-token-2"))

        def asc(token: str, _method: str, _path: str, _body: dict | None) -> dict:
            tokens.append(token)
            return {"data": {"type": "fixture", "id": str(len(tokens))}}

        with patch("provision_signing._asc_request", asc), patch(
            "provision_signing._jwt_token", lambda: next(issued)
        ):
            provider = QuizzlerTestFlightProvider()
            provider._asc("GET", "/first")
            provider._asc("GET", "/second")

        self.assertEqual(tokens, ["provider-token-1", "provider-token-2"])
        self.assertNotIn("in-memory-jwt", tokens)

    def test_real_provider_constructs_the_fixed_native_gate_without_running_it(self) -> None:
        commands: list[list[str]] = []

        def fake_run(arguments: list[str], **_kwargs: object) -> object:
            commands.append(arguments)
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        provider = QuizzlerTestFlightProvider(run=fake_run)
        provider.run_full_gate()
        self.assertEqual(commands, [["/bin/bash", str(ROOT / "app" / "test-gate.sh")]])

    def test_signing_readiness_accepts_production_capable_profile_and_rejects_invalid_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "app" / "releases" / "evidence"
            evidence.mkdir(parents=True)
            (root / "app" / "release-config.toml").write_text(
                'bundle_id = "com.zerodelta.quizzler"\n'
                'team_identifier = "4CJ49V6QHW"\n'
                'production_container = "iCloud.com.zerodelta.quizzler.dev"\n',
                encoding="utf-8",
            )
            (evidence / "signing-bootstrap.json").write_text(json.dumps({
                "consumer": "quizzler-asc-provision",
                "bundle_id": "com.zerodelta.quizzler",
                "certificate": {"status": "reused-local-certificate"},
                "profile": {"status": "installed"},
            }), encoding="utf-8")
            entitlements = {
                "application-identifier": "4CJ49V6QHW.com.zerodelta.quizzler",
                "aps-environment": "production",
                "com.apple.developer.icloud-container-environment": ["Production", "Development"],
                "com.apple.developer.icloud-container-identifiers": ["iCloud.com.zerodelta.quizzler.dev"],
            }
            commands: list[list[str]] = []

            def run(arguments: list[str], **_kwargs: object) -> object:
                commands.append(arguments)
                return type("Result", (), {"returncode": 0, "stdout": plistlib.dumps({"Entitlements": entitlements}).decode(), "stderr": ""})()

            QuizzlerTestFlightProvider(root=root, run=run).verify_signing_ready(
                ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
            )
            self.assertEqual(len(commands), 1)

            for invalid_environment in (["Development"], [], ["production"], ["Production", 1], 1, {"Production": True}):
                with self.subTest(invalid_environment=invalid_environment):
                    entitlements["com.apple.developer.icloud-container-environment"] = invalid_environment
                    with self.assertRaisesRegex(WorkflowError, "signing-profile-production-mismatch"):
                        QuizzlerTestFlightProvider(root=root, run=run).verify_signing_ready(
                            ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
                        )

            entitlements["com.apple.developer.icloud-container-environment"] = ["Production", "Development"]
            entitlements["com.apple.developer.icloud-container-identifiers"] = ["iCloud.com.other"]
            with self.assertRaisesRegex(WorkflowError, "signing-profile-production-mismatch"):
                QuizzlerTestFlightProvider(root=root, run=run).verify_signing_ready(
                    ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
                )

    def test_archive_inspection_rejects_wrong_production_container(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app").mkdir()
            (root / "app" / "release-config.toml").write_text(
                'bundle_id = "com.zerodelta.quizzler"\n'
                'team_identifier = "4CJ49V6QHW"\n'
                'production_container = "iCloud.com.zerodelta.quizzler.dev"\n',
                encoding="utf-8",
            )
            archive_path = root / "Quizzler.xcarchive"
            app = archive_path / "Products" / "Applications" / "QuizzleriOS.app"
            app.mkdir(parents=True)
            (app / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": "com.zerodelta.quizzler",
                "CFBundleShortVersionString": "1.2.3",
                "CFBundleVersion": "17",
            }))
            (app / "Assets.car").write_bytes(b"assets")
            entitlements = {
                "application-identifier": "4CJ49V6QHW.com.zerodelta.quizzler",
                "aps-environment": "production",
                "com.apple.developer.icloud-container-environment": "Production",
                "com.apple.developer.icloud-container-identifiers": ["iCloud.com.other"],
                "get-task-allow": False,
            }

            def run(arguments: list[str], **_kwargs: object) -> object:
                stdout = plistlib.dumps(entitlements).decode() if "--entitlements" in arguments else ""
                return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

            provider = QuizzlerTestFlightProvider(root=root, run=run)
            archive = ArchiveArtifact(archive_path, "0" * 64)
            with self.assertRaisesRegex(WorkflowError, "archive-entitlements-invalid"):
                provider.inspect_archive(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), archive)

    def test_exact_build_requires_marketing_and_build_identity_and_checked_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "app" / "releases" / "evidence"
            evidence.mkdir(parents=True)
            (root / "app" / "release-config.toml").write_text('bundle_id = "com.zerodelta.quizzler"\nteam_identifier = "4CJ49V6QHW"\n', encoding="utf-8")
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            requests: list[str] = []
            def asc(_token: str, _method: str, path: str, _body: object) -> dict:
                requests.append(path)
                return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID"}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "in-memory-jwt")
            build_id, result = provider._exact_build(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1")
            self.assertEqual(build_id, "build-1")
            self.assertEqual(result["id"], "build-1")
            self.assertNotIn("include=", requests[0])
            self.assertIn("preReleaseVersion", requests[0])

    def test_exact_build_reads_prerelease_version_when_not_included(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            requests: list[str] = []
            def asc(_token: str, _method: str, path: str, _body: object) -> dict:
                requests.append(path)
                if path.startswith("/apps/app-1/builds?"):
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID"}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}]}
                if path.startswith("/preReleaseVersions/pre-1?"):
                    return {"data": {"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}}
                raise AssertionError(path)
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "in-memory-jwt")
            self.assertEqual(provider._exact_build(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1")[0], "build-1")
            self.assertEqual(len(requests), 2)

    def test_typed_build_upload_uses_server_ranges_and_commits_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "app" / "releases" / "evidence"
            evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            ipa_path = root / "QuizzleriOS.ipa"; ipa_path.write_bytes(b"abcdef")
            bodies: list[tuple[str, str, dict]] = []
            uploads: list[tuple[str, str, bytes, dict]] = []
            def asc(_token: str, method: str, path: str, body: dict | None) -> dict:
                bodies.append((method, path, body or {}))
                if path == "/buildUploads": return {"data": {"type": "buildUploads", "id": "upload-1"}}
                if path == "/buildUploadFiles": return {"data": {"type": "buildUploadFiles", "id": "file-1", "attributes": {"uploadOperations": [{"url": "https://upload.example/one", "method": "PUT", "requestHeaders": [{"name": "x-upload", "value": "one"}], "offset": 0, "length": 3, "deliveryHint": "ignored"}, {"url": "https://upload.example/two", "method": "PUT", "requestHeaders": [{"name": "x-upload", "value": "two"}], "offset": 3, "length": 3}]}}}
                if path == "/buildUploadFiles/file-1": return {"data": {"type": "buildUploadFiles", "id": "file-1"}}
                if path.startswith("/apps/app-1/builds?"):
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID"}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
                raise AssertionError(path)
            def binary(method: str, url: str, payload: bytes, headers: dict) -> int:
                uploads.append((method, url, payload, headers)); return 200
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, binary_request=binary, jwt=lambda: "jwt-is-never-persisted", sleep=lambda _: None)
            prior = os.environ.get("QUIZZLER_TESTFLIGHT_BWS_CONSUMER")
            os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = PINNED_UPLOAD_CONSUMER
            try:
                build_id = provider.attended_upload(PINNED_UPLOAD_CONSUMER, ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), IpaArtifact(ipa_path, hashlib.sha256(b"abcdef").hexdigest()))
            finally:
                if prior is None: os.environ.pop("QUIZZLER_TESTFLIGHT_BWS_CONSUMER", None)
                else: os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = prior
            self.assertEqual(build_id, "build-1")
            self.assertEqual(uploads, [("PUT", "https://upload.example/one", b"abc", {"x-upload": "one"}), ("PUT", "https://upload.example/two", b"def", {"x-upload": "two"})])
            self.assertEqual(bodies[0][2]["data"]["attributes"], {"cfBundleShortVersionString": "1.2.3", "cfBundleVersion": "17", "platform": "IOS"})
            self.assertEqual(bodies[1][2]["data"]["attributes"], {"assetType": "ASSET", "fileName": "QuizzleriOS.ipa", "fileSize": 6, "uti": "com.apple.ipa"})
            self.assertEqual(bodies[2], ("PATCH", "/buildUploadFiles/file-1", {"data": {"type": "buildUploadFiles", "id": "file-1", "attributes": {"uploaded": True, "sourceFileChecksums": {"file": {"algorithm": "MD5", "hash": hashlib.md5(b"abcdef").hexdigest()}}}}}))

    def test_bad_upload_ranges_fail_before_binary_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            ipa = root / "QuizzleriOS.ipa"; ipa.write_bytes(b"abc")
            def asc(_token: str, _method: str, path: str, _body: dict | None) -> dict:
                if path == "/buildUploads": return {"data": {"type": "buildUploads", "id": "upload-1"}}
                return {"data": {"type": "buildUploadFiles", "id": "file-1", "attributes": {"uploadOperations": [{"url": "http://not-https.example", "method": "PUT", "requestHeaders": [], "offset": 0, "length": 3}]}}}
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, binary_request=lambda *_: (_ for _ in ()).throw(AssertionError("must not upload")), jwt=lambda: "jwt", sleep=lambda _: None)
            prior = os.environ.get("QUIZZLER_TESTFLIGHT_BWS_CONSUMER"); os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = PINNED_UPLOAD_CONSUMER
            try:
                with self.assertRaisesRegex(WorkflowError, "asc-upload-operations-invalid"):
                    provider.attended_upload(PINNED_UPLOAD_CONSUMER, ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), IpaArtifact(ipa, hashlib.sha256(b"abc").hexdigest()))
            finally:
                if prior is None: os.environ.pop("QUIZZLER_TESTFLIGHT_BWS_CONSUMER", None)
                else: os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = prior

    def test_upload_conflict_identifies_the_safe_request_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            ipa = root / "QuizzleriOS.ipa"; ipa.write_bytes(b"abc")
            def asc(_token: str, _method: str, _path: str, _body: dict | None) -> dict:
                raise AscHTTPError(409, "conflict", "ENTITY_ERROR.ATTRIBUTE.INVALID")
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "jwt", sleep=lambda _: None)
            prior = os.environ.get("QUIZZLER_TESTFLIGHT_BWS_CONSUMER"); os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = PINNED_UPLOAD_CONSUMER
            try:
                with self.assertRaisesRegex(WorkflowError, "asc-build-upload-create-asc-request-http-409-ENTITY_ERROR.ATTRIBUTE.INVALID"):
                    provider.attended_upload(PINNED_UPLOAD_CONSUMER, ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), IpaArtifact(ipa, hashlib.sha256(b"abc").hexdigest()))
            finally:
                if prior is None: os.environ.pop("QUIZZLER_TESTFLIGHT_BWS_CONSUMER", None)
                else: os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = prior

    def test_duplicate_upload_resumes_with_exact_file_checksum_and_additive_composite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            ipa = root / "QuizzleriOS.ipa"; ipa.write_bytes(b"abc")
            expected_md5 = hashlib.md5(b"abc").hexdigest(); paths: list[str] = []
            def asc(_token: str, _method: str, path: str, _body: dict | None) -> dict:
                paths.append(path)
                if path == "/buildUploads": raise AscHTTPError(409, "conflict", "ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE")
                if path.startswith("/apps/app-1/buildUploads?"):
                    return {"data": [{"type": "buildUploads", "id": "upload-1", "attributes": {"cfBundleShortVersionString": "1.2.3", "cfBundleVersion": "17", "platform": "IOS", "state": {"state": "PROCESSING"}}}]}
                if path.startswith("/buildUploads/upload-1/buildUploadFiles?"):
                    return {"data": [{"type": "buildUploadFiles", "id": "file-1", "attributes": {"assetType": "ASSET", "assetDeliveryState": {"state": "UPLOAD_COMPLETE"}, "sourceFileChecksums": {"file": {"algorithm": "MD5", "hash": expected_md5}, "composite": {"algorithm": "MD5", "hash": "aggregate"}}}}]}
                if path.startswith("/apps/app-1/builds?"):
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID"}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
                raise AssertionError(path)
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, binary_request=lambda *_: (_ for _ in ()).throw(AssertionError("must not reupload")), jwt=lambda: "jwt", sleep=lambda _: None)
            prior = os.environ.get("QUIZZLER_TESTFLIGHT_BWS_CONSUMER"); os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = PINNED_UPLOAD_CONSUMER
            try:
                self.assertEqual(provider.attended_upload(PINNED_UPLOAD_CONSUMER, ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), IpaArtifact(ipa, hashlib.sha256(b"abc").hexdigest())), "build-1")
            finally:
                if prior is None: os.environ.pop("QUIZZLER_TESTFLIGHT_BWS_CONSUMER", None)
                else: os.environ["QUIZZLER_TESTFLIGHT_BWS_CONSUMER"] = prior
            self.assertEqual(paths[0], "/buildUploads")
            self.assertTrue(any(path.startswith("/buildUploads/upload-1/buildUploadFiles?") for path in paths))

    def test_duplicate_upload_recovery_reports_malformed_nested_state_safely(self) -> None:
        def asc(_token: str, _method: str, _path: str, _body: dict | None) -> dict:
            return {"data": [{"type": "buildUploads", "id": "upload-1", "attributes": {"cfBundleShortVersionString": "1.2.3", "cfBundleVersion": "17", "platform": "IOS", "state": {"state": 1}}}]}

        provider = QuizzlerTestFlightProvider(asc_request=asc, jwt=lambda: "jwt")
        identity = ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
        with self.assertRaisesRegex(WorkflowError, "^asc-duplicate-upload-state-invalid$") as raised:
            _call(provider._recover_duplicate_upload, identity, "app-1", "expected")
        self.assertNotEqual(str(raised.exception), "provider-operation-failed")

    def test_duplicate_upload_recovery_reports_malformed_file_state_safely(self) -> None:
        def asc(_token: str, _method: str, path: str, _body: dict | None) -> dict:
            if path.startswith("/apps/app-1/buildUploads?"):
                return {"data": [{"type": "buildUploads", "id": "upload-1", "attributes": {"cfBundleShortVersionString": "1.2.3", "cfBundleVersion": "17", "platform": "IOS", "state": {"state": "PROCESSING"}}}]}
            if path.startswith("/buildUploads/upload-1/buildUploadFiles?"):
                return {"data": [{"type": "buildUploadFiles", "id": "file-1", "attributes": {"assetType": "ASSET", "assetDeliveryState": {"state": 1}}}]}
            raise AssertionError(path)

        provider = QuizzlerTestFlightProvider(asc_request=asc, jwt=lambda: "jwt")
        identity = ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a")
        with self.assertRaisesRegex(WorkflowError, "^asc-duplicate-upload-file-state-invalid$") as raised:
            _call(provider._recover_duplicate_upload, identity, "app-1", "expected")
        self.assertNotEqual(str(raised.exception), "provider-operation-failed")

    def test_duplicate_upload_recovery_rejects_a_different_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            def asc(_token: str, _method: str, path: str, _body: dict | None) -> dict:
                if path.startswith("/apps/app-1/buildUploads?"):
                    return {"data": [{"type": "buildUploads", "id": "upload-1", "attributes": {"cfBundleShortVersionString": "1.2.3", "cfBundleVersion": "17", "platform": "IOS", "state": {"state": "PROCESSING"}}}]}
                if path.startswith("/buildUploads/upload-1/buildUploadFiles?"):
                    return {"data": [{"type": "buildUploadFiles", "id": "file-1", "attributes": {"assetType": "ASSET", "assetDeliveryState": {"state": "UPLOAD_COMPLETE"}, "sourceFileChecksums": {"file": {"algorithm": "MD5", "hash": "different"}}}}]}
                raise AssertionError(path)
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "jwt")
            with self.assertRaisesRegex(WorkflowError, "asc-duplicate-upload-unresolved"):
                provider._recover_duplicate_upload(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "app-1", "expected")

    def test_internal_group_assignment_uses_typed_204_relation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            calls: list[tuple[str, str, dict]] = []
            def asc(_token: str, _method: str, path: str, _body: dict | None) -> dict:
                if path.startswith("/apps/app-1/builds?"):
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID"}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
                if path.startswith("/apps/app-1/betaGroups?"):
                    return {"data": [{"type": "betaGroups", "id": "group-1", "attributes": {"isInternalGroup": True}}]}
                raise AssertionError(path)
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, asc_no_content=lambda method, path, body: calls.append((method, path, body)), jwt=lambda: "jwt")
            provider.assign_internal_group(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1")
            self.assertEqual(calls, [("POST", "/builds/build-1/relationships/betaGroups", {"data": [{"type": "betaGroups", "id": "group-1"}]})])

    def test_unanswered_encryption_is_answered_false_and_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            calls: list[tuple[str, str, object]] = []
            observed = [None, False]
            def asc(_token: str, method: str, path: str, body: object) -> dict:
                calls.append((method, path, body))
                if path.startswith("/apps/app-1/builds?"):
                    value = observed.pop(0)
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID", "usesNonExemptEncryption": value}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
                if path == "/builds/build-1":
                    return {"data": {"type": "builds", "id": "build-1"}}
                raise AssertionError(path)
            events: list[str] = []
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "jwt", on_status=events.append)
            provider.resolve_compliance(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1")
            self.assertEqual(calls[1], ("PATCH", "/builds/build-1", {"data": {"type": "builds", "id": "build-1", "attributes": {"usesNonExemptEncryption": False}}}))
            self.assertEqual(events[-1], "asc-compliance-exempt")

    def test_nonexempt_encryption_requires_declaration_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root / "app" / "releases" / "evidence"; evidence.mkdir(parents=True)
            (evidence / "testflight-internal-group.json").write_text('{"formatVersion":"1.0.0","appId":"app-1","bundleId":"com.zerodelta.quizzler","groupId":"group-1","isInternalGroup":true}', encoding="utf-8")
            def asc(_token: str, _method: str, path: str, _body: object) -> dict:
                if path.startswith("/apps/app-1/builds?"):
                    return {"data": [{"type": "builds", "id": "build-1", "attributes": {"version": "17", "processingState": "VALID", "usesNonExemptEncryption": True}, "relationships": {"preReleaseVersion": {"data": {"id": "pre-1"}}}}], "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.2.3"}}]}
                raise AssertionError(path)
            provider = QuizzlerTestFlightProvider(root=root, asc_request=asc, jwt=lambda: "jwt")
            with self.assertRaisesRegex(WorkflowError, "compliance-evidence-missing"):
                provider.resolve_compliance(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1")

    def test_dirty_candidate_is_rejected_before_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            readiness = root / "app" / "releases" / "state" / "current-readiness.json"
            manifest = root / "app" / "releases" / "state" / "candidate.json"
            manifest.parent.mkdir(parents=True)
            readiness.write_text('{"candidateManifest":"app/releases/state/candidate.json"}', encoding="utf-8")
            manifest.write_text(provider_manifest(), encoding="utf-8")
            commands: list[list[str]] = []
            def run(arguments: list[str], **_kwargs: object) -> object:
                commands.append(arguments)
                if arguments[-1] == "HEAD":
                    output = f"{TEST_REVISION}\n"
                elif "status" in arguments:
                    output = "?? app/source.swift\0"
                elif "show" in arguments:
                    output = TEST_PROJECT
                elif "ls-tree" in arguments:
                    output = TEST_TREE
                else:
                    output = ""
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            provider = QuizzlerTestFlightProvider(root=root, run=run)
            with self.assertRaisesRegex(WorkflowError, "candidate-working-tree-dirty"):
                provider.verify_readiness()
            self.assertTrue(any(command[command.index("status") + 1:command.index("status") + 4] == ["--porcelain=v1", "-z", "--untracked-files=all"] for command in commands if "status" in command))

    def test_ignored_release_output_does_not_dirty_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); state = root / "app" / "releases" / "state"; state.mkdir(parents=True)
            (state / "current-readiness.json").write_text('{"candidateManifest":"app/releases/state/candidate.json"}', encoding="utf-8")
            (state / "candidate.json").write_text(provider_manifest(), encoding="utf-8")
            def run(arguments: list[str], **_kwargs: object) -> object:
                if arguments[-1] == "HEAD":
                    output = f"{TEST_REVISION}\n"
                elif "status" in arguments:
                    output = "?? app/build/testflight/candidate-17/\0"
                elif "show" in arguments:
                    output = TEST_PROJECT
                elif "ls-tree" in arguments:
                    output = TEST_TREE
                else:
                    output = ""
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            identity = QuizzlerTestFlightProvider(root=root, run=run).verify_readiness()
            self.assertEqual(identity.candidate_id, "candidate-17")

    def test_readiness_uses_injected_project_python_not_system_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); state = root / "app" / "releases" / "state"; state.mkdir(parents=True)
            (state / "current-readiness.json").write_text('{"candidateManifest":"app/releases/state/candidate.json"}', encoding="utf-8")
            (state / "candidate.json").write_text(provider_manifest(), encoding="utf-8")
            commands: list[list[str]] = []
            def run(arguments: list[str], **_kwargs: object) -> object:
                commands.append(arguments)
                if arguments[-1] == "HEAD":
                    output = f"{TEST_REVISION}\n"
                elif "show" in arguments:
                    output = TEST_PROJECT
                elif "ls-tree" in arguments:
                    output = TEST_TREE
                else:
                    output = ""
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            QuizzlerTestFlightProvider(root=root, run=run, project_python=Path("/project/python3")).verify_readiness()
            self.assertEqual(commands[0][0], "/project/python3")
            self.assertNotIn("/usr/bin/python3", commands[0])
            self.assertNotIn("--require", commands[0])
            self.assertNotIn("production-schema,device-acceptance", commands[0])

    def test_receipt_evidence_append_binds_only_public_identity_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "Quizzler.xcarchive"; archive_path.write_bytes(b"archive")
            ipa_path = root / "QuizzleriOS.ipa"; ipa_path.write_bytes(b"ipa")
            provider = QuizzlerTestFlightProvider(root=root)
            provider._group = {"appId": "app-1", "groupId": "group-1"}
            archive = ArchiveArtifact(archive_path, hashlib.sha256(b"archive").hexdigest())
            ipa = IpaArtifact(ipa_path, hashlib.sha256(b"ipa").hexdigest())
            provider.record_evidence(ReleaseIdentity("candidate-17", "1.2.3", "17", "head-a"), "build-1", archive, ipa)
            receipt = root / "app" / "releases" / "evidence" / "testflight-receipts.jsonl"
            value = receipt.read_text(encoding="utf-8")
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertIn('"ascBuildId":"build-1"', value)
            self.assertNotRegex(value, r"(?i)token|secret|password|credential|api[_-]?key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
