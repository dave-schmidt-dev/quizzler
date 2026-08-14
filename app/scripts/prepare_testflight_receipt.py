#!/usr/bin/env python3
"""Capture existing TestFlight group and compliance receipts without ASC mutation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from provision_signing import AscHTTPError, SigningError, _asc_request, _config, _jwt_token


ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "quizzler-testflight-receipt-prep"
MARKER = "QUIZZLER_TESTFLIGHT_RECEIPT_BWS_CONSUMER"
EVIDENCE_DIRECTORY = ROOT / "app" / "releases" / "evidence"
GROUP_PATH = EVIDENCE_DIRECTORY / "testflight-internal-group.json"
COMPLIANCE_PATH = EVIDENCE_DIRECTORY / "testflight-compliance.json"


class ReceiptPreparationError(ValueError):
    """A stable, redacted receipt-preparation rejection."""


Request = Callable[[str, str, dict[str, Any] | None], dict[str, Any]]


def _status(event: str) -> None:
    print(f"STATUS {event}", file=sys.stderr, flush=True)


def _resource_list(response: Mapping[str, Any], resource_type: str, code: str) -> list[dict[str, Any]]:
    data = response.get("data")
    if not isinstance(data, list) or any(not isinstance(item, dict) or item.get("type") != resource_type for item in data):
        raise ReceiptPreparationError(code)
    return data


def _one_identifier(items: list[dict[str, Any]], code: str) -> str:
    identifiers = [item.get("id") for item in items if isinstance(item.get("id"), str) and item["id"]]
    if len(items) != 1 or len(identifiers) != 1:
        raise ReceiptPreparationError(code)
    return identifiers[0]


def _exact_app(request: Request, bundle_id: str) -> str:
    response = request("GET", "/apps?" + urlencode({"filter[bundleId]": bundle_id, "fields[apps]": "bundleId"}), None)
    apps = _resource_list(response, "apps", "asc-app-response-invalid")
    exact = [item for item in apps if isinstance(item.get("attributes"), dict) and item["attributes"].get("bundleId") == bundle_id]
    return _one_identifier(exact, "asc-app-cardinality")


def _internal_group(request: Request, app_id: str) -> str:
    response = request("GET", f"/apps/{app_id}/betaGroups?" + urlencode({"fields[betaGroups]": "isInternalGroup"}), None)
    groups = _resource_list(response, "betaGroups", "asc-group-response-invalid")
    internal = [item for item in groups if isinstance(item.get("attributes"), dict) and item["attributes"].get("isInternalGroup") is True]
    return _one_identifier(internal, "asc-internal-group-cardinality")


def _approved_declaration(request: Request, app_id: str) -> str | None:
    response = request("GET", "/appEncryptionDeclarations?" + urlencode({"filter[app]": app_id, "fields[appEncryptionDeclarations]": "appEncryptionDeclarationState"}), None)
    declarations = _resource_list(response, "appEncryptionDeclarations", "asc-compliance-response-invalid")
    approved = [item for item in declarations if isinstance(item.get("attributes"), dict) and item["attributes"].get("appEncryptionDeclarationState") == "APPROVED"]
    if not approved:
        return None
    return _one_identifier(approved, "asc-approved-compliance-cardinality")


def _atomic_write(documents: tuple[tuple[Path, dict[str, str]], ...]) -> None:
    EVIDENCE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: list[tuple[Path, Path]] = []
    try:
        for destination, document in documents:
            descriptor, raw_path = tempfile.mkstemp(prefix=f".{destination.name}.", dir=EVIDENCE_DIRECTORY)
            path = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.append((path, destination))
        for source, destination in temporary:
            os.replace(source, destination)
            destination.chmod(0o600)
    except OSError as exc:
        raise ReceiptPreparationError("receipt-evidence-write-failed") from exc
    finally:
        for source, _destination in temporary:
            source.unlink(missing_ok=True)


def prepare_receipts(request: Request, *, status: Callable[[str], None] = _status) -> dict[str, str]:
    """Resolve the exact Internal Testers group and optional approved declaration."""

    bundle_id = _config().get("bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id:
        raise ReceiptPreparationError("release-config-invalid")
    status("asc-app-discovery-started")
    app_id = _exact_app(request, bundle_id)
    status("asc-internal-group-discovery-started")
    group_id = _internal_group(request, app_id)
    status("asc-compliance-discovery-started")
    declaration_id = _approved_declaration(request, app_id)
    documents: list[tuple[Path, dict[str, str]]] = [
        (GROUP_PATH, {"formatVersion": "1.0.0", "appId": app_id, "bundleId": bundle_id, "groupId": group_id, "isInternalGroup": True}),
    ]
    result = {"appId": app_id, "groupId": group_id}
    if declaration_id is not None:
        documents.append((COMPLIANCE_PATH, {"formatVersion": "1.0.0", "appId": app_id, "bundleId": bundle_id, "declarationId": declaration_id}))
        result["declarationId"] = declaration_id
    _atomic_write(tuple(documents))
    status("testflight-receipts-captured")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attended", action="store_true")
    args = parser.parse_args(argv)
    if not args.attended:
        parser.error("--attended is required")
    if os.environ.get(MARKER) != CONSUMER:
        print("BLOCKED bws-consumer-boundary-required", file=sys.stderr)
        return 2
    try:
        token = _jwt_token()
        prepare_receipts(lambda method, path, body: _asc_request(token, method, path, body))
    except ReceiptPreparationError as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    except AscHTTPError as exc:
        print(f"BLOCKED asc-http-{exc.status}", file=sys.stderr)
        return 2
    except SigningError:
        print("BLOCKED asc-request-failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
