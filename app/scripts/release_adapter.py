#!/usr/bin/env python3
"""Thin Quizzler adapter over the hash-bound central release state layer."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

# A verified runtime must remain byte-for-byte closed over its manifest. Avoid
# interpreter-generated files that would either weaken or trip that contract.
sys.dont_write_bytecode = True

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_release_tool import DEFAULT_DESTINATION, SyncError, verify_runtime


ROOT = Path(__file__).resolve().parents[2]
V2_FORMAT = "2.0.0"
STANDARD_LANE = "standard"
# These are intentionally frozen before archive creation.  ASC build and
# receipt evidence are post-upload observations, not candidate inputs.
PREBUILD_REQUIREMENTS: tuple[str, ...] = ()
POSTUPLOAD_REQUIREMENTS = ("asc-build", "testflight-receipt")
READINESS_REQUIREMENTS = PREBUILD_REQUIREMENTS + POSTUPLOAD_REQUIREMENTS
HEX64 = frozenset("0123456789abcdef")


class AdapterError(ValueError):
    """A stable Quizzler release-adapter rejection."""


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(code) from exc
    if not isinstance(value, dict):
        raise AdapterError(code)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AdapterError("release-input-unreadable") from exc
    return digest.hexdigest()


def _digest(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - HEX64:
        raise AdapterError(code)
    return value


def _resolve_input(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AdapterError("release-input-path-invalid")
    declared = Path(value)
    path = (declared if declared.is_absolute() else root / declared).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise AdapterError("release-input-outside-repository") from exc
    if path.is_symlink() or not path.is_file():
        raise AdapterError("release-input-unreadable")
    return path


def central_runtime(runtime: Path = DEFAULT_DESTINATION) -> ModuleType:
    """Verify then import the exact central state implementation."""

    try:
        verify_runtime(runtime)
    except SyncError as exc:
        raise AdapterError(str(exc)) from exc
    runtime_text = str(runtime.resolve())
    if runtime_text not in sys.path:
        sys.path.insert(0, runtime_text)
    module = importlib.import_module("release_tools.iterative_release")
    expected = runtime.resolve() / "release_tools" / "iterative_release.py"
    if Path(module.__file__).resolve() != expected:
        raise AdapterError("central-runtime-identity-drift")
    return module


def freeze_release(
    request_path: Path,
    *,
    state_directory: Path,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Freeze an exact Quizzler candidate through the central implementation."""

    request = _load_json(request_path, "release-request-unreadable")
    required = {
        "formatVersion", "marketingVersion", "buildNumber", "gitRevision",
        "sourceDigest", "adapterDigest", "identityProofSha256",
        "lane", "readinessRequirements",
    }
    optional = {"createdAt"}
    if not required.issubset(request) or set(request) - required - optional or request.get("formatVersion") != V2_FORMAT:
        raise AdapterError("release-request-invalid")
    if request.get("lane") != STANDARD_LANE:
        raise AdapterError("release-lane-invalid")
    requirements = request.get("readinessRequirements")
    if requirements != list(READINESS_REQUIREMENTS):
        raise AdapterError("release-readiness-requirements-invalid")
    source_digest = _digest(request.get("sourceDigest"), "release-source-identity-invalid")
    adapter_digest = _digest(request.get("adapterDigest"), "release-adapter-identity-invalid")
    identity_proof = _digest(request.get("identityProofSha256"), "release-identity-proof-invalid")
    actual_adapter_digest = _sha256(Path(__file__).resolve())
    if adapter_digest != actual_adapter_digest:
        raise AdapterError("release-adapter-identity-drift")
    if on_status:
        on_status("release-input-hashing-started")
    central = central_runtime(runtime)
    identity = central.CandidateIdentityV2(
        marketing_version=request["marketingVersion"],
        build_number=request["buildNumber"],
        git_revision=request["gitRevision"],
        source_digest=source_digest,
        adapter_digest=adapter_digest,
        identity_proof_sha256=identity_proof,
        readiness_requirements=tuple(requirements),
        lane=STANDARD_LANE,
    )
    if on_status:
        on_status("release-candidate-freeze-started")
    path = central.freeze_candidate_v2(
        state_directory / "candidates",
        identity,
        product_identifier="quizzler-ios",
        created_at=request.get("createdAt"),
    )
    if on_status:
        on_status("release-candidate-frozen")
    return path


def bind_artifact_attestation(
    manifest_path: Path,
    artifact_path: Path,
    *,
    runtime: Path = DEFAULT_DESTINATION,
    artifact_kind: str = "ipa",
    captured_at: str | None = None,
) -> Path:
    """Bind one final signed artifact to an existing v2 candidate.

    This is deliberately independent of readiness evidence: the first
    candidate can be prepared before Production schema/device observations
    exist, while upload still resumes the exact attested bytes.
    """

    central = central_runtime(runtime)
    manifest = central.load_candidate_manifest(manifest_path)
    if manifest.get("formatVersion") != 2:
        raise AdapterError("candidate-manifest-v1-rejected")
    candidate_dir = manifest_path.parent.resolve()
    artifact = artifact_path.resolve()
    try:
        artifact.relative_to(candidate_dir)
    except ValueError as exc:
        raise AdapterError("artifact-outside-candidate") from exc
    if artifact.is_symlink() or not artifact.is_file():
        raise AdapterError("artifact-unreadable")
    digest = _sha256(artifact)
    details = {
        "candidateId": manifest["candidateId"],
        "sourceDigest": manifest["sourceSnapshot"]["sha256"],
        "artifactKind": artifact_kind,
        "artifactPath": artifact.relative_to(candidate_dir).as_posix(),
        "artifactSha256": digest,
        "fileSize": artifact.stat().st_size,
        "capturedAt": captured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return central.bind_artifact_attestation(manifest_path, details)


def bind_asc_build(
    manifest_path: Path,
    ledger_path: Path,
    evidence_path: Path,
    *,
    runtime: Path = DEFAULT_DESTINATION,
) -> dict[str, Any]:
    """Append the exact later ASC build binding once; never rewrite candidate state."""

    central = central_runtime(runtime)
    manifest = central.load_candidate_manifest(manifest_path)
    if manifest.get("formatVersion") != 2:
        raise AdapterError("candidate-manifest-v1-rejected")
    evidence = _load_json(evidence_path, "asc-evidence-unreadable")
    required = {"formatVersion", "candidateId", "marketingVersion", "buildNumber", "gitRevision", "artifactSha256", "buildId", "capturedAt"}
    if set(evidence) != required or evidence.get("formatVersion") != "1.0.0":
        raise AdapterError("asc-evidence-invalid")
    release = manifest["release"]
    if (
        evidence["candidateId"] != manifest["candidateId"]
        or str(evidence["marketingVersion"]) != release["marketingVersion"]
        or str(evidence["buildNumber"]) != release["buildNumber"]
        or evidence["gitRevision"] != release["gitRevision"]
        or not isinstance(evidence["buildId"], str)
        or not evidence["buildId"]
    ):
        raise AdapterError("asc-evidence-identity-drift")
    candidate_ledger = manifest_path.parent / "transitions.jsonl"
    if ledger_path.resolve() != candidate_ledger.resolve():
        raise AdapterError("candidate-ledger-location-invalid")
    attestation = _load_json(manifest_path.parent / "artifact-attestation.json", "artifact-attestation-missing")
    if evidence["artifactSha256"] != attestation.get("artifactSha256"):
        raise AdapterError("asc-evidence-artifact-drift")
    return central.append_candidate_transition(
        manifest_path,
        "asc-build-bound",
        details={"ascBuildId": evidence["buildId"], "evidenceSha256": _sha256(evidence_path), "artifactSha256": evidence["artifactSha256"]},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("request", type=Path)
    freeze.add_argument("--state-directory", type=Path, required=True)
    freeze.add_argument("--repository", type=Path, default=ROOT)
    bind = subparsers.add_parser("bind-asc-build")
    bind.add_argument("manifest", type=Path)
    bind.add_argument("ledger", type=Path)
    bind.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    status = lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True)
    try:
        if args.command == "freeze":
            freeze_release(
                args.request,
                state_directory=args.state_directory,
                repository_root=args.repository,
                runtime=args.runtime,
                on_status=status,
            )
        else:
            status("asc-build-binding-started")
            bind_asc_build(args.manifest, args.ledger, args.evidence, runtime=args.runtime)
            status("asc-build-bound")
    except (AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
