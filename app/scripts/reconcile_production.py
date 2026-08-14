#!/usr/bin/env python3
"""Reconcile privacy-safe Production observations with one frozen candidate.

The input is a raw, hash-bearing observation document.  A mutable ``pass`` or
``success`` flag is never trusted, and this verifier performs no CloudKit or
device operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_adapter import AdapterError, central_runtime  # noqa: E402
from release_readiness import (  # noqa: E402
    ReadinessError,
    _identity,
    _load_json,
    _parse_timestamp,
    _reject_decision_flags,
    _v2_manifest,
)
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "app" / "release-config.toml"
V2_FORMAT = "2.0.0"
REQUIRED_FIELDS = {
    "formatVersion",
    "candidateId",
    "marketingVersion",
    "buildNumber",
    "gitRevision",
    "sourceDigest",
    "capturedAt",
    "environment",
    "containerIdentifier",
    "fields",
    "canonicalStateSha256",
}
PRIVATE_KEYS = {
    "account",
    "accountid",
    "appleid",
    "certificate",
    "credential",
    "devicename",
    "email",
    "log",
    "password",
    "path",
    "record",
    "serial",
    "token",
    "udid",
}


class ProductionReconciliationError(ValueError):
    """Stable raw-evidence reconciliation rejection."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProductionReconciliationError("production-evidence-unreadable") from exc


def _load_config(root: Path) -> dict[str, Any]:
    config_path = root / "app/release-config.toml"
    if not config_path.is_file():
        config_path = CONFIG
    try:
        value = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProductionReconciliationError("release-config-invalid") from exc
    if not isinstance(value, dict):
        raise ProductionReconciliationError("release-config-invalid")
    return value


def _resolve(root: Path, value: object) -> Path:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProductionReconciliationError("production-path-invalid")
    path = (Path(value) if Path(value).is_absolute() else root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ProductionReconciliationError("production-path-outside-repository") from exc
    if path.is_symlink() or not path.is_file():
        raise ProductionReconciliationError("production-evidence-unreadable")
    return path


def _reject_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                normalized = key.lower().replace("_", "").replace("-", "")
                if normalized in PRIVATE_KEYS:
                    raise ProductionReconciliationError("privacy-sensitive-field-forbidden")
            _reject_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_private_fields(child)


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _load_candidate(manifest_path: Path, runtime: Path) -> dict[str, Any]:
    try:
        return _v2_manifest(central_runtime(runtime), manifest_path)
    except (AdapterError, ValueError) as exc:
        raise ProductionReconciliationError(str(exc)) from exc


def verify_production_evidence(
    manifest_path: Path,
    evidence_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Re-derive field-level Production evidence against the manifest identity."""

    if on_status:
        on_status("production-reconciliation-started")
    root = repository_root.resolve()
    manifest = _load_candidate(manifest_path, runtime)
    document = _load_json(evidence_path, "production-evidence-invalid")
    try:
        _reject_decision_flags(document)
    except ReadinessError as exc:
        raise ProductionReconciliationError(str(exc)) from exc
    _reject_private_fields(document)
    if set(document) != REQUIRED_FIELDS or document.get("formatVersion") != V2_FORMAT:
        raise ProductionReconciliationError("production-evidence-invalid")
    try:
        _identity(document, manifest)
    except ReadinessError as exc:
        raise ProductionReconciliationError("production-identity-mismatch") from exc
    config = _load_config(root)
    if (
        document.get("environment") != "Production"
        or document.get("containerIdentifier") != config.get("production_container")
        or not _hex64(document.get("sourceDigest"))
    ):
        raise ProductionReconciliationError("production-identity-mismatch")
    _parse_timestamp(document.get("capturedAt"), "production-evidence-time-invalid")
    fields = document.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ProductionReconciliationError("production-fields-invalid")
    # Boolean claims such as ``passed: true`` cannot establish a Production
    # round-trip; facts must be represented by raw values or hashes.
    def reject_boolean(value: Any) -> None:
        if isinstance(value, bool):
            raise ProductionReconciliationError("production-boolean-claim-forbidden")
        if isinstance(value, dict):
            for child in value.values():
                reject_boolean(child)
        elif isinstance(value, list):
            for child in value:
                reject_boolean(child)

    reject_boolean(fields)
    declared_hash = document.get("canonicalStateSha256")
    actual_hash = hashlib.sha256(_canonical(fields)).hexdigest()
    if declared_hash != actual_hash:
        raise ProductionReconciliationError("production-canonical-hash-mismatch")
    if on_status:
        on_status("production-fields-reconciled")
        on_status("production-reconciliation-complete")
    return {
        "formatVersion": V2_FORMAT,
        "candidateId": manifest["candidateId"],
        "decision": "reconciled",
        "evidenceSha256": _sha256(evidence_path),
        "canonicalStateSha256": actual_hash,
        "fieldCount": len(fields),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="current or a repository-relative v2 manifest")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args(argv)
    status = lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True)
    try:
        root = args.repository.resolve()
        if args.candidate == "current":
            readiness = _load_json(root / "app/releases/state/current-readiness.json", "current-readiness-unreadable")
            manifest_ref = readiness.get("candidateManifest")
        else:
            manifest_ref = args.candidate
        manifest_path = _resolve(root, manifest_ref)
        evidence_path = _resolve(root, args.evidence)
        status("production-candidate-resolved")
        report = verify_production_evidence(
            manifest_path, evidence_path, repository_root=root, runtime=args.runtime, on_status=status
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (ProductionReconciliationError, ReadinessError, AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
