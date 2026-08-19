#!/usr/bin/env python3
"""Verify or explicitly ingest one privacy-safe physical-device observation.

The default operation is local verification only.  Collection is intentionally
an attended hand-off: callers provide the raw document produced by their
device checklist, and this module validates/copies it without contacting
Apple, CloudKit, or a device.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_adapter import AdapterError, central_runtime  # noqa: E402
from release_readiness import (  # noqa: E402
    ReadinessError,
    _device_attestation,
    _load_json,
    _parse_timestamp,
    _reject_decision_flags,
    _v2_manifest,
)
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "app" / "release-config.toml"
CURRENT_READINESS = Path("app/releases/state/current-readiness.json")
V2_FORMAT = "2.0.0"


class DeviceAcceptanceError(ValueError):
    """Stable, privacy-safe device acceptance rejection."""


_PRIVATE_KEYS = {
    "account",
    "accountid",
    "appleid",
    "certificate",
    "credential",
    "deviceaddress",
    "devicename",
    "email",
    "log",
    "password",
    "path",
    "phone",
    "record",
    "serial",
    "token",
    "udid",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DeviceAcceptanceError("device-evidence-unreadable") from exc


def _load_config(root: Path) -> dict[str, Any]:
    config_path = root / "app/release-config.toml"
    # Test and hosted callers may provide a candidate-only checkout.  Use the
    # fixed product config only when that checkout has no local config.
    if not config_path.is_file():
        config_path = CONFIG
    try:
        value = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DeviceAcceptanceError("release-config-invalid") from exc
    if not isinstance(value, dict):
        raise DeviceAcceptanceError("release-config-invalid")
    return value


def _resolve(root: Path, value: object) -> Path:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value or "\\" in value:
        raise DeviceAcceptanceError("device-path-invalid")
    path = (Path(value) if Path(value).is_absolute() else root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DeviceAcceptanceError("device-path-outside-repository") from exc
    if path.is_symlink() or not path.is_file():
        raise DeviceAcceptanceError("device-evidence-unreadable")
    return path


def _reject_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                normalized = key.lower().replace("_", "").replace("-", "")
                if normalized in _PRIVATE_KEYS:
                    raise DeviceAcceptanceError("privacy-sensitive-field-forbidden")
            _reject_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_private_fields(child)


def _candidate_inputs(root: Path, candidate: str, evidence: Path | None) -> tuple[Path, Path]:
    if candidate == "current":
        readiness_path = root / CURRENT_READINESS
        readiness = _load_json(readiness_path, "current-readiness-unreadable")
        if set(readiness) != {"formatVersion", "candidateManifest", "evidence"} or readiness.get("formatVersion") != V2_FORMAT:
            raise DeviceAcceptanceError("current-readiness-invalid")
        manifest_ref = readiness.get("candidateManifest")
        evidence_refs = readiness.get("evidence")
        if not isinstance(evidence_refs, dict) or not isinstance(evidence_refs.get("device"), dict):
            raise DeviceAcceptanceError("current-readiness-invalid")
        default_ref = evidence_refs["device"].get("path")
    else:
        manifest_ref = candidate
        default_ref = None
    manifest_path = _resolve(root, manifest_ref)
    evidence_path = _resolve(root, evidence) if evidence is not None else _resolve(root, default_ref)
    return manifest_path, evidence_path


def _load_candidate(manifest_path: Path, *, runtime: Path) -> dict[str, Any]:
    try:
        central = central_runtime(runtime)
        return _v2_manifest(central, manifest_path)
    except (AdapterError, ValueError) as exc:
        raise DeviceAcceptanceError(str(exc)) from exc


def build_device_evidence(
    raw_path: Path,
    manifest_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
) -> dict[str, Any]:
    """Validate and return the exact v2 two-device evidence document.

    This function deliberately does not add a success field, device metadata,
    or an inferred identity.  Every value must be present in the attended raw
    document and must bind to the immutable candidate manifest.
    """

    root = repository_root.resolve()
    manifest = _load_candidate(manifest_path, runtime=runtime)
    document = _load_json(raw_path, "device-evidence-invalid")
    _reject_decision_flags(document)
    _reject_private_fields(document)
    config = _load_config(root)
    try:
        _device_attestation(document, manifest, config)
    except ReadinessError as exc:
        raise DeviceAcceptanceError(str(exc)) from exc
    _parse_timestamp(document["capturedAt"], "device-evidence-time-invalid")
    for device in document["devices"]:
        _parse_timestamp(device["observedAt"], "device-evidence-time-invalid")
    return json.loads(_canonical(document).decode("utf-8"))


def verify_device_evidence(
    manifest_path: Path,
    evidence_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Re-derive local two-device acceptance without writing any state."""

    if on_status:
        on_status("device-local-validation-started")
    document = build_device_evidence(
        evidence_path, manifest_path, repository_root=repository_root, runtime=runtime
    )
    manifest = _load_candidate(manifest_path, runtime=runtime)
    if on_status:
        on_status("device-attestation-rederived")
    report = {
        "formatVersion": V2_FORMAT,
        "candidateId": manifest["candidateId"],
        "decision": "verified",
        "evidenceSha256": _sha256(evidence_path),
        "sourceDigest": manifest["sourceSnapshot"]["sha256"],
        "deviceCount": len(document["devices"]),
    }
    if on_status:
        on_status("device-local-validation-complete")
    return report


def _write_new(path: Path, document: dict[str, Any], root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DeviceAcceptanceError("device-output-outside-repository") from exc
    if path.is_symlink():
        raise DeviceAcceptanceError("device-output-invalid")
    encoded = _canonical(document) + b"\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_bytes() != encoded:
                raise DeviceAcceptanceError("device-output-identity-drift")
        except OSError as exc:
            raise DeviceAcceptanceError("device-output-invalid") from exc
        return
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise DeviceAcceptanceError("device-output-invalid") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def collect_attended_device_evidence(
    manifest_path: Path,
    raw_path: Path,
    output_path: Path,
    *,
    repository_root: Path = ROOT,
    runtime: Path = DEFAULT_DESTINATION,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Ingest one caller-supplied attended document, with no device actions."""

    if on_status:
        on_status("device-attended-ingest-started")
    document = build_device_evidence(
        raw_path, manifest_path, repository_root=repository_root, runtime=runtime
    )
    _write_new(output_path.resolve(), document, repository_root.resolve())
    if on_status:
        on_status("device-attended-ingest-complete")
    return output_path.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="current or a repository-relative v2 manifest")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--evidence", type=Path, help="raw/device evidence path")
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--verify-only", action="store_true")
    operations.add_argument("--collect", action="store_true")
    parser.add_argument("--attended", action="store_true", help="required for explicit local ingestion")
    parser.add_argument("--output", type=Path, help="output path for --collect")
    args = parser.parse_args(argv)
    status = lambda event: print(f"STATUS {event}", file=sys.stderr, flush=True)
    try:
        root = args.repository.resolve()
        manifest_path, evidence_path = _candidate_inputs(root, args.candidate, args.evidence)
        status("device-candidate-resolved")
        if args.verify_only:
            report = verify_device_evidence(
                manifest_path, evidence_path, repository_root=root, runtime=args.runtime, on_status=status
            )
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            return 0
        if not args.attended:
            raise DeviceAcceptanceError("attended-collection-required")
        output = args.output or evidence_path
        if args.evidence is None:
            raise DeviceAcceptanceError("attended-raw-evidence-required")
        collect_attended_device_evidence(
            manifest_path,
            evidence_path,
            output,
            repository_root=root,
            runtime=args.runtime,
            on_status=status,
        )
        print(json.dumps({"evidence": str(output.resolve()), "formatVersion": V2_FORMAT}, sort_keys=True, separators=(",", ":")))
        return 0
    except (DeviceAcceptanceError, ReadinessError, AdapterError, ValueError) as exc:
        print(f"BLOCKED {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
