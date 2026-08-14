"""Project-agnostic immutable candidate and restartable release primitives.

An app project supplies its own archive, signing, device-health, App Store Connect,
and evidence adapters. This module owns only the durable boundary around them:
one frozen version/build, a content-addressed manifest, a hash-chained ledger, and
candidate-scoped resume behavior. It never reads credentials or accepts a caller
selected executable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SENSITIVE_NAME = re.compile(
    r"(?:secret|token|password|private|credential|api[_-]?key|\.p8)",
    re.IGNORECASE,
)
GENESIS = "0" * 64
SEMANTIC_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


class ReleaseStateError(ValueError):
    """Raised when a manifest or ledger is missing, stale, or tampered with."""


class WorkflowError(ValueError):
    """Raised for a fixed, credential-free workflow failure code."""


def canonical_bytes(value: Any) -> bytes:
    """Encode JSON deterministically for hashes and immutable comparisons."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_file(path: Path | str) -> str:
    """Hash a file without loading the whole artifact into memory."""

    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseStateError("release input is unreadable") from exc
    return digest.hexdigest()


def _identity_value(value: str | int, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise ReleaseStateError(f"{field} must be non-empty")
    return result


def candidate_id(marketing_version: str, build_number: str | int) -> str:
    """Return the safe, stable identity used for the candidate filename."""

    value = f"{_identity_value(marketing_version, 'marketing version')}-{_identity_value(build_number, 'build number')}"
    if not SAFE_ID.fullmatch(value):
        raise ReleaseStateError("candidate identity contains unsafe path characters")
    return value


@dataclass(frozen=True)
class CandidateIdentity:
    """Inputs frozen before an app archive or device candidate is built."""

    marketing_version: str
    build_number: str
    git_revision: str
    input_hashes: Mapping[str, str]

    def validate(self) -> "CandidateIdentity":
        _identity_value(self.marketing_version, "marketing version")
        _identity_value(self.build_number, "build number")
        _identity_value(self.git_revision, "git revision")
        for name, digest in self.input_hashes.items():
            if not isinstance(name, str) or not SAFE_ID.fullmatch(name):
                raise ReleaseStateError("frozen input names must be safe identifiers")
            if not isinstance(digest, str) or not HEX64.fullmatch(digest.lower()):
                raise ReleaseStateError(f"frozen input {name} is not a SHA-256")
        return self


@dataclass(frozen=True)
class CandidateIdentityV2:
    """Immutable format-v2 pre-build inputs."""

    marketing_version: str
    build_number: str | int
    source_digest: str
    adapter_digest: str
    identity_proof_sha256: str
    git_revision: str = "unknown"
    adapter_schema_version: str = "1.0.0"
    snapshot_policy_version: str = "1.0.0"
    readiness_requirements: tuple[str, ...] = ()
    lane: str = "standard"

    def validate(self) -> "CandidateIdentityV2":
        if not isinstance(self.marketing_version, str) or not SEMANTIC_VERSION.fullmatch(
            self.marketing_version
        ):
            raise ReleaseStateError("candidate-marketing-version-invalid")
        try:
            build = int(str(self.build_number))
        except (TypeError, ValueError) as exc:
            raise ReleaseStateError("candidate-build-number-invalid") from exc
        if build <= 0 or str(build) != str(self.build_number):
            raise ReleaseStateError("candidate-build-number-invalid")
        for digest, code in (
            (self.source_digest, "candidate-source-snapshot-required"),
            (self.adapter_digest, "candidate-adapter-digest-invalid"),
            (self.identity_proof_sha256, "candidate-identity-proof-invalid"),
        ):
            if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                raise ReleaseStateError(code)
        if not isinstance(self.git_revision, str) or not self.git_revision:
            raise ReleaseStateError("candidate-git-revision-invalid")
        if any(not isinstance(name, str) or not SAFE_ID.fullmatch(name) for name in self.readiness_requirements):
            raise ReleaseStateError("candidate-readiness-requirement-invalid")
        if self.lane not in {"standard", "physical-validation"}:
            raise ReleaseStateError("candidate-lane-invalid")
        return self


def _manifest_body(
    identity: CandidateIdentity,
    *,
    product_identifier: str,
    created_at: str | None,
) -> dict[str, Any]:
    identity.validate()
    product = _identity_value(product_identifier, "product identifier")
    return {
        "formatVersion": 1,
        "candidateId": candidate_id(identity.marketing_version, identity.build_number),
        "productIdentifier": product,
        "release": {
            "marketingVersion": str(identity.marketing_version),
            "buildNumber": str(identity.build_number),
            "gitRevision": str(identity.git_revision),
            "frozen": True,
        },
        "frozenInputs": dict(sorted(identity.input_hashes.items())),
        "createdAt": created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _load_manifest(path: Path | str) -> dict[str, Any]:
    candidate_path = Path(path)
    try:
        value = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseStateError("candidate manifest is unreadable") from exc
    if not isinstance(value, dict) or value.get("formatVersion") != 1:
        raise ReleaseStateError("candidate manifest format is unsupported")
    declared = value.get("manifestSha256")
    body = dict(value)
    body.pop("manifestSha256", None)
    if (
        not isinstance(declared, str)
        or not HEX64.fullmatch(declared)
        or hash_bytes(canonical_bytes(body)) != declared
    ):
        raise ReleaseStateError("candidate manifest hash does not verify")
    if value.get("candidateId") != candidate_path.stem:
        raise ReleaseStateError("candidate manifest filename does not match identity")
    release = value.get("release")
    if not isinstance(release, dict) or release.get("frozen") is not True:
        raise ReleaseStateError("candidate release identity is not frozen")
    return value


def freeze_candidate(
    directory: Path | str,
    identity: CandidateIdentity,
    *,
    product_identifier: str,
    created_at: str | None = None,
) -> Path:
    """Create one immutable candidate or verify the identical retry."""

    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / f"{candidate_id(identity.marketing_version, identity.build_number)}.json"
    existing = _load_manifest(path) if path.exists() else None
    if existing is not None and created_at is None:
        created_at = str(existing.get("createdAt"))
    body = _manifest_body(
        identity, product_identifier=product_identifier, created_at=created_at
    )
    body["manifestSha256"] = hash_bytes(canonical_bytes(body))
    encoded = canonical_bytes(body) + b"\n"
    if existing is not None:
        if existing != body:
            raise ReleaseStateError("candidate manifest is immutable")
        return path
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _load_manifest(path)
        if existing != body:
            raise ReleaseStateError("candidate manifest is immutable")
        return path
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def read_ledger(ledger: Path | str) -> list[dict[str, Any]]:
    """Verify every record in the shared append-only ledger."""

    path = Path(ledger)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseStateError("transition ledger is unreadable") from exc
    records: list[dict[str, Any]] = []
    previous_hash = GENESIS
    for sequence, line in enumerate(lines, 1):
        if not line.strip():
            raise ReleaseStateError("transition ledger contains a blank line")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseStateError("transition ledger contains invalid JSON") from exc
        if not isinstance(record, dict) or record.get("sequence") != sequence:
            raise ReleaseStateError("transition ledger sequence is not append-only")
        if record.get("previousHash") != previous_hash:
            raise ReleaseStateError("transition ledger hash chain is broken")
        declared = record.get("recordHash")
        body = dict(record)
        body.pop("recordHash", None)
        if not isinstance(declared, str) or hash_bytes(canonical_bytes(body)) != declared:
            raise ReleaseStateError("transition ledger record hash does not verify")
        candidate = record.get("candidateId")
        transition = record.get("transition")
        if not isinstance(candidate, str) or not SAFE_ID.fullmatch(candidate):
            raise ReleaseStateError("transition ledger candidate is unsafe")
        if not isinstance(transition, str) or not SAFE_ID.fullmatch(transition):
            raise ReleaseStateError("transition ledger transition is unsafe")
        details = record.get("details")
        if not isinstance(details, dict):
            raise ReleaseStateError("transition ledger details are invalid")
        records.append(record)
        previous_hash = declared
    return records


def _public_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    values = dict(details or {})
    if any(SENSITIVE_NAME.search(str(key)) for key in values):
        raise ReleaseStateError("transition details may not contain credential fields")
    if any(SENSITIVE_NAME.search(str(value)) for value in values.values()):
        raise ReleaseStateError("transition details may not contain credential-like values")
    return values


def append_transition(
    ledger: Path | str,
    candidate: str,
    transition: str,
    *,
    details: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Append one public, hash-chained transition and fsync it."""

    if not SAFE_ID.fullmatch(candidate) or not SAFE_ID.fullmatch(transition):
        raise ReleaseStateError("ledger identifiers are unsafe")
    path = Path(ledger)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    records = read_ledger(path) if path.exists() else []
    previous_hash = records[-1]["recordHash"] if records else GENESIS
    sequence = records[-1]["sequence"] + 1 if records else 1
    record: dict[str, Any] = {
        "formatVersion": 1,
        "sequence": sequence,
        "candidateId": candidate,
        "transition": transition,
        "recordedAt": recorded_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previousHash": previous_hash,
        "details": _public_details(details),
    }
    record["recordHash"] = hash_bytes(canonical_bytes(record))
    with path.open("ab") as handle:
        handle.write(canonical_bytes(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return record


def read_candidate_ledger(ledger: Path | str, candidate: str) -> list[dict[str, Any]]:
    """Verify the shared ledger, then select only this candidate's records."""

    if not SAFE_ID.fullmatch(candidate):
        raise ReleaseStateError("ledger candidate is unsafe")
    return [record for record in read_ledger(ledger) if record["candidateId"] == candidate]


def has_transition(ledger: Path | str, candidate: str, transition: str) -> bool:
    """Return whether a candidate has a verified transition."""

    if not SAFE_ID.fullmatch(transition):
        raise ReleaseStateError("ledger transition is unsafe")
    return any(
        record["transition"] == transition
        for record in read_candidate_ledger(ledger, candidate)
    )


def transition_once(
    ledger: Path | str,
    candidate: str,
    transition: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an identical existing transition or append it once."""

    public = _public_details(details)
    existing = read_candidate_ledger(ledger, candidate) if Path(ledger).exists() else []
    matches = [
        record
        for record in existing
        if record["transition"] == transition
    ]
    if matches:
        if public and matches[-1].get("details") != public:
            raise WorkflowError("transition-details-mismatch")
        return matches[-1]
    return append_transition(ledger, candidate, transition, details=public)


def _v2_manifest_body(
    identity: CandidateIdentityV2,
    *,
    product_identifier: str,
    created_at: str,
) -> dict[str, Any]:
    identity.validate()
    candidate = candidate_id(identity.marketing_version, identity.build_number)
    body: dict[str, Any] = {
        "formatVersion": 2,
        "candidateId": candidate,
        "productIdentifier": _identity_value(product_identifier, "product identifier"),
        "immutable": True,
        "release": {
            "marketingVersion": identity.marketing_version,
            "buildNumber": str(identity.build_number),
            "gitRevision": identity.git_revision,
            "frozen": True,
        },
        "sourceSnapshot": {
            "sha256": identity.source_digest,
            "policyVersion": identity.snapshot_policy_version,
        },
        "adapter": {
            "sha256": identity.adapter_digest,
            "schemaVersion": identity.adapter_schema_version,
        },
        "identityAllocation": {"proofSha256": identity.identity_proof_sha256},
        "readinessRequirements": sorted(set(identity.readiness_requirements)),
        "lane": identity.lane,
        "artifactAttestation": {
            "path": "artifact-attestation.json",
            "candidateId": candidate,
            "sourceDigest": identity.source_digest,
            "immutable": True,
        },
        "createdAt": created_at,
    }
    return body


def freeze_candidate_v2(
    directory: Path | str,
    identity: CandidateIdentityV2,
    *,
    product_identifier: str,
    created_at: str | None = None,
) -> Path:
    """Create a per-candidate format-v2 manifest or verify an exact resume."""

    identity.validate()
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    candidate = candidate_id(identity.marketing_version, identity.build_number)
    candidate_directory = root / candidate
    candidate_directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    path = candidate_directory / "manifest.json"
    existing = load_candidate_manifest(path) if path.exists() else None
    if existing is not None and created_at is None:
        created_at = str(existing["createdAt"])
    timestamp = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = _v2_manifest_body(
        identity, product_identifier=product_identifier, created_at=timestamp
    )
    body["manifestSha256"] = hash_bytes(canonical_bytes(body))
    encoded = canonical_bytes(body) + b"\n"
    if existing is not None:
        if existing != body or path.read_bytes() != encoded:
            raise ReleaseStateError("candidate-manifest-immutable")
        return path
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    except FileExistsError:
        return freeze_candidate_v2(
            root,
            identity,
            product_identifier=product_identifier,
            created_at=created_at,
        )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def load_candidate_manifest(path: Path | str) -> dict[str, Any]:
    """Verify a v1 or v2 manifest without modifying either format."""

    candidate_path = Path(path)
    try:
        raw = candidate_path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseStateError("candidate manifest is unreadable") from exc
    if not isinstance(value, dict) or value.get("formatVersion") not in {1, 2}:
        raise ReleaseStateError("candidate manifest format is unsupported")
    declared = value.get("manifestSha256")
    body = dict(value)
    body.pop("manifestSha256", None)
    if not isinstance(declared, str) or not HEX64.fullmatch(declared) or hash_bytes(canonical_bytes(body)) != declared:
        raise ReleaseStateError("candidate manifest hash does not verify")
    candidate = value.get("candidateId")
    expected = candidate_path.stem if value["formatVersion"] == 1 else candidate_path.parent.name
    if candidate != expected:
        raise ReleaseStateError("candidate manifest filename does not match identity")
    release = value.get("release")
    if not isinstance(release, dict) or release.get("frozen") is not True:
        raise ReleaseStateError("candidate release identity is not frozen")
    if value["formatVersion"] == 2:
        if raw != canonical_bytes(value) + b"\n":
            raise ReleaseStateError("candidate manifest bytes are not canonical")
        source = value.get("sourceSnapshot")
        adapter = value.get("adapter")
        placeholder = value.get("artifactAttestation")
        if not isinstance(source, dict) or not HEX64.fullmatch(str(source.get("sha256", ""))):
            raise ReleaseStateError("candidate-source-snapshot-required")
        if not isinstance(adapter, dict) or not HEX64.fullmatch(str(adapter.get("sha256", ""))):
            raise ReleaseStateError("candidate-adapter-digest-invalid")
        if not isinstance(placeholder, dict) or placeholder.get("candidateId") != candidate or placeholder.get("sourceDigest") != source["sha256"]:
            raise ReleaseStateError("candidate-artifact-binding-invalid")
    return value


def _load_manifest(path: Path | str) -> dict[str, Any]:
    value = load_candidate_manifest(path)
    if value.get("formatVersion") != 1:
        raise ReleaseStateError("candidate manifest format is unsupported")
    return value


def _candidate_directory(path: Path | str) -> Path:
    candidate_directory = Path(path)
    if candidate_directory.name == "manifest.json":
        candidate_directory = candidate_directory.parent
    manifest = load_candidate_manifest(candidate_directory / "manifest.json")
    if manifest["formatVersion"] != 2:
        raise ReleaseStateError("candidate manifest format is unsupported")
    return candidate_directory


def read_candidate_ledger_v2(path: Path | str) -> list[dict[str, Any]]:
    """Verify one candidate's independent v2 transition chain."""

    candidate_directory = _candidate_directory(path)
    candidate = candidate_directory.name
    ledger = candidate_directory / "transitions.jsonl"
    if not ledger.exists():
        return []
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseStateError("candidate-ledger-unreadable") from exc
    records: list[dict[str, Any]] = []
    previous = GENESIS
    last_failure_attempt = 0
    for sequence, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseStateError("candidate-ledger-invalid-json") from exc
        if not line or not isinstance(record, dict) or record.get("formatVersion") != 2 or record.get("sequence") != sequence:
            raise ReleaseStateError("candidate-ledger-sequence-invalid")
        if record.get("candidateId") != candidate or record.get("previousHash") != previous:
            raise ReleaseStateError("candidate-ledger-chain-invalid")
        declared = record.get("recordHash")
        body = dict(record)
        body.pop("recordHash", None)
        if not isinstance(declared, str) or hash_bytes(canonical_bytes(body)) != declared:
            raise ReleaseStateError("candidate-ledger-hash-invalid")
        if record.get("transition") == "failed":
            last_failure_attempt += 1
            if record.get("attempt") != last_failure_attempt:
                raise ReleaseStateError("candidate-failure-attempt-invalid")
        records.append(record)
        previous = declared
    return records


def _append_candidate_transition_unlocked(
    candidate_directory: Path,
    transition: str,
    *,
    details: Mapping[str, Any] | None,
    recorded_at: str | None,
) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(transition):
        raise ReleaseStateError("ledger identifiers are unsafe")
    records = read_candidate_ledger_v2(candidate_directory)
    candidate = candidate_directory.name
    record: dict[str, Any] = {
        "formatVersion": 2,
        "sequence": len(records) + 1,
        "candidateId": candidate,
        "transition": transition,
        "recordedAt": recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previousHash": records[-1]["recordHash"] if records else GENESIS,
        "details": _public_details(details),
    }
    if transition == "failed":
        record["attempt"] = 1 + sum(item["transition"] == "failed" for item in records)
        record["details"].setdefault("attempt", record["attempt"])
    record["recordHash"] = hash_bytes(canonical_bytes(record))
    ledger = candidate_directory / "transitions.jsonl"
    with ledger.open("ab") as handle:
        handle.write(canonical_bytes(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def append_candidate_transition(
    path: Path | str,
    transition: str,
    *,
    details: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
    use_lock: bool = True,
) -> dict[str, Any]:
    """Append a transition under the per-candidate lock."""

    candidate_directory = _candidate_directory(path)
    if not use_lock:
        return _append_candidate_transition_unlocked(
            candidate_directory, transition, details=details, recorded_at=recorded_at
        )
    from .product_state import CandidateLock

    with CandidateLock(candidate_directory / "candidate.lock"):
        return _append_candidate_transition_unlocked(
            candidate_directory, transition, details=details, recorded_at=recorded_at
        )


def execute_candidate_once(
    path: Path | str,
    transition: str,
    operation: Callable[[], Mapping[str, Any] | None],
) -> tuple[dict[str, Any], bool]:
    """Execute one same-candidate operation once across concurrent triggers."""

    candidate_directory = _candidate_directory(path)
    from .product_state import CandidateLock

    with CandidateLock(candidate_directory / "candidate.lock"):
        for record in read_candidate_ledger_v2(candidate_directory):
            if record["transition"] == transition:
                return record, False
        result = operation()
        if result is not None and not isinstance(result, Mapping):
            raise WorkflowError("candidate-operation-result-invalid")
        return (
            _append_candidate_transition_unlocked(
                candidate_directory, transition, details=result, recorded_at=None
            ),
            True,
        )


def append_failure_attempt(
    path: Path | str,
    *,
    step: str,
    code: str,
    diagnostic_fingerprint: str | None = None,
    recorded_at: str | None = None,
    use_lock: bool = True,
) -> dict[str, Any]:
    """Append, never deduplicate, a sanitized attempt-numbered failure."""

    if (
        not isinstance(step, str)
        or not SAFE_ID.fullmatch(step)
        or not isinstance(code, str)
        or not SAFE_ID.fullmatch(code)
        or (
            diagnostic_fingerprint is not None
            and (
                not isinstance(diagnostic_fingerprint, str)
                or not HEX64.fullmatch(diagnostic_fingerprint)
            )
        )
    ):
        raise ReleaseStateError("candidate-failure-details-invalid")
    details: dict[str, Any] = {"step": step, "code": code}
    if diagnostic_fingerprint is not None:
        details["diagnosticFingerprint"] = diagnostic_fingerprint
    return append_candidate_transition(
        path,
        "failed",
        details=details,
        recorded_at=recorded_at,
        use_lock=use_lock,
    )


def append_readiness_observation(
    path: Path | str,
    name: str,
    observation: Mapping[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Append renewable readiness without changing immutable candidate bytes."""

    candidate_directory = _candidate_directory(path)
    manifest = load_candidate_manifest(candidate_directory / "manifest.json")
    if not SAFE_ID.fullmatch(name):
        raise ReleaseStateError("readiness-name-invalid")
    values = _public_details(observation)
    if values.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
        raise ReleaseStateError("readiness-source-mismatch")
    from .product_state import CandidateLock

    with CandidateLock(candidate_directory / "candidate.lock"):
        ledger = candidate_directory / "readiness.jsonl"
        records = _read_auxiliary_ledger(ledger, "readiness")
        record: dict[str, Any] = {
            "formatVersion": 2,
            "sequence": len(records) + 1,
            "kind": "readiness",
            "name": name,
            "candidateId": candidate_directory.name,
            "recordedAt": recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "previousHash": records[-1]["recordHash"] if records else GENESIS,
            "observation": values,
        }
        record["recordHash"] = hash_bytes(canonical_bytes(record))
        with ledger.open("ab") as handle:
            handle.write(canonical_bytes(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record


def _read_auxiliary_ledger(path: Path, kind: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    previous = GENESIS
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseStateError(f"candidate-{kind}-unreadable") from exc
    for sequence, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseStateError(f"candidate-{kind}-invalid") from exc
        body = dict(record) if isinstance(record, dict) else {}
        declared = body.pop("recordHash", None)
        if record.get("sequence") != sequence or record.get("kind") != kind or record.get("previousHash") != previous or hash_bytes(canonical_bytes(body)) != declared:
            raise ReleaseStateError(f"candidate-{kind}-invalid")
        records.append(record)
        previous = declared
    return records


def bind_artifact_attestation(
    path: Path | str,
    attestation: Mapping[str, Any],
) -> Path:
    """Write once an artifact attestation bound to candidate and source."""

    candidate_directory = _candidate_directory(path)
    manifest = load_candidate_manifest(candidate_directory / "manifest.json")
    values = _public_details(attestation)
    if values.get("candidateId") != manifest["candidateId"] or values.get("sourceDigest") != manifest["sourceSnapshot"]["sha256"]:
        raise ReleaseStateError("artifact-attestation-binding-mismatch")
    if not HEX64.fullmatch(str(values.get("artifactSha256", ""))):
        raise ReleaseStateError("artifact-attestation-hash-invalid")
    body = {"formatVersion": 2, **values}
    body["attestationSha256"] = hash_bytes(canonical_bytes(body))
    encoded = canonical_bytes(body) + b"\n"
    destination = candidate_directory / "artifact-attestation.json"
    if destination.exists():
        if destination.read_bytes() != encoded:
            raise ReleaseStateError("artifact-attestation-immutable")
        return destination
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return destination


def diagnose_candidate(path: Path | str) -> dict[str, Any]:
    """Return a candidate-scoped diagnosis without changing any source state."""

    candidate_directory = Path(path)
    if candidate_directory.name == "manifest.json":
        candidate_directory = candidate_directory.parent
    try:
        manifest = load_candidate_manifest(candidate_directory / "manifest.json")
        read_candidate_ledger_v2(candidate_directory)
    except ReleaseStateError as exc:
        return {
            "formatVersion": 2,
            "candidateId": candidate_directory.name,
            "result": "corrupt",
            "code": str(exc),
            "recovery": "preserve-source-and-migrate-or-supersede",
        }
    return {
        "formatVersion": 2,
        "candidateId": manifest["candidateId"],
        "result": "verified",
    }


def run_gate(
    command: Sequence[str],
    *,
    cwd: Path,
    runner: Callable[..., Any] = subprocess.run,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """Run an app-owned gate while emitting progress on the status channel."""

    if not command or any(not isinstance(value, str) or not value for value in command):
        raise WorkflowError("release-gate-command-invalid")
    if on_status:
        on_status("release-gate-started")
    result = runner(command, cwd=cwd, check=False, capture_output=True, text=True)
    if getattr(result, "returncode", 1) != 0:
        if on_status:
            on_status("release-gate-failed")
        raise WorkflowError("release-gate-failed")
    if on_status:
        on_status("release-gate-ok")


def run_candidate(
    *,
    manifest_path: Path,
    ledger_path: Path,
    steps: Sequence[str],
    operations: Mapping[str, Callable[[], Mapping[str, Any] | None]],
    on_status: Callable[[str], None] | None = None,
    cleanup: Callable[[], None] | None = None,
    durable_after: str = "upload",
) -> list[dict[str, Any]]:
    """Execute or resume named steps for one frozen candidate.

    The ledger is verified in full but only records for ``manifest_path``'s
    candidate can skip work. Cleanup is allowed only until the durable step is
    recorded; after upload, a retry must preserve the artifact and target the
    exact already-uploaded build.
    """

    manifest = _load_manifest(manifest_path)
    candidate = str(manifest["candidateId"])
    records = read_candidate_ledger(ledger_path, candidate) if ledger_path.exists() else []
    completed = {str(record["transition"]): record for record in records}
    durable_transition = f"{durable_after}-ok"
    current_step = "unknown"
    try:
        for step in steps:
            if not isinstance(step, str) or not SAFE_ID.fullmatch(step):
                raise WorkflowError("release-step-invalid")
            current_step = step
            transition = f"{step}-ok"
            if transition in completed:
                continue
            if on_status:
                on_status(f"{step}-started")
            operation = operations.get(step)
            if operation is None:
                raise WorkflowError(f"operation-{step}-missing")
            result = operation()
            if result is not None and not isinstance(result, Mapping):
                raise WorkflowError(f"operation-{step}-result-invalid")
            record = transition_once(ledger_path, candidate, transition, details=result)
            records.append(record)
            completed[transition] = record
            if on_status:
                on_status(transition)
        return records
    except (WorkflowError, ReleaseStateError):
        if durable_transition not in completed and cleanup is not None:
            cleanup()
        transition_once(
            ledger_path,
            candidate,
            "failed",
            details={"step": current_step},
        )
        raise


__all__ = [
    "CandidateIdentity",
    "CandidateIdentityV2",
    "ReleaseStateError",
    "WorkflowError",
    "append_candidate_transition",
    "append_failure_attempt",
    "append_readiness_observation",
    "append_transition",
    "bind_artifact_attestation",
    "candidate_id",
    "diagnose_candidate",
    "execute_candidate_once",
    "freeze_candidate",
    "freeze_candidate_v2",
    "hash_file",
    "has_transition",
    "load_candidate_manifest",
    "read_candidate_ledger",
    "read_candidate_ledger_v2",
    "read_ledger",
    "run_candidate",
    "run_gate",
    "transition_once",
]
