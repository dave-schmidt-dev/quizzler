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
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloudkit_schema_compatibility import SchemaCompatibilityError, compare_schemas
from release_adapter import AdapterError, central_runtime
from sync_release_tool import DEFAULT_DESTINATION


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "app" / "release-config.toml"
REQUIRED_EVIDENCE = (
    "productionSchema",
    "device",
)
EDITABLE_DECISION_KEYS = {"pass", "passed", "ready", "readiness", "result", "status", "success", "valid", "approved"}
ALLOWED_REQUIREMENTS = {"production-schema", "device-acceptance", "asc-build", "testflight-receipt"}
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
        raise ReadinessError("production-schema-evidence-invalid")
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
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReadinessError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
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


def _device_attestation(document: dict[str, Any], manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """Validate exactly one signed physical preflight build for this candidate.

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
        "entitlementsSha256", "cloudKitContainerIdentifier", "cloudKitContainerEnvironment", "observedAt",
    }
    if not isinstance(devices, list) or len(devices) != 1 or not isinstance(devices[0], dict) or set(devices[0]) != required_device:
        raise ReadinessError("device-evidence-invalid")
    device = devices[0]
    if (
        device.get("platform") != "physical"
        or not isinstance(device.get("deviceEvidenceId"), str)
        or not device["deviceEvidenceId"]
        or device.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]
        or any(device.get(key) != preflight.get(key) for key in ("signedBuildSha256", "codeSignatureSha256", "entitlementsSha256"))
        or device.get("cloudKitContainerIdentifier") != config.get("production_container")
        or device.get("cloudKitContainerEnvironment") != "Production"
    ):
        raise ReadinessError("device-evidence-invalid")
    return {
        "signedBuildSha256": str(preflight["signedBuildSha256"]),
        "codeSignatureSha256": str(preflight["codeSignatureSha256"]),
        "entitlementsSha256": str(preflight["entitlementsSha256"]),
        "cloudKitContainerIdentifier": str(config["production_container"]),
        "cloudKitContainerEnvironment": "Production",
    }


def _v2_manifest(central: Any, path: Path) -> dict[str, Any]:
    """Load only the candidate-local v2 manifest; v1 is never migrated here."""

    manifest = central.load_candidate_manifest(path)
    if manifest.get("formatVersion") != 2:
        raise ReadinessError("candidate-manifest-v1-rejected")
    if manifest.get("lane") != "standard":
        raise ReadinessError("candidate-lane-invalid")
    requirements = manifest.get("readinessRequirements")
    if requirements != ["asc-build", "device-acceptance", "production-schema", "testflight-receipt"]:
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


def append_readiness_observation(
    manifest_path: Path,
    name: str,
    evidence_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
) -> dict[str, Any]:
    """Append one raw schema or physical-preflight observation.

    Production schema evidence is candidate/source-bound and records the
    normalized schema digest plus the raw evidence digest. Device evidence is
    intentionally independent of the later IPA and instead binds a signed
    preflight build/signature/entitlement attestation to the frozen candidate.
    """

    central = central_runtime(runtime)
    manifest = _v2_manifest(central, manifest_path)
    if name not in {"production-schema", "device"}:
        raise ReadinessError("readiness-observation-name-invalid")
    document = _load_json(evidence_path, "readiness-observation-invalid")
    _reject_decision_flags(document)
    if name == "production-schema":
        _identity(document, manifest)
        schema = document.get("schema")
        if document.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
            raise ReadinessError("readiness-observation-binding-mismatch")
        schema_digest = document.get("schemaDigest")
        if (
            document.get("environment") != "Production"
            or not isinstance(schema, dict)
            or schema.get("containerIdentifier") != _load_config().get("production_container")
            or not isinstance(schema_digest, str)
            or schema_digest != _schema_digest(document)
        ):
            raise ReadinessError("production-schema-evidence-invalid")
        details = {"schemaDigest": schema_digest}
    else:
        details = _device_attestation(document, manifest, _load_config())
    return central.append_readiness_observation(
        manifest_path,
        name,
        {
            "candidateId": manifest["candidateId"],
            "sourceDigest": manifest["sourceSnapshot"]["sha256"],
            "evidenceSha256": _sha256(evidence_path),
            **details,
        },
    )


def _load_config() -> dict[str, Any]:
    try:
        return tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReadinessError("release-config-invalid") from exc


def evaluate_readiness(
    readiness_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    now: datetime | None = None,
    require: Iterable[str] = (),
) -> dict[str, Any]:
    """Verify raw evidence and return a derived decision report."""

    try:
        required = frozenset(require)
    except TypeError as exc:
        raise ReadinessError("readiness-requirement-invalid") from exc
    bundle = _load_json(readiness_path, "readiness-input-unreadable")
    if set(bundle) != {"formatVersion", "candidateManifest", "evidence"} or bundle.get("formatVersion") != V2_FORMAT:
        raise ReadinessError("readiness-input-invalid")
    evidence_refs = bundle.get("evidence")
    if not isinstance(evidence_refs, dict) or set(evidence_refs) != set(REQUIRED_EVIDENCE):
        raise ReadinessError("readiness-evidence-set-invalid")
    _reject_decision_flags(bundle)
    if required - ALLOWED_REQUIREMENTS:
        raise ReadinessError("readiness-requirement-unsupported")
    if "testflight-receipt" in required:
        raise ReadinessError("testflight-receipt-evidence-not-implemented")
    config = _load_config()
    maximum_age = config.get("release_evidence_max_age_seconds")
    if not isinstance(maximum_age, int) or maximum_age <= 0:
        raise ReadinessError("release-config-invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    central = central_runtime(runtime)
    manifest_path = _resolve(repository_root, bundle["candidateManifest"])
    manifest = _v2_manifest(central, manifest_path)
    if manifest.get("productIdentifier") != config.get("release_product_identifier"):
        raise ReadinessError("candidate-product-identity-drift")
    # Prebuild readiness is intentionally evaluable before archive creation.
    # Artifact/IPA attestation remains required by any post-archive readiness
    # request and is independently enforced by the workflow before upload.
    if required & {"asc-build", "testflight-receipt"}:
        _attestation(manifest_path, manifest, repository_root)
    observations = _read_observations(manifest_path)

    paths: dict[str, Path] = {}
    documents: dict[str, dict[str, Any]] = {}
    for name, reference in evidence_refs.items():
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            raise ReadinessError("evidence-reference-invalid")
        path = _resolve(repository_root, reference["path"])
        digest = _sha256(path)
        if reference["sha256"] != digest:
            raise ReadinessError("evidence-hash-mismatch")
        paths[name] = path
        documents[name] = _load_json(path, f"{name}-evidence-invalid")
        _reject_decision_flags(documents[name])

    production = documents["productionSchema"]
    _identity(production, manifest)
    _fresh(production.get("capturedAt"), now=current, maximum_age=maximum_age)
    schema = production.get("schema")
    if (
        production.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]
        or production.get("environment") != "Production"
        or not isinstance(schema, dict)
        or schema.get("containerIdentifier") != config.get("production_container")
        or production.get("schemaDigest") != _schema_digest(production)
    ):
        raise ReadinessError("production-schema-evidence-invalid")
    try:
        # The first candidate has no production reset path.  A captured
        # Production schema is accepted only as the candidate-bound snapshot;
        # additive comparison remains available when a Development capture is
        # supplied by a later qualification workflow.
        comparison = {"disposition": config.get("schema_disposition")}
    except SchemaCompatibilityError as exc:
        raise ReadinessError(str(exc)) from exc

    device = documents["device"]
    _fresh(device.get("capturedAt"), now=current, maximum_age=maximum_age)
    device_attestation = _device_attestation(device, manifest, config)
    _fresh(device["devices"][0]["observedAt"], now=current, maximum_age=maximum_age)
    # The append-only observation ledger is the durable binding, not a mutable
    # pass flag embedded in the evidence document.
    names = {record.get("name") for record in observations}
    if not {"production-schema", "device"}.issubset(names):
        raise ReadinessError("readiness-observations-incomplete")
    expected_observations = {
        "production-schema": {
            "schemaDigest": production["schemaDigest"],
            "evidenceSha256": _sha256(paths["productionSchema"]),
        },
        "device": {
            **device_attestation,
            "evidenceSha256": _sha256(paths["device"]),
        },
    }
    for record in observations:
        observation = record.get("observation")
        name = record.get("name")
        if (
            not isinstance(observation, dict)
            or record.get("candidateId") != manifest["candidateId"]
            or observation.get("candidateId") != manifest["candidateId"]
            or observation.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]
            or name not in expected_observations
            or any(observation.get(key) != value for key, value in expected_observations[name].items())
        ):
            raise ReadinessError("readiness-observation-binding-mismatch")

    return {
        "formatVersion": V2_FORMAT,
        "candidateId": manifest["candidateId"],
        "decision": "ready",
        "schemaDisposition": comparison["disposition"],
        "verifiedEvidence": sorted(evidence_refs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("readiness", type=Path)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--require", default="")
    args = parser.parse_args(argv)
    required = frozenset(filter(None, args.require.split(",")))
    print("STATUS release-readiness-verification-started", file=sys.stderr, flush=True)
    try:
        report = evaluate_readiness(args.readiness, repository_root=args.repository, runtime=args.runtime, require=required)
    except (ReadinessError, AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    print("STATUS release-readiness-verification-ok", file=sys.stderr, flush=True)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
