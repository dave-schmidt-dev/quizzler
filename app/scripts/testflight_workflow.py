#!/usr/bin/env python3
"""Fail-closed, resumable attended TestFlight promotion for Quizzler.

The sole public entrypoint delegates to this module through the fixed BWS
consumer.  Unit tests inject fakes; production uses ``QuizzlerTestFlightProvider``
and never accepts a caller-supplied executable, root, bundle ID, or endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from release_adapter import AdapterError, bind_artifact_attestation, central_runtime
from release_candidate import CandidateSourceError, assert_candidate_scope_clean, source_snapshot

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "app" / "releases" / "state" / "testflight-workflow.json"
PINNED_UPLOAD_CONSUMER = "quizzler-testflight-upload"
BWS_MARKER = "QUIZZLER_TESTFLIGHT_BWS_CONSUMER"
CONFIG_PATH = ROOT / "app" / "release-config.toml"
PROJECT_PYTHON = Path("/opt/homebrew/bin/python3")
READINESS_PATH = ROOT / "app" / "releases" / "state" / "current-readiness.json"
SIGNING_EVIDENCE_PATH = ROOT / "app" / "releases" / "evidence" / "signing-bootstrap.json"
INTERNAL_GROUP_EVIDENCE_PATH = ROOT / "app" / "releases" / "evidence" / "testflight-internal-group.json"
COMPLIANCE_EVIDENCE_PATH = ROOT / "app" / "releases" / "evidence" / "testflight-compliance.json"
BUILD_ROOT = ROOT / "app" / "build" / "testflight"
PROJECT = ROOT / "app" / "Quizzler.xcodeproj"
SCHEME = "Quizzler"
TARGET = "QuizzleriOS"
BUNDLE_ID = "com.zerodelta.quizzler"
SAFE_EVENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE = re.compile(r"(?:secret|token|password|credential|api[_-]?key|private[_-]?(?:key|credential)|authorization|\.p8)", re.I)


class WorkflowError(ValueError):
    """A public, credential-free stop code."""


@dataclass(frozen=True)
class ReleaseIdentity:
    """The frozen identity returned by the immutable readiness boundary."""

    candidate_id: str
    marketing_version: str
    build_number: str
    git_revision: str

    def as_dict(self) -> dict[str, str]:
        return {"candidateId": self.candidate_id, "marketingVersion": self.marketing_version,
                "buildNumber": self.build_number, "gitRevision": self.git_revision}


@dataclass(frozen=True)
class ArchiveArtifact:
    archive_path: Path
    archive_sha256: str


@dataclass(frozen=True)
class IpaArtifact:
    ipa_path: Path
    ipa_sha256: str


class ReleaseProvider(Protocol):
    def verify_runtime(self) -> None: ...
    def verify_readiness(self) -> ReleaseIdentity: ...
    def run_full_gate(self) -> None: ...
    def verify_signing_ready(self, identity: ReleaseIdentity) -> None: ...
    def archive(self, identity: ReleaseIdentity) -> ArchiveArtifact: ...
    def inspect_archive(self, identity: ReleaseIdentity, archive: ArchiveArtifact) -> None: ...
    def package_ipa(self, identity: ReleaseIdentity, archive: ArchiveArtifact) -> IpaArtifact: ...
    def run_final_validation(self, identity: ReleaseIdentity, archive: ArchiveArtifact, ipa: IpaArtifact) -> None: ...
    def attended_upload(self, consumer: str, identity: ReleaseIdentity, ipa: IpaArtifact) -> str: ...
    def poll_exact_build(self, identity: ReleaseIdentity, build_id: str, ipa: IpaArtifact) -> None: ...
    def resolve_compliance(self, identity: ReleaseIdentity, build_id: str) -> None: ...
    def assign_internal_group(self, identity: ReleaseIdentity, build_id: str) -> None: ...
    def verify_receipt(self, identity: ReleaseIdentity, build_id: str, ipa: IpaArtifact) -> None: ...
    def record_evidence(self, identity: ReleaseIdentity, build_id: str, archive: ArchiveArtifact, ipa: IpaArtifact) -> None: ...
    def notify(self, identity: ReleaseIdentity, build_id: str) -> None: ...


def _safe_event(value: str) -> str:
    if not SAFE_EVENT.fullmatch(value):
        raise WorkflowError("workflow-event-invalid")
    return value


def _sha256(path: Path) -> str:
    """Hash a final file or a deterministic archive tree without following links."""
    digest = hashlib.sha256()
    try:
        if path.is_dir():
            for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
                if child.is_symlink() or not child.is_file():
                    if child.is_symlink():
                        raise OSError("archive symlink")
                    continue
                digest.update(child.relative_to(path).as_posix().encode() + b"\0")
                with child.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
        else:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    except OSError as exc:
        raise WorkflowError("signed-artifact-unreadable") from exc
    return digest.hexdigest()


def _artifact(kind: str, artifact: ArchiveArtifact | IpaArtifact) -> dict[str, str]:
    path = artifact.archive_path if kind == "archive" else artifact.ipa_path
    declared = artifact.archive_sha256 if kind == "archive" else artifact.ipa_sha256
    if not isinstance(declared, str) or not SHA256.fullmatch(declared):
        raise WorkflowError("signed-artifact-digest-invalid")
    if _sha256(path) != declared:
        raise WorkflowError("signed-artifact-digest-mismatch")
    return {"path": str(path), "sha256": declared}


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(code) from exc
    if not isinstance(value, dict):
        raise WorkflowError(code)
    return value


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = _load_json(path, "testflight-state-unreadable")
    if not isinstance(value, dict) or set(value) != {"formatVersion", "identity", "stage", "archive", "ipa", "ascBuildId"}:
        raise WorkflowError("testflight-state-invalid")
    if value["formatVersion"] == "1.0.0":
        raise WorkflowError("testflight-state-v1-rejected")
    if value["formatVersion"] != "2.0.0" or value["stage"] not in {"prepared", "uploaded", "complete"}:
        raise WorkflowError("testflight-state-invalid")
    if not isinstance(value["identity"], dict) or set(value["identity"]) != {"candidateId", "marketingVersion", "buildNumber", "gitRevision"}:
        raise WorkflowError("testflight-state-invalid")
    if not isinstance(value["archive"], dict) or not isinstance(value["ipa"], dict):
        raise WorkflowError("testflight-state-invalid")
    if value["stage"] == "prepared" and value["ascBuildId"] is not None:
        raise WorkflowError("testflight-state-invalid")
    if value["stage"] != "prepared" and (not isinstance(value["ascBuildId"], str) or not value["ascBuildId"]):
        raise WorkflowError("testflight-state-invalid")
    return value


def _write_state(path: Path, value: Mapping[str, Any]) -> None:
    if SENSITIVE.search(json.dumps(value, sort_keys=True)):
        raise WorkflowError("testflight-state-sensitive-data")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise WorkflowError("testflight-state-write-failed") from exc


def _emit(on_status: Callable[[str], None], event: str) -> None:
    on_status(_safe_event(event))


def _call(operation: Callable[..., Any], *args: Any) -> Any:
    try:
        return operation(*args)
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("provider-operation-failed") from exc


def _identity_matches(state: Mapping[str, Any], identity: ReleaseIdentity) -> None:
    if state["identity"] != identity.as_dict():
        raise WorkflowError("testflight-resume-identity-drift")


def _candidate_identity(manifest: Mapping[str, Any]) -> ReleaseIdentity:
    if manifest.get("formatVersion") != 2:
        raise WorkflowError("candidate-manifest-v1-rejected")
    release = manifest.get("release")
    if not isinstance(release, Mapping) or manifest.get("lane") != "standard":
        raise WorkflowError("candidate-manifest-invalid")
    return ReleaseIdentity(
        str(manifest.get("candidateId")), str(release.get("marketingVersion")),
        str(release.get("buildNumber")), str(release.get("gitRevision")),
    )


def _candidate_records(manifest_path: Path, runtime: Path = ROOT / "app" / "vendor" / "apple-release" / "runtime") -> tuple[Any, list[dict[str, Any]]]:
    try:
        central = central_runtime(runtime)
        manifest = central.load_candidate_manifest(manifest_path)
        if manifest.get("formatVersion") != 2:
            raise WorkflowError("candidate-manifest-v1-rejected")
        return central, central.read_candidate_ledger_v2(manifest_path.parent)
    except (AdapterError, ValueError) as exc:
        raise WorkflowError(str(exc)) from exc


def _attested_ipa(manifest_path: Path, central: Any) -> IpaArtifact:
    attestation_path = manifest_path.parent / "artifact-attestation.json"
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        if attestation.get("formatVersion") != 2 or attestation.get("artifactKind") != "ipa":
            raise WorkflowError("artifact-attestation-invalid")
        relative = Path(attestation.get("artifactPath", ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise WorkflowError("artifact-attestation-path-invalid")
        path = (manifest_path.parent / relative).resolve()
        path.relative_to(manifest_path.parent.resolve())
        artifact = IpaArtifact(path, str(attestation.get("artifactSha256")))
        _artifact("ipa", artifact)
        if artifact.ipa_path.stat().st_size != attestation.get("fileSize"):
            raise WorkflowError("artifact-attestation-drift")
        return artifact
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, WorkflowError):
            raise
        raise WorkflowError("artifact-attestation-invalid") from exc


def run_candidate_workflow(
    provider: ReleaseProvider,
    *,
    manifest_path: Path,
    attended: bool,
    on_status: Callable[[str], None],
    runtime: Path = ROOT / "app" / "vendor" / "apple-release" / "runtime",
) -> Mapping[str, Any]:
    """Prepare, attest, qualify, and upload one v2 candidate exactly once."""

    if not attended:
        raise WorkflowError("attended-invocation-required")
    central, records = _candidate_records(manifest_path, runtime)
    manifest = central.load_candidate_manifest(manifest_path)
    identity = _candidate_identity(manifest)
    by_transition = {record["transition"]: record for record in records}
    if "internalTestFlightReceipted" in by_transition:
        _emit(on_status, "testflight-already-complete")
        return {"formatVersion": 2, "candidateId": identity.candidate_id, "stage": "complete", "ascBuildId": by_transition["internalTestFlightReceipted"]["details"].get("ascBuildId")}

    try:
        if "artifact-attested" not in by_transition:
            _emit(on_status, "runtime-verification-started"); _call(provider.verify_runtime)
        if "readiness-verified" not in by_transition:
            _emit(on_status, "readiness-verification-started")
            observed = _call(provider.verify_readiness)
            if isinstance(observed, ReleaseIdentity):
                _identity_matches({"identity": identity.as_dict()}, observed)
            record = central.append_candidate_transition(manifest_path, "readiness-verified", details={"sourceDigest": manifest["sourceSnapshot"]["sha256"]})
            by_transition["readiness-verified"] = record
            _emit(on_status, "readiness-verified")
        if "artifact-attested" not in by_transition:
            _emit(on_status, "full-gate-started"); _call(provider.run_full_gate)
            _emit(on_status, "signing-readiness-started"); _call(provider.verify_signing_ready, identity)
            _emit(on_status, "archive-started"); archive = _call(provider.archive, identity); archive_record = _artifact("archive", archive)
            _emit(on_status, "archive-inspection-started"); _call(provider.inspect_archive, identity, archive)
            _emit(on_status, "ipa-packaging-started"); ipa = _call(provider.package_ipa, identity, archive); _artifact("ipa", ipa)
            _emit(on_status, "final-validation-started"); _call(provider.run_final_validation, identity, archive, ipa)
            bind_artifact_attestation(manifest_path, ipa.ipa_path, artifact_kind="ipa")
            record = central.append_candidate_transition(manifest_path, "artifact-attested", details={"artifactSha256": ipa.ipa_sha256, "archive": archive_record})
            by_transition["artifact-attested"] = record; _emit(on_status, "artifact-attested")
        else:
            ipa = _attested_ipa(manifest_path, central)
            archive_data = by_transition["artifact-attested"]["details"].get("archive")
            if not isinstance(archive_data, Mapping):
                raise WorkflowError("archive-attestation-missing")
            archive = ArchiveArtifact(Path(str(archive_data.get("path"))), str(archive_data.get("sha256")))
            _artifact("archive", archive)
        bound = by_transition.get("asc-build-bound")
        if bound is None:
            _emit(on_status, "attended-upload-boundary-started")
            build_id = _call(provider.attended_upload, PINNED_UPLOAD_CONSUMER, identity, ipa)
            if not isinstance(build_id, str) or not build_id:
                raise WorkflowError("asc-build-id-invalid")
            bound = central.append_candidate_transition(manifest_path, "asc-build-bound", details={"ascBuildId": build_id, "artifactSha256": ipa.ipa_sha256})
            by_transition["asc-build-bound"] = bound
        build_id = str(bound["details"]["ascBuildId"])
        if "exact-build-verified" not in by_transition:
            _call(provider.poll_exact_build, identity, build_id, ipa)
            central.append_candidate_transition(manifest_path, "exact-build-verified", details={"ascBuildId": build_id})
        if "compliance-resolved" not in by_transition:
            _call(provider.resolve_compliance, identity, build_id); central.append_candidate_transition(manifest_path, "compliance-resolved", details={"ascBuildId": build_id})
        if "internal-group-assigned" not in by_transition:
            _call(provider.assign_internal_group, identity, build_id); central.append_candidate_transition(manifest_path, "internal-group-assigned", details={"ascBuildId": build_id})
        if "internalTestFlightReceipted" not in by_transition:
            _call(provider.verify_receipt, identity, build_id, ipa)
            _call(provider.record_evidence, identity, build_id, archive, ipa)
            _call(provider.notify, identity, build_id)
            central.append_candidate_transition(manifest_path, "internalTestFlightReceipted", details={"ascBuildId": build_id, "artifactSha256": ipa.ipa_sha256})
        _emit(on_status, "testflight-complete")
        return {"formatVersion": 2, "candidateId": identity.candidate_id, "stage": "complete", "ascBuildId": build_id}
    except (WorkflowError, AdapterError, ValueError) as exc:
        try:
            central.append_candidate_transition(manifest_path, "failed", details={"step": "candidate-workflow", "code": str(exc)})
        except ValueError:
            pass
        raise


class QuizzlerTestFlightProvider:
    """Concrete attended provider with fixed Quizzler commands and ASC paths.

    Binary upload requests are typed from Apple's documented Build Upload contract.
    Every externally supplied upload operation is constrained to a contiguous HTTPS
    range of the final IPA and its response is never sent to the terminal.
    """

    def __init__(self, *, root: Path = ROOT, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 asc_request: Callable[[str, str, str, dict[str, Any] | None], dict[str, Any]] | None = None,
                 asc_no_content: Callable[[str, str, dict[str, Any]], None] | None = None,
                 binary_request: Callable[[str, str, bytes, Mapping[str, str]], int] | None = None,
                 jwt: Callable[[], str] | None = None, sleep: Callable[[float], None] | None = None,
                 project_python: Path = PROJECT_PYTHON,
                 on_status: Callable[[str], None] | None = None) -> None:
        self.root = root.resolve()
        self.run = run
        self.on_status = on_status or (lambda _event: None)
        self._asc_request = asc_request
        self._asc_no_content = asc_no_content
        self._binary_request = binary_request
        self._jwt = jwt
        self._sleep = sleep or time.sleep
        self.project_python = project_python
        self._app_id: str | None = None
        self._group: dict[str, str] | None = None

    def _status(self, event: str) -> None:
        _emit(self.on_status, event)

    def _command(self, event: str, arguments: list[str], *, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
        self._status(f"{event}-started")
        try:
            result = self.run(arguments, cwd=self.root, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkflowError("fixed-command-unavailable") from exc
        if result.returncode != 0:
            raise WorkflowError("fixed-command-failed")
        self._status(f"{event}-complete")
        return result

    def _config(self) -> dict[str, Any]:
        try:
            with (self.root / "app" / "release-config.toml").open("rb") as handle:
                config = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise WorkflowError("release-config-invalid") from exc
        if config.get("bundle_id") != BUNDLE_ID or config.get("team_identifier") != "4CJ49V6QHW":
            raise WorkflowError("release-config-identity-drift")
        return config

    def _token(self) -> str:
        if self._jwt is not None:
            return self._jwt()
        try:
            from provision_signing import _asc_request, _jwt_token  # type: ignore
        except ImportError as exc:
            raise WorkflowError("asc-client-unavailable") from exc
        self._asc_request = _asc_request
        return _jwt_token()

    def _asc(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._asc_request is None:
            token = self._token()
        else:
            token = self._token() if self._jwt is not None else "in-memory-jwt"
        self._status("asc-request-started")
        try:
            response = self._asc_request(token, method, path, body) if self._asc_request else None
        except Exception as exc:
            raise WorkflowError("asc-request-failed") from exc
        self._status("asc-request-complete")
        if not isinstance(response, dict):
            raise WorkflowError("asc-response-invalid")
        return response

    def _asc_empty(self, method: str, path: str, body: dict[str, Any]) -> None:
        """Perform only typed 204 ASC relationship mutations with an in-memory JWT."""
        if self._asc_no_content is not None:
            self._status("asc-request-started")
            try:
                self._asc_no_content(method, path, body)
            except Exception as exc:
                raise WorkflowError("asc-request-failed") from exc
            self._status("asc-request-complete")
            return
        token = self._token()
        payload = json.dumps(body, separators=(",", ":")).encode()
        request = Request(
            "https://api.appstoreconnect.apple.com/v1" + path,
            data=payload,
            method=method,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        self._status("asc-request-started")
        try:
            with urlopen(request, timeout=30) as response:
                if response.status != 204:
                    raise WorkflowError("asc-response-invalid")
        except WorkflowError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise WorkflowError("asc-request-failed") from exc
        self._status("asc-request-complete")

    def _binary(self, method: str, url: str, payload: bytes, headers: Mapping[str, str]) -> None:
        """Upload one server-authorized IPA range without outputting request details."""
        self._status("asc-binary-upload-started")
        try:
            if self._binary_request is not None:
                status = self._binary_request(method, url, payload, headers)
            else:
                request = Request(url, data=payload, method=method, headers=dict(headers))
                with urlopen(request, timeout=120) as response:
                    status = response.status
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise WorkflowError("asc-binary-upload-failed") from exc
        if status not in {200, 201, 202, 204}:
            raise WorkflowError("asc-binary-upload-failed")
        self._status("asc-binary-upload-complete")

    @staticmethod
    def _resource(response: Mapping[str, Any], kind: str) -> dict[str, Any]:
        data = response.get("data")
        if not isinstance(data, dict) or data.get("type") != kind or not isinstance(data.get("id"), str) or not data["id"]:
            raise WorkflowError("asc-response-invalid")
        return data

    def _load_group(self) -> dict[str, str]:
        value = _load_json(self.root / "app" / "releases" / "evidence" / "testflight-internal-group.json", "internal-group-evidence-missing")
        if set(value) != {"formatVersion", "appId", "bundleId", "groupId", "isInternalGroup"} or value.get("formatVersion") != "1.0.0":
            raise WorkflowError("internal-group-evidence-invalid")
        if value.get("bundleId") != BUNDLE_ID or value.get("isInternalGroup") is not True:
            raise WorkflowError("internal-group-evidence-invalid")
        if not all(isinstance(value.get(key), str) and SAFE_ID.fullmatch(value[key]) for key in ("appId", "groupId")):
            raise WorkflowError("internal-group-evidence-invalid")
        return {"appId": value["appId"], "groupId": value["groupId"]}

    def _load_compliance(self) -> dict[str, str]:
        value = _load_json(self.root / "app" / "releases" / "evidence" / "testflight-compliance.json", "compliance-evidence-missing")
        if set(value) != {"formatVersion", "appId", "bundleId", "declarationId"} or value.get("formatVersion") != "1.0.0":
            raise WorkflowError("compliance-evidence-invalid")
        if value.get("bundleId") != BUNDLE_ID or not all(isinstance(value.get(key), str) and SAFE_ID.fullmatch(value[key]) for key in ("appId", "declarationId")):
            raise WorkflowError("compliance-evidence-invalid")
        return {"appId": value["appId"], "declarationId": value["declarationId"]}

    def verify_runtime(self) -> None:
        if self.root != ROOT.resolve() or not (self.root / "app" / "deploy-testflight").is_file():
            raise WorkflowError("quizzler-root-identity-drift")
        self._config()
        if not shutil.which("/usr/bin/git") or not shutil.which("/usr/bin/xcodebuild") or not shutil.which("/usr/bin/codesign"):
            raise WorkflowError("release-toolchain-unavailable")

    def verify_readiness(self) -> ReleaseIdentity:
        readiness = self.root / "app" / "releases" / "state" / "current-readiness.json"
        if not readiness.is_file():
            raise WorkflowError("immutable-readiness-missing")
        if not self.project_python.is_absolute() or not self.project_python.name.startswith("python"):
            raise WorkflowError("project-python-invalid")
        self._command("immutable-readiness", [str(self.project_python), str(self.root / "app" / "scripts" / "release_readiness.py"), str(readiness),
                                             "--repository", str(self.root), "--require", "production-schema,device-acceptance"])
        bundle = _load_json(readiness, "immutable-readiness-invalid")
        manifest_ref = bundle.get("candidateManifest")
        if not isinstance(manifest_ref, str) or not manifest_ref or Path(manifest_ref).is_absolute() or ".." in Path(manifest_ref).parts:
            raise WorkflowError("immutable-readiness-invalid")
        manifest = _load_json(self.root / manifest_ref, "candidate-manifest-unreadable")
        release = manifest.get("release")
        if not isinstance(release, dict) or not all(isinstance(release.get(key), str) and release[key] for key in ("marketingVersion", "buildNumber", "gitRevision")):
            raise WorkflowError("candidate-manifest-invalid")
        candidate = manifest.get("candidateId")
        if not isinstance(candidate, str) or not SAFE_ID.fullmatch(candidate):
            raise WorkflowError("candidate-manifest-invalid")
        actual = self._command("git-revision", ["/usr/bin/git", "-C", str(self.root), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        if actual != release["gitRevision"]:
            raise WorkflowError("git-revision-drift")
        try:
            assert_candidate_scope_clean(
                self.root,
                command=lambda arguments: self._command(
                    "git-candidate-cleanliness",
                    ["/usr/bin/git", "-C", str(self.root), *arguments],
                    timeout=30,
                ).stdout,
            )
            snapshot = source_snapshot(
                self.root,
                actual,
                command=lambda arguments: self._command(
                    "git-candidate-source-snapshot",
                    ["/usr/bin/git", "-C", str(self.root), *arguments],
                    timeout=30,
                ).stdout,
            )
        except CandidateSourceError as exc:
            raise WorkflowError(str(exc)) from exc
        if snapshot.digest != manifest.get("sourceSnapshot", {}).get("sha256"):
            raise WorkflowError("candidate-source-snapshot-drift")
        return ReleaseIdentity(candidate, release["marketingVersion"], release["buildNumber"], release["gitRevision"])

    def run_full_gate(self) -> None:
        self._command("native-gate", ["/usr/bin/bash", str(self.root / "app" / "test-gate.sh")])

    def verify_signing_ready(self, identity: ReleaseIdentity) -> None:
        evidence = _load_json(self.root / "app" / "releases" / "evidence" / "signing-bootstrap.json", "signing-evidence-missing")
        if evidence.get("consumer") != "quizzler-asc-provision" or evidence.get("bundle_id") != BUNDLE_ID:
            raise WorkflowError("signing-evidence-identity-drift")
        if not isinstance(evidence.get("certificate"), dict) or evidence["certificate"].get("status") != "imported":
            raise WorkflowError("signing-evidence-invalid")
        if not isinstance(evidence.get("profile"), dict) or evidence["profile"].get("status") != "installed":
            raise WorkflowError("signing-evidence-invalid")
        profile = Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles" / "quizzler-ios-app-store.provisionprofile"
        decoded = self._command("signing-profile", ["/usr/bin/security", "cms", "-D", "-i", str(profile)], timeout=60).stdout.encode()
        try:
            values = plistlib.loads(decoded)
        except (plistlib.InvalidFileException, ValueError) as exc:
            raise WorkflowError("signing-profile-invalid") from exc
        entitlements = values.get("Entitlements") if isinstance(values, dict) else None
        expected = f"4CJ49V6QHW.{BUNDLE_ID}"
        if not isinstance(entitlements, dict) or entitlements.get("application-identifier") != expected or entitlements.get("aps-environment") != "production":
            raise WorkflowError("signing-profile-production-mismatch")
        if entitlements.get("com.apple.developer.icloud-container-environment") != "Production" or entitlements.get("com.apple.developer.icloud-container-identifiers") != ["iCloud.com.zerodelta.quizzler"]:
            raise WorkflowError("signing-profile-production-mismatch")

    def _candidate_paths(self, identity: ReleaseIdentity) -> tuple[Path, Path, Path]:
        if not SAFE_ID.fullmatch(identity.candidate_id):
            raise WorkflowError("candidate-manifest-invalid")
        candidate_root = self.root / "app" / "build" / "testflight" / identity.candidate_id
        return candidate_root, candidate_root / "Quizzler.xcarchive", candidate_root / "export"

    def archive(self, identity: ReleaseIdentity) -> ArchiveArtifact:
        candidate_root, archive, _ = self._candidate_paths(identity)
        if archive.exists():
            raise WorkflowError("archive-path-already-exists")
        candidate_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._command("release-archive", ["/usr/bin/xcodebuild", "-project", str(self.root / "app" / "Quizzler.xcodeproj"), "-scheme", SCHEME,
                                          "-configuration", "Release", "-destination", "generic/platform=iOS", "-archivePath", str(archive), "archive"])
        if not archive.is_dir():
            raise WorkflowError("archive-missing")
        return ArchiveArtifact(archive, _sha256(archive))

    def inspect_archive(self, identity: ReleaseIdentity, archive: ArchiveArtifact) -> None:
        app = archive.archive_path / "Products" / "Applications" / f"{TARGET}.app"
        info_path = app / "Info.plist"
        assets = app / "Assets.car"
        self._command("archive-signature", ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], timeout=120)
        entitlements = self._command("archive-entitlements", ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(app)], timeout=120).stdout.encode()
        self._command("archive-assets", ["/usr/bin/xcrun", "assetutil", "--info", str(assets)], timeout=120)
        try:
            info = plistlib.loads(info_path.read_bytes())
            entitlement_values = plistlib.loads(entitlements)
        except (OSError, plistlib.InvalidFileException, ValueError) as exc:
            raise WorkflowError("archive-inspection-invalid") from exc
        if info.get("CFBundleIdentifier") != BUNDLE_ID or str(info.get("CFBundleShortVersionString")) != identity.marketing_version or str(info.get("CFBundleVersion")) != identity.build_number:
            raise WorkflowError("archive-identity-drift")
        if not assets.is_file() or assets.stat().st_size <= 0 or not isinstance(entitlement_values, dict):
            raise WorkflowError("archive-assets-invalid")
        if entitlement_values.get("application-identifier") != f"4CJ49V6QHW.{BUNDLE_ID}" or entitlement_values.get("aps-environment") != "production":
            raise WorkflowError("archive-entitlements-invalid")
        if entitlement_values.get("com.apple.developer.icloud-container-environment") != "Production" or entitlement_values.get("get-task-allow") is not False:
            raise WorkflowError("archive-entitlements-invalid")

    def package_ipa(self, identity: ReleaseIdentity, archive: ArchiveArtifact) -> IpaArtifact:
        candidate_root, _, export = self._candidate_paths(identity)
        options = candidate_root / "ExportOptions.plist"
        if export.exists():
            raise WorkflowError("ipa-path-already-exists")
        options.write_bytes(plistlib.dumps({"method": "app-store", "signingStyle": "manual", "stripSwiftSymbols": True}))
        try:
            self._command("ipa-export", ["/usr/bin/xcodebuild", "-exportArchive", "-archivePath", str(archive.archive_path), "-exportPath", str(export), "-exportOptionsPlist", str(options)])
        finally:
            options.unlink(missing_ok=True)
        ipa = export / f"{TARGET}.ipa"
        if not ipa.is_file() or ipa.stat().st_size <= 0:
            raise WorkflowError("ipa-missing")
        try:
            with zipfile.ZipFile(ipa) as contents:
                names = set(contents.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            raise WorkflowError("ipa-invalid") from exc
        if any(name.startswith("__MACOSX/") or name.endswith(".DS_Store") for name in names) or f"Payload/{TARGET}.app/Info.plist" not in names:
            raise WorkflowError("ipa-regenerated-metadata-invalid")
        return IpaArtifact(ipa, _sha256(ipa))

    def run_final_validation(self, identity: ReleaseIdentity, archive: ArchiveArtifact, ipa: IpaArtifact) -> None:
        _artifact("archive", archive)
        _artifact("ipa", ipa)
        self._command("final-signature", ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(archive.archive_path / "Products" / "Applications" / f"{TARGET}.app")], timeout=120)

    def attended_upload(self, consumer: str, identity: ReleaseIdentity, ipa: IpaArtifact) -> str:
        if consumer != PINNED_UPLOAD_CONSUMER or os.environ.get(BWS_MARKER) != PINNED_UPLOAD_CONSUMER:
            raise WorkflowError("bws-consumer-boundary-required")
        _artifact("ipa", ipa)
        group = self._load_group()
        upload_request = {
            "data": {
                "type": "buildUploads",
                "attributes": {
                    "cfBundleShortVersionString": identity.marketing_version,
                    "cfBundleVersion": identity.build_number,
                    "platform": "IOS",
                },
                "relationships": {"app": {"data": {"type": "apps", "id": group["appId"]}}},
            },
        }
        created = self._asc("POST", "/buildUploads", upload_request)
        upload = self._resource(created, "buildUploads")
        upload_id = upload["id"]
        file_request = {
            "data": {
                "type": "buildUploadFiles",
                "attributes": {"assetType": "ASSET", "fileName": ipa.ipa_path.name, "fileSize": ipa.ipa_path.stat().st_size, "uti": "com.apple.ipa"},
                "relationships": {"buildUpload": {"data": {"type": "buildUploads", "id": upload_id}}},
            },
        }
        file_record = self._asc("POST", "/buildUploadFiles", file_request)
        upload_file = self._resource(file_record, "buildUploadFiles")
        operations = upload_file.get("attributes", {}).get("uploadOperations") if isinstance(upload_file.get("attributes"), dict) else None
        if not isinstance(operations, list) or not operations:
            raise WorkflowError("asc-upload-operations-invalid")
        try:
            contents = ipa.ipa_path.read_bytes()
        except OSError as exc:
            raise WorkflowError("signed-artifact-unreadable") from exc
        expected_offset = 0
        for operation in operations:
            if not isinstance(operation, dict) or set(operation) != {"url", "method", "requestHeaders", "offset", "length"}:
                raise WorkflowError("asc-upload-operations-invalid")
            url = operation["url"]
            method = operation["method"]
            headers = operation["requestHeaders"]
            offset = operation["offset"]
            length = operation["length"]
            parsed = urlparse(url) if isinstance(url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment or method != "PUT" or not isinstance(offset, int) or not isinstance(length, int) or offset != expected_offset or length <= 0 or offset + length > len(contents) or not isinstance(headers, list):
                raise WorkflowError("asc-upload-operations-invalid")
            typed_headers: dict[str, str] = {}
            for header in headers:
                if not isinstance(header, dict) or set(header) != {"name", "value"} or not isinstance(header["name"], str) or not isinstance(header["value"], str) or not header["name"] or "\n" in header["name"] or "\n" in header["value"]:
                    raise WorkflowError("asc-upload-operations-invalid")
                typed_headers[header["name"]] = header["value"]
            self._binary(method, url, contents[offset:offset + length], typed_headers)
            expected_offset += length
        if expected_offset != len(contents):
            raise WorkflowError("asc-upload-operations-incomplete")
        committed = self._asc("PATCH", f"/buildUploadFiles/{upload_file['id']}", {"data": {"type": "buildUploadFiles", "id": upload_file["id"], "attributes": {"uploaded": True}}})
        if self._resource(committed, "buildUploadFiles")["id"] != upload_file["id"]:
            raise WorkflowError("asc-response-invalid")
        return self._poll_new_build(identity)

    def _poll_new_build(self, identity: ReleaseIdentity) -> str:
        for attempt in range(12):
            try:
                build_id, build = self._exact_build(identity, None)
            except WorkflowError as exc:
                if str(exc) != "asc-exact-build-not-found":
                    raise
                build = None
                build_id = ""
            if build is not None and build["attributes"].get("processingState") == "VALID":
                return build_id
            if attempt < 11:
                self._status("asc-processing-poll-pending")
                self._sleep(30)
        raise WorkflowError("asc-processing-timeout")

    def _exact_build(self, identity: ReleaseIdentity, build_id: str | None) -> tuple[str, dict[str, Any]]:
        group = self._load_group()
        response = self._asc("GET", f"/apps/{group['appId']}/builds?" + urlencode({"fields[builds]": "version,processingState", "fields[preReleaseVersions]": "version", "include": "preReleaseVersion", "limit": "200"}))
        data = response.get("data")
        included = response.get("included")
        if not isinstance(data, list) or not isinstance(included, list):
            raise WorkflowError("asc-response-invalid")
        prerelease_ids = {
            item.get("id") for item in included
            if isinstance(item, dict) and item.get("type") == "preReleaseVersions" and isinstance(item.get("attributes"), dict) and item["attributes"].get("version") == identity.marketing_version
        }
        matches = [item for item in data if isinstance(item, dict) and item.get("type") == "builds" and (build_id is None or item.get("id") == build_id) and isinstance(item.get("id"), str) and isinstance(item.get("attributes"), dict) and str(item["attributes"].get("version")) == identity.build_number and isinstance(item.get("relationships"), dict) and isinstance(item["relationships"].get("preReleaseVersion"), dict) and isinstance(item["relationships"]["preReleaseVersion"].get("data"), dict) and item["relationships"]["preReleaseVersion"]["data"].get("id") in prerelease_ids]
        if len(matches) != 1:
            raise WorkflowError("asc-exact-build-not-found")
        self._app_id = group["appId"]
        self._group = group
        return matches[0]["id"], matches[0]

    def poll_exact_build(self, identity: ReleaseIdentity, build_id: str, ipa: IpaArtifact) -> None:
        _artifact("ipa", ipa)
        for _attempt in range(12):
            observed_id, build = self._exact_build(identity, build_id)
            if observed_id != build_id:
                raise WorkflowError("asc-exact-build-not-found")
            if build["attributes"].get("processingState") == "VALID":
                return
            self._status("asc-processing-poll-pending")
            self._sleep(30)
        raise WorkflowError("asc-processing-timeout")

    def resolve_compliance(self, identity: ReleaseIdentity, build_id: str) -> None:
        self._exact_build(identity, build_id)
        compliance = self._load_compliance()
        if self._app_id != compliance["appId"]:
            raise WorkflowError("compliance-evidence-identity-drift")
        declaration = self._asc("GET", f"/appEncryptionDeclarations/{compliance['declarationId']}")
        resource = self._resource(declaration, "appEncryptionDeclarations")
        if resource["id"] != compliance["declarationId"] or not isinstance(resource.get("attributes"), dict) or resource["attributes"].get("appEncryptionDeclarationState") != "APPROVED":
            raise WorkflowError("asc-compliance-unavailable")
        self._asc_empty("PATCH", f"/builds/{build_id}/relationships/appEncryptionDeclaration", {"data": {"type": "appEncryptionDeclarations", "id": compliance["declarationId"]}})

    def assign_internal_group(self, identity: ReleaseIdentity, build_id: str) -> None:
        self._exact_build(identity, build_id)
        if self._group is None:
            raise WorkflowError("internal-group-evidence-missing")
        groups = self._asc("GET", f"/apps/{self._group['appId']}/betaGroups?" + urlencode({"fields[betaGroups]": "isInternalGroup", "limit": "200"}))
        records = groups.get("data")
        matches = [item for item in records if isinstance(item, dict) and item.get("type") == "betaGroups" and item.get("id") == self._group["groupId"] and isinstance(item.get("attributes"), dict) and item["attributes"].get("isInternalGroup") is True] if isinstance(records, list) else []
        if len(matches) != 1:
            raise WorkflowError("internal-group-evidence-invalid")
        self._asc_empty("POST", f"/builds/{build_id}/relationships/betaGroups", {"data": [{"type": "betaGroups", "id": self._group["groupId"]}]})

    def verify_receipt(self, identity: ReleaseIdentity, build_id: str, ipa: IpaArtifact) -> None:
        self._exact_build(identity, build_id)
        _artifact("ipa", ipa)
        if self._group is None:
            raise WorkflowError("testflight-receipt-missing")
        receipt = self._asc("GET", f"/builds/{build_id}?" + urlencode({"include": "betaGroups,preReleaseVersion", "fields[betaGroups]": "isInternalGroup", "fields[preReleaseVersions]": "version"}))
        build = self._resource(receipt, "builds")
        included = receipt.get("included")
        if build["id"] != build_id or not isinstance(included, list) or not any(isinstance(item, dict) and item.get("type") == "betaGroups" and item.get("id") == self._group["groupId"] and isinstance(item.get("attributes"), dict) and item["attributes"].get("isInternalGroup") is True for item in included):
            raise WorkflowError("testflight-receipt-missing")

    def record_evidence(self, identity: ReleaseIdentity, build_id: str, archive: ArchiveArtifact, ipa: IpaArtifact) -> None:
        _artifact("archive", archive)
        _artifact("ipa", ipa)
        if self._group is None:
            raise WorkflowError("testflight-receipt-missing")
        evidence = {"formatVersion": "1.0.0", "candidateId": identity.candidate_id, "marketingVersion": identity.marketing_version, "buildNumber": identity.build_number, "gitRevision": identity.git_revision, "ascBuildId": build_id, "appId": self._group["appId"], "internalGroupId": self._group["groupId"], "archiveSha256": archive.archive_sha256, "ipaSha256": ipa.ipa_sha256, "recordedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        if SENSITIVE.search(json.dumps(evidence, sort_keys=True)):
            raise WorkflowError("testflight-evidence-sensitive-data")
        evidence_dir = self.root / "app" / "releases" / "evidence"
        evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = evidence_dir / "testflight-receipts.jsonl"
        try:
            descriptor = os.open(record, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush(); os.fsync(handle.fileno())
            record.chmod(0o600)
        except OSError as exc:
            raise WorkflowError("testflight-evidence-write-failed") from exc

    def notify(self, identity: ReleaseIdentity, build_id: str) -> None:
        # Notification is not a release success signal and has no implicit route.
        self._status("testflight-notification-deferred")


def run_workflow(
    provider: ReleaseProvider,
    *,
    state_path: Path,
    attended: bool,
    on_status: Callable[[str], None],
    candidate_manifest: Path | None = None,
    runtime: Path = ROOT / "app" / "vendor" / "apple-release" / "runtime",
) -> Mapping[str, Any]:
    """Execute an ordered, resumable TestFlight promotion."""
    if candidate_manifest is not None:
        return run_candidate_workflow(provider, manifest_path=candidate_manifest, attended=attended, on_status=on_status, runtime=runtime)
    if not attended:
        raise WorkflowError("attended-invocation-required")
    state = _read_state(state_path)
    if state is not None and state["stage"] == "complete":
        _emit(on_status, "testflight-already-complete")
        return state
    if state is None:
        _emit(on_status, "runtime-verification-started"); _call(provider.verify_runtime); _emit(on_status, "runtime-verified")
        _emit(on_status, "readiness-verification-started"); identity = _call(provider.verify_readiness); _emit(on_status, "readiness-verified")
        _emit(on_status, "full-gate-started"); _call(provider.run_full_gate); _emit(on_status, "full-gate-passed")
        _emit(on_status, "signing-readiness-started"); _call(provider.verify_signing_ready, identity); _emit(on_status, "signing-readiness-verified")
        _emit(on_status, "archive-started"); archive = _call(provider.archive, identity); archive_record = _artifact("archive", archive); _emit(on_status, "archive-created")
        _emit(on_status, "archive-inspection-started"); _call(provider.inspect_archive, identity, archive); _emit(on_status, "archive-inspected")
        _emit(on_status, "ipa-packaging-started"); ipa = _call(provider.package_ipa, identity, archive); ipa_record = _artifact("ipa", ipa); _emit(on_status, "ipa-packaged")
        _emit(on_status, "final-validation-started"); _call(provider.run_final_validation, identity, archive, ipa); _emit(on_status, "final-validation-passed")
        state = {"formatVersion": "2.0.0", "identity": identity.as_dict(), "stage": "prepared", "archive": archive_record, "ipa": ipa_record, "ascBuildId": None}
        _write_state(state_path, state); _emit(on_status, "pre-upload-boundary-reached")
    else:
        identity = ReleaseIdentity(candidate_id=state["identity"]["candidateId"], marketing_version=state["identity"]["marketingVersion"], build_number=state["identity"]["buildNumber"], git_revision=state["identity"]["gitRevision"])
        _emit(on_status, "resume-identity-verification-started"); observed = _call(provider.verify_readiness); _identity_matches(state, observed); _emit(on_status, "resume-identity-verified")
        archive = ArchiveArtifact(Path(state["archive"]["path"]), state["archive"]["sha256"]); ipa = IpaArtifact(Path(state["ipa"]["path"]), state["ipa"]["sha256"]); _artifact("archive", archive); _artifact("ipa", ipa)
    if state["stage"] == "prepared":
        _emit(on_status, "attended-upload-boundary-started"); build_id = _call(provider.attended_upload, PINNED_UPLOAD_CONSUMER, identity, ipa)
        if not isinstance(build_id, str) or not build_id: raise WorkflowError("asc-build-id-invalid")
        state = dict(state); state["stage"] = "uploaded"; state["ascBuildId"] = build_id; _write_state(state_path, state); _emit(on_status, "upload-bound")
    build_id = state["ascBuildId"]; assert isinstance(build_id, str)
    _emit(on_status, "exact-build-poll-started"); _call(provider.poll_exact_build, identity, build_id, ipa); _emit(on_status, "exact-build-poll-complete")
    _emit(on_status, "compliance-resolution-started"); _call(provider.resolve_compliance, identity, build_id); _emit(on_status, "compliance-resolved")
    _emit(on_status, "internal-group-assignment-started"); _call(provider.assign_internal_group, identity, build_id); _emit(on_status, "internal-group-assigned")
    _emit(on_status, "receipt-verification-started"); _call(provider.verify_receipt, identity, build_id, ipa); _emit(on_status, "receipt-verified")
    _call(provider.record_evidence, identity, build_id, archive, ipa); _call(provider.notify, identity, build_id)
    state = dict(state); state["stage"] = "complete"; _write_state(state_path, state); _emit(on_status, "testflight-complete")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the attended Quizzler TestFlight workflow.")
    parser.add_argument("--attended", action="store_true")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args(argv)
    try:
        if os.environ.get(BWS_MARKER) != PINNED_UPLOAD_CONSUMER:
            raise WorkflowError("bws-consumer-boundary-required")
        provider = QuizzlerTestFlightProvider(on_status=lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True))
        candidate_manifest = None
        if READINESS_PATH.is_file():
            bundle = _load_json(READINESS_PATH, "immutable-readiness-invalid")
            if bundle.get("formatVersion") == "1.0.0":
                raise WorkflowError("readiness-v1-rejected")
            reference = bundle.get("candidateManifest")
            if isinstance(reference, str) and not Path(reference).is_absolute() and ".." not in Path(reference).parts:
                candidate_manifest = ROOT / reference
        run_workflow(provider, state_path=args.state, attended=args.attended, candidate_manifest=candidate_manifest, on_status=lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True))
    except WorkflowError as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
