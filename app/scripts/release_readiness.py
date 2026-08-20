#!/usr/bin/env python3
"""Derive Quizzler release readiness only from immutable raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_adapter import AdapterError, central_runtime
from sync_release_tool import DEFAULT_DESTINATION


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "app" / "release-config.toml"
CURRENT_READINESS = Path("app/releases/state/current-readiness.json")
REQUIRED_EVIDENCE: tuple[str, ...] = ()
EDITABLE_DECISION_KEYS = {"pass", "passed", "ready", "readiness", "result", "status", "success", "valid", "approved"}
ALLOWED_REQUIREMENTS = {"asc-build", "testflight-receipt"}
V2_FORMAT = "2.0.0"


class ReadinessError(ValueError):
    """A stable raw-evidence readiness rejection."""


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(code) from exc
    if not isinstance(value, dict):
        raise ReadinessError(code)
    return value


def _load_config() -> dict[str, Any]:
    try:
        return tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReadinessError("release-config-invalid") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReadinessError("evidence-unreadable") from exc
    return digest.hexdigest()


def _schema_digest(document: dict[str, Any]) -> str:
    schema = document.get("schema")
    if not isinstance(schema, dict):
        raise ReadinessError("cloudkit-schema-evidence-invalid")
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _resolve(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReadinessError("evidence-path-invalid")
    declared = Path(value)
    path = (declared if declared.is_absolute() else root / declared).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ReadinessError("evidence-outside-repository") from exc
    if path.is_symlink() or not path.is_file():
        raise ReadinessError("evidence-unreadable")
    return path


def _reject_decision_flags(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                normalized = key.lower().replace("_", "").replace("-", "")
                flag_tokens = {"pass", "passed", "ready", "readiness", "result", "status", "success", "valid", "approved"}
                if normalized in EDITABLE_DECISION_KEYS or any(normalized.endswith(token) for token in flag_tokens):
                    raise ReadinessError("editable-pass-flag-forbidden")
            _reject_decision_flags(child)
    elif isinstance(value, list):
        for child in value:
            _reject_decision_flags(child)


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ReadinessError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ReadinessError(code) from exc
    return parsed.astimezone(timezone.utc)


def _fresh(value: object, *, now: datetime, maximum_age: int) -> None:
    observed = _parse_timestamp(value, "evidence-time-invalid")
    age = (now - observed).total_seconds()
    if age < 0 or age > maximum_age:
        raise ReadinessError("evidence-stale")


def _identity(document: dict[str, Any], manifest: dict[str, Any]) -> None:
    release = manifest["release"]
    if (
        document.get("candidateId") != manifest["candidateId"]
        or str(document.get("marketingVersion")) != release["marketingVersion"]
        or str(document.get("buildNumber")) != release["buildNumber"]
        or document.get("gitRevision") != release["gitRevision"]
    ):
        raise ReadinessError("evidence-identity-drift")


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _device_attestation(document: dict[str, Any], manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Validate one signed physical preflight device for this candidate.

    The preflight build is deliberately not the later App Store IPA. An App
    Store distribution IPA cannot be installed until after upload, so device
    proof binds the frozen source/version/build to its signed preflight bundle
    and extracted signing/entitlement evidence instead.
    """

    required = {
        "formatVersion", "candidateId", "marketingVersion", "buildNumber", "gitRevision", "sourceDigest",
        "capturedAt", "preflightBuild", "devices",
    }
    if set(document) != required or document.get("formatVersion") != V2_FORMAT:
        raise ReadinessError("device-evidence-invalid")
    _identity(document, manifest)
    if document.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
        raise ReadinessError("device-evidence-invalid")
    preflight = document.get("preflightBuild")
    required_preflight = {
        "signedBuildSha256", "codeSignatureSha256", "entitlementsSha256", "bundleIdentifier", "teamIdentifier",
        "cloudKitContainerIdentifiers", "cloudKitContainerEnvironment",
    }
    if not isinstance(preflight, dict) or set(preflight) != required_preflight:
        raise ReadinessError("device-evidence-invalid")
    if (
        not all(_hex64(preflight.get(key)) for key in ("signedBuildSha256", "codeSignatureSha256", "entitlementsSha256"))
        or preflight.get("bundleIdentifier") != config.get("bundle_id")
        or preflight.get("teamIdentifier") != config.get("team_identifier")
        or preflight.get("cloudKitContainerIdentifiers") != [config.get("production_container")]
        or preflight.get("cloudKitContainerEnvironment") != "Production"
    ):
        raise ReadinessError("device-preflight-attestation-invalid")

    devices = document.get("devices")
    required_device = {
        "deviceEvidenceId", "platform", "sourceDigest", "signedBuildSha256", "codeSignatureSha256",
        "entitlementsSha256", "cloudKitContainerIdentifier", "cloudKitContainerEnvironment", "semanticStateSha256",
        "observedAt",
    }
    expected_device_count = config.get("release_device_evidence_count", 1)
    if expected_device_count != 1:
        raise ReadinessError("device-count-config-invalid")
    if not isinstance(devices, list) or len(devices) != expected_device_count or any(not isinstance(device, dict) or set(device) != required_device for device in devices):
        raise ReadinessError("device-evidence-invalid")
    if any(not _hex64(device.get("deviceEvidenceId")) for device in devices) or any(
        device.get("platform") != "physical"
        or not isinstance(device.get("deviceEvidenceId"), str)
        or not device["deviceEvidenceId"]
        or device.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]
        or any(device.get(key) != preflight.get(key) for key in ("signedBuildSha256", "codeSignatureSha256", "entitlementsSha256"))
        or device.get("cloudKitContainerIdentifier") != config.get("production_container")
        or device.get("cloudKitContainerEnvironment") != "Production"
        or not _hex64(device.get("semanticStateSha256"))
        for device in devices
    ):
        raise ReadinessError("device-evidence-invalid")
    device = devices[0]
    return {
        "signedBuildSha256": str(preflight["signedBuildSha256"]),
        "codeSignatureSha256": str(preflight["codeSignatureSha256"]),
        "entitlementsSha256": str(preflight["entitlementsSha256"]),
        "cloudKitContainerIdentifier": str(config["production_container"]),
        "cloudKitContainerEnvironment": "Production",
        "deviceEvidenceId": str(device["deviceEvidenceId"]),
        "semanticStateSha256": str(device["semanticStateSha256"]),
    }


def _v2_manifest(central: Any, path: Path) -> dict[str, Any]:
    """Load only the candidate-local v2 manifest; v1 is never migrated here."""

    manifest = central.load_candidate_manifest(path)
    if manifest.get("formatVersion") != 2:
        raise ReadinessError("candidate-manifest-v1-rejected")
    if manifest.get("lane") != "standard":
        raise ReadinessError("candidate-lane-invalid")
    requirements = manifest.get("readinessRequirements")
    if requirements != ["asc-build", "testflight-receipt"]:
        raise ReadinessError("candidate-readiness-requirements-invalid")
    return manifest


def _attestation(manifest_path: Path, manifest: dict[str, Any], repository_root: Path) -> tuple[dict[str, Any], Path, str]:
    path = manifest_path.parent / "artifact-attestation.json"
    value = _load_json(path, "artifact-attestation-missing")
    if value.get("formatVersion") != 2 or value.get("candidateId") != manifest["candidateId"] or value.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
        raise ReadinessError("artifact-attestation-binding-mismatch")
    artifact_ref = value.get("artifactPath")
    if not isinstance(artifact_ref, str) or not artifact_ref or Path(artifact_ref).is_absolute() or ".." in Path(artifact_ref).parts:
        raise ReadinessError("artifact-attestation-path-invalid")
    artifact_path = (manifest_path.parent / artifact_ref).resolve()
    try:
        artifact_path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise ReadinessError("artifact-attestation-path-invalid") from exc
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise ReadinessError("artifact-attestation-artifact-missing")
    artifact_digest = _sha256(artifact_path)
    if value.get("artifactSha256") != artifact_digest or value.get("fileSize") != artifact_path.stat().st_size:
        raise ReadinessError("artifact-attestation-artifact-drift")
    return value, artifact_path, artifact_digest


def _read_observations(manifest_path: Path) -> list[dict[str, Any]]:
    """Verify candidate-local readiness hash chain without editable decisions."""

    path = manifest_path.parent / "readiness.jsonl"
    if not path.is_file():
        raise ReadinessError("readiness-observations-missing")
    previous = "0" * 64
    records: list[dict[str, Any]] = []
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReadinessError("readiness-observations-invalid") from exc
        body = dict(record) if isinstance(record, dict) else {}
        declared = body.pop("recordHash", None)
        if (
            record.get("formatVersion") != 2
            or record.get("sequence") != sequence
            or record.get("kind") != "readiness"
            or record.get("previousHash") != previous
            or not isinstance(declared, str)
            or hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != declared
        ):
            raise ReadinessError("readiness-observations-invalid")
        _reject_decision_flags(record)
        records.append(record)
        previous = declared
    return records


def evaluate_readiness(
    readiness_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    now: datetime | None = None,
    require: Iterable[str] = (),
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify the frozen candidate and, when requested, its artifact attestation."""

    try:
        required = frozenset(require)
    except TypeError as exc:
        raise ReadinessError("readiness-requirement-invalid") from exc
    bundle = _load_json(readiness_path, "readiness-input-unreadable")
    if on_status:
        on_status("release-readiness-input-loaded")
    if set(bundle) != {"formatVersion", "candidateManifest", "evidence"} or bundle.get("formatVersion") != V2_FORMAT:
        raise ReadinessError("readiness-input-invalid")
    evidence_refs = bundle.get("evidence")
    if not isinstance(evidence_refs, dict):
        raise ReadinessError("readiness-evidence-set-invalid")
    if set(evidence_refs) != set(REQUIRED_EVIDENCE):
        raise ReadinessError("readiness-evidence-set-invalid")
    _reject_decision_flags(bundle)
    if required - ALLOWED_REQUIREMENTS:
        raise ReadinessError("readiness-requirement-unsupported")
    if "testflight-receipt" in required:
        raise ReadinessError("testflight-receipt-evidence-not-implemented")
    config = _load_config()
    central = central_runtime(runtime)
    manifest_path = _resolve(repository_root, bundle["candidateManifest"])
    manifest = _v2_manifest(central, manifest_path)
    if on_status:
        on_status("release-readiness-candidate-resolved")
    if manifest.get("productIdentifier") != config.get("release_product_identifier"):
        raise ReadinessError("candidate-product-identity-drift")
    # Prebuild readiness is intentionally evaluable before archive creation.
    # Artifact/IPA attestation remains required by any post-archive readiness
    # request and is independently enforced by the workflow before upload.
    if required & {"asc-build", "testflight-receipt"}:
        _attestation(manifest_path, manifest, repository_root)
    if on_status:
        on_status("release-readiness-evidence-validation-started")
    if on_status:
        on_status("release-readiness-evidence-validation-complete")

    return {
        "formatVersion": V2_FORMAT,
        "candidateId": manifest["candidateId"],
        "decision": "ready",
        "verifiedEvidence": sorted(evidence_refs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("readiness", nargs="?", type=Path)
    parser.add_argument("--candidate", choices=("current",), help="verify the repository's frozen current candidate")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--require", default="")
    args = parser.parse_args(argv)
    if (args.readiness is None) == (args.candidate is None):
        parser.error("provide exactly one readiness path or --candidate current")
    readiness_path = args.repository.resolve() / CURRENT_READINESS if args.candidate == "current" else args.readiness
    required = frozenset(filter(None, args.require.split(",")))
    print("STATUS release-readiness-verification-started", file=sys.stderr, flush=True)
    try:
        report = evaluate_readiness(
            readiness_path,
            repository_root=args.repository,
            runtime=args.runtime,
            require=required,
            on_status=lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True),
        )
    except (ReadinessError, AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    print("STATUS release-readiness-verification-ok", file=sys.stderr, flush=True)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
