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

from cloudkit_schema_compatibility import SchemaCompatibilityError, compare_schemas
from release_adapter import AdapterError, central_runtime
from sync_release_tool import DEFAULT_DESTINATION


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "app" / "release-config.toml"
CURRENT_READINESS = Path("app/releases/state/current-readiness.json")
REQUIRED_EVIDENCE = (
    "inv8Certification",
    "productionSchema",
    "device",
)
EDITABLE_DECISION_KEYS = {"pass", "passed", "ready", "readiness", "result", "status", "success", "valid", "approved"}
ALLOWED_REQUIREMENTS = {"inv8-certification", "production-schema", "device-acceptance", "asc-build", "testflight-receipt"}
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


def _pack_certification(root: Path, record: dict[str, Any], manifest: dict[str, Any], *, now: datetime, maximum_age: int, check_freshness: bool = True) -> dict[str, str]:
    """Verify current installed-pack metadata and the independent INV-8 records."""

    required = {
        "packPath", "packSha256", "questionsHash", "certificationSha256",
        "independentReview", "humanSpotCheck",
    }
    if set(record) != required:
        raise ReadinessError("inv8-certification-partial")
    path = _resolve(root, record.get("packPath"))
    if not path.as_posix().startswith((root / "question-packs").resolve().as_posix() + "/"):
        raise ReadinessError("inv8-pack-path-invalid")
    pack = _load_json(path, "inv8-pack-invalid")
    if not isinstance(record.get("packSha256"), str) or record["packSha256"] != _sha256(path):
        raise ReadinessError("inv8-pack-hash-mismatch")

    # Import the canonical installer validator, rather than duplicating its
    # certification rules.  This remains local-only and reads no credentials.
    # Certification semantics are part of this verifier's checked-in source,
    # not an import supplied by a candidate or fixture repository.
    scripts_path = str(ROOT / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    try:
        import pack_cert  # type: ignore[import-not-found]
        fresh = pack_cert.certification_fresh(pack)
        current_questions_hash = pack_cert.questions_hash(pack)
    except (ImportError, TypeError, ValueError):
        raise ReadinessError("inv8-certification-invalid")
    certification = pack.get("certification")
    if not fresh or not isinstance(certification, dict):
        raise ReadinessError("inv8-certification-stale")
    if record["questionsHash"] != current_questions_hash or record["certificationSha256"] != _canonical_sha256(certification):
        raise ReadinessError("inv8-certification-source-mismatch")

    for name, required_fields in {
        "independentReview": {"reviewedAt", "reviewerModel", "evidenceSha256", "packSha256", "questionsHash"},
        "humanSpotCheck": {"reviewedAt", "reviewerSha256", "evidenceSha256", "packSha256", "questionsHash"},
    }.items():
        review = record.get(name)
        if not isinstance(review, dict) or set(review) != required_fields:
            raise ReadinessError("inv8-certification-partial")
        if check_freshness:
            _fresh(review.get("reviewedAt"), now=now, maximum_age=maximum_age)
        if not isinstance(review.get("evidenceSha256"), str) or not _hex64(review["evidenceSha256"]):
            raise ReadinessError("inv8-certification-evidence-invalid")
        if review.get("packSha256") != record["packSha256"] or review.get("questionsHash") != record["questionsHash"]:
            raise ReadinessError("inv8-certification-source-mismatch")
    certification_model = certification.get("critic_model")
    if (
        not isinstance(certification_model, str)
        or not certification_model.strip()
        or not isinstance(record["independentReview"]["reviewerModel"], str)
        or not record["independentReview"]["reviewerModel"].strip()
        or record["independentReview"]["reviewerModel"] == certification_model
    ):
        raise ReadinessError("inv8-certification-evidence-invalid")
    if not _hex64(record["humanSpotCheck"]["reviewerSha256"]):
        raise ReadinessError("inv8-certification-evidence-invalid")
    if record["independentReview"]["evidenceSha256"] == record["humanSpotCheck"]["evidenceSha256"]:
        raise ReadinessError("inv8-certification-evidence-invalid")
    # A valid pack certificate is itself the strict verifier's record.  Its
    # timestamp is checked independently so a copied old cert cannot qualify.
    if check_freshness:
        _fresh(certification.get("verified_at"), now=now, maximum_age=maximum_age)
    return {
        "packSha256": record["packSha256"],
        "questionsHash": record["questionsHash"],
        "certificationSha256": record["certificationSha256"],
    }


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
    if name not in {"inv8-certification", "production-schema", "device"}:
        raise ReadinessError("readiness-observation-name-invalid")
    document = _load_json(evidence_path, "readiness-observation-invalid")
    _reject_decision_flags(document)
    if name == "inv8-certification":
        config = _load_config()
        maximum_age = config.get("release_evidence_max_age_seconds")
        if not isinstance(maximum_age, int) or maximum_age <= 0:
            raise ReadinessError("release-config-invalid")
        details = {"packs": [_pack_certification(repository_root, item, manifest, now=datetime.now(timezone.utc), maximum_age=maximum_age, check_freshness=False) for item in document.get("packs", []) if isinstance(item, dict)]}
    elif name == "production-schema":
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
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify raw evidence and return a derived decision report."""

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
    missing_evidence = set(REQUIRED_EVIDENCE) - set(evidence_refs)
    if missing_evidence:
        if "inv8Certification" in missing_evidence:
            raise ReadinessError("inv8-certification-missing")
        raise ReadinessError("readiness-evidence-set-invalid")
    if set(evidence_refs) != set(REQUIRED_EVIDENCE):
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
        on_status("release-readiness-observation-validation-started")
    observations = _read_observations(manifest_path)
    if on_status:
        on_status("release-readiness-observation-validation-complete")

    if on_status:
        on_status("release-readiness-evidence-validation-started")
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
    if on_status:
        on_status("release-readiness-evidence-validation-complete")

    if on_status:
        on_status("release-readiness-inv8-validation-started")
    inv8 = documents["inv8Certification"]
    if set(inv8) != {"formatVersion", "candidateId", "marketingVersion", "buildNumber", "gitRevision", "sourceDigest", "capturedAt", "packs"} or inv8.get("formatVersion") != V2_FORMAT:
        raise ReadinessError("inv8-certification-invalid")
    _identity(inv8, manifest)
    if inv8.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
        raise ReadinessError("inv8-certification-source-mismatch")
    _fresh(inv8.get("capturedAt"), now=current, maximum_age=maximum_age)
    packs = inv8.get("packs")
    required_packs = config.get("release_inv8_required_packs", ["question-packs/cissp/cissp-core.json"])
    if not isinstance(required_packs, list) or not all(isinstance(item, str) and item for item in required_packs):
        raise ReadinessError("release-config-invalid")
    if not isinstance(packs, list) or {item.get("packPath") for item in packs if isinstance(item, dict)} != set(required_packs) or len(packs) != len(required_packs):
        raise ReadinessError("inv8-certification-incomplete")
    inv8_details = [_pack_certification(repository_root, item, manifest, now=current, maximum_age=maximum_age) for item in packs]
    if on_status:
        on_status("release-readiness-inv8-validation-complete")

    if on_status:
        on_status("release-readiness-schema-validation-started")
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
    if on_status:
        on_status("release-readiness-schema-validation-complete")

    if on_status:
        on_status("release-readiness-device-validation-started")
    device = documents["device"]
    _fresh(device.get("capturedAt"), now=current, maximum_age=maximum_age)
    device_attestation = _device_attestation(device, manifest, config)
    for observed in device["devices"]:
        _fresh(observed["observedAt"], now=current, maximum_age=maximum_age)
    if on_status:
        on_status("release-readiness-device-validation-complete")
    # The append-only observation ledger is the durable binding, not a mutable
    # pass flag embedded in the evidence document.
    names = {record.get("name") for record in observations}
    if not {"inv8-certification", "production-schema", "device"}.issubset(names):
        raise ReadinessError("readiness-observations-incomplete")
    expected_observations = {
        "inv8-certification": {
            "packs": inv8_details,
            "evidenceSha256": _sha256(paths["inv8Certification"]),
        },
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
        "deviceCount": len(device["devices"]),
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
