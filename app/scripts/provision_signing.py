#!/usr/bin/env python3
"""Attended, fail-closed bootstrap for Quizzler distribution signing.

The default mode is a secret-free plan.  The live path is intentionally
reachable only through the pinned BWS consumer, requires an explicit approval
flag, and keeps the private key in the login Keychain for its entire lifetime.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = "quizzler-asc-provision"
BWS_MARKER = CONSUMER
SCRIPT_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
API_BASE = "https://api.appstoreconnect.apple.com/v1"
REQUEST_TIMEOUT = 30
PROFILE_NAME = "Quizzler iOS App Store (API-created)"
PROFILES_DIR = Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles"
SAFE_PROFILE_NAME = "quizzler-ios-app-store.provisionprofile"


def _status_event(message: str) -> None:
    """Emit a redacted progress event without credentials or Apple identifiers."""
    print(f"==> {message}", flush=True)


def _config() -> dict[str, Any]:
    with (ROOT / "release-config.toml").open("rb") as handle:
        return tomllib.load(handle)


def configured_signing() -> tuple[str | None, str | None]:
    """Read the fixed consumer and script digest from reviewed config."""
    values = _config()
    return values.get("signing_consumer"), values.get("signing_script_sha256")


def configuration_error() -> str | None:
    consumer, digest = configured_signing()
    if consumer != CONSUMER:
        return "configured signing consumer does not match the fixed consumer"
    if digest != SCRIPT_SHA256:
        return "configured signing script SHA-256 does not match this script; update only after review"
    bundle_id = _config().get("bundle_id")
    if not isinstance(bundle_id, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", bundle_id):
        return "configured bundle ID is missing or invalid"
    app_store_name = _config().get("app_store_name")
    if not isinstance(app_store_name, str) or not app_store_name.strip():
        return "configured App Store app name is missing or invalid"
    return None


def bootstrap_plan() -> dict[str, object]:
    """Return the inert plan without reading credentials or contacting ASC."""
    configured_consumer, configured_digest = configured_signing()
    return {
        "mode": "inert",
        "consumer": CONSUMER,
        "script_sha256": SCRIPT_SHA256,
        "configured_consumer": configured_consumer,
        "configured_script_sha256": configured_digest,
        "configuration_error": configuration_error(),
        "broker": ["bws-secret-exec", CONSUMER, "--"],
        "operations": [
            "resolve the configured bundle ID and select one local Apple Distribution certificate",
            "reuse or POST /v1/profiles without deleting existing profiles",
            "install the public profile and write public evidence",
        ],
        "network": "disabled",
        "secret_output": "disabled",
    }


class SigningError(RuntimeError):
    """A safe, user-facing failure that contains no credential material."""


class AscHTTPError(SigningError):
    def __init__(self, status: int, classification: str) -> None:
        super().__init__(f"ASC HTTP {status}: {classification}")
        self.status = status
        self.classification = classification


class BundleLookupSummary(NamedTuple):
    """Redacted aggregate facts from a bundle-ID response."""

    resource_count: int = 0
    exact_identifier_count: int = 0
    eligible_platform_counts: dict[str, int] | None = None


class BundleLookupResult(NamedTuple):
    """The private resource ID plus its safe aggregate lookup summary."""

    resource_id: str
    summary: BundleLookupSummary


class ExistingProfileResult(NamedTuple):
    """A validated existing App Store profile and its decoded public bytes."""

    profile_id: str
    profile_bytes: bytes


class DistributionCertificateResult(NamedTuple):
    """An ASC distribution certificate with a matching local private key."""

    certificate_id: str
    serial_number: str


class AppBindingSummary(NamedTuple):
    """Redacted aggregate facts from an App Store app lookup."""

    app_count: int = 0
    configured_bundle_match: bool = False


class AppBindingError(SigningError):
    """A safe app binding lookup failure with aggregate public diagnostics."""

    def __init__(self, reason: str, *, summary: AppBindingSummary | None = None) -> None:
        self.reason = reason
        self.summary = summary or AppBindingSummary()
        super().__init__(reason)


class BundleLookupError(SigningError):
    """A safe bundle lookup failure with aggregate public diagnostics."""

    def __init__(
        self,
        reason: str,
        *,
        summary: BundleLookupSummary | None = None,
    ) -> None:
        self.reason = reason
        self.summary = summary or BundleLookupSummary()
        super().__init__(reason)


def _classify_status(status: int) -> str:
    if status in (401, 403):
        return "authentication or authorization rejected; verify the ASC key role and issuer"
    if status == 404:
        return "resource not found; verify the bundle ID or Apple account setup"
    if status == 409:
        return "resource conflict; inspect the existing certificate/profile in ASC"
    if status == 422:
        return "request rejected by ASC validation; verify certificate/profile attributes"
    if status == 429:
        return "rate limited; wait for a human-directed rerun"
    if 400 <= status < 500:
        return "request rejected; verify the configured Apple identifiers and request shape"
    return "ASC request failed"


def _jwt_token() -> str:
    """Create a short-lived ES256 JWT entirely in memory."""
    key_pem = os.environ.get("APP_STORE_CONNECT_API_KEY", "")
    key_id = os.environ.get("APP_STORE_CONNECT_KEY_ID", "")
    issuer_id = os.environ.get("APP_STORE_CONNECT_ISSUER_ID", "")
    if not key_pem or not key_id or not issuer_id:
        raise SigningError("required App Store Connect credentials are unavailable")
    if "\\n" in key_pem and "\n" not in key_pem:
        key_pem = key_pem.replace("\\n", "\n")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        private_key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise ValueError("API key is not an EC private key")
        now = int(time.time())
        header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
        payload = {"iss": issuer_id, "iat": now, "exp": now + 1190, "aud": "appstoreconnect-v1"}

        def encoded(value: object) -> str:
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()

        signing_input = f"{encoded(header)}.{encoded(payload)}".encode()
        der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return signing_input.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    except SigningError:
        raise
    except Exception as exc:
        raise SigningError("App Store Connect API key could not be used for ES256 authentication") from exc


def _asc_request(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read()
    except HTTPError as exc:
        raise AscHTTPError(exc.code, _classify_status(exc.code)) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise SigningError("App Store Connect request failed or timed out; no retry was attempted") from exc
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SigningError("App Store Connect returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SigningError("App Store Connect returned an unexpected response shape")
    return value


def local_certificate_serials(label: str) -> set[str]:
    """Return serials for local certificates whose private keys are available."""
    try:
        listing = subprocess.run(
            ["security", "find-certificate", "-a", "-c", label, "-p"],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SigningError("local certificate lookup failed or timed out") from exc
    if listing.returncode != 0:
        return set()
    serials: set[str] = set()
    for pem in listing.stdout.split("-----BEGIN CERTIFICATE-----"):
        if "-----END CERTIFICATE-----" not in pem:
            continue
        pem_block = "-----BEGIN CERTIFICATE-----" + pem
        try:
            result = subprocess.run(
                ["openssl", "x509", "-noout", "-serial"],
                input=pem_block,
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SigningError("local certificate serial lookup failed or timed out") from exc
        if result.returncode == 0 and result.stdout.startswith("serial="):
            serials.add(result.stdout.strip().removeprefix("serial=").upper())
    return serials


def _select_local_distribution_certificate(token: str) -> DistributionCertificateResult:
    """Select the latest uniquely expiring ASC certificate backed by a local key."""
    _status_event("App Store Connect distribution certificate lookup started (up to 30 seconds)")
    response = _asc_request(
        token,
        "GET",
        "/certificates?"
        + urlencode(
            {
                "filter[certificateType]": "DISTRIBUTION",
                "fields[certificates]": "serialNumber,expirationDate",
                "limit": "50",
            }
        ),
    )
    data = _require_data(response, "data")
    if not isinstance(data, list):
        raise SigningError("App Store Connect certificate lookup returned an invalid resource list")
    local_serials = local_certificate_serials("Apple Distribution")
    matches: list[tuple[datetime, DistributionCertificateResult]] = []
    for certificate in data:
        if not isinstance(certificate, dict) or certificate.get("type") != "certificates":
            raise SigningError("App Store Connect certificate lookup returned a nonconforming resource")
        certificate_id = certificate.get("id")
        attributes = certificate.get("attributes")
        serial = attributes.get("serialNumber") if isinstance(attributes, dict) else None
        if not isinstance(certificate_id, str) or not certificate_id or not isinstance(serial, str) or not serial:
            raise SigningError("App Store Connect certificate lookup returned a malformed resource")
        normalized_serial = serial.upper()
        if normalized_serial in local_serials:
            expiration = attributes.get("expirationDate")
            if not isinstance(expiration, str) or not expiration:
                raise SigningError("matching App Store Connect distribution certificate omitted expiration date")
            try:
                parsed_expiration = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SigningError("matching App Store Connect distribution certificate had invalid expiration date") from exc
            if parsed_expiration.tzinfo is None:
                parsed_expiration = parsed_expiration.replace(tzinfo=timezone.utc)
            matches.append(
                (
                    parsed_expiration.astimezone(timezone.utc),
                    DistributionCertificateResult(certificate_id, normalized_serial),
                )
            )
    if len(matches) == 0:
        raise SigningError("no App Store Connect distribution certificate has a matching local private key")
    latest_expiration = max(expiration for expiration, _certificate in matches)
    latest = [certificate for expiration, certificate in matches if expiration == latest_expiration]
    if len(latest) > 1:
        raise SigningError("multiple App Store Connect distribution certificates share the latest expiration date")
    return latest[0]


def _require_data(response: dict[str, Any], *keys: str) -> Any:
    value: Any = response
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise SigningError("App Store Connect response omitted a required public field")
        value = value[key]
    return value


def _resolve_bundle_id_resource(token: str, bundle_id: str) -> BundleLookupResult:
    """Resolve exactly one eligible bundle resource with the configured identifier."""
    bundle_response = _asc_request(
        token,
        "GET",
        "/bundleIds?" + urlencode({"filter[identifier]": bundle_id}),
    )
    bundle_data = _require_data(bundle_response, "data")
    if not isinstance(bundle_data, list):
        raise BundleLookupError("invalid-resource-list")
    exact_bundles: list[dict[str, Any]] = []
    eligible_platform_counts: Counter[str] = Counter()
    invalid_count = 0
    for bundle in bundle_data:
        if not isinstance(bundle, dict) or bundle.get("type") != "bundleIds":
            invalid_count += 1
            continue
        attributes = bundle.get("attributes")
        identifier = attributes.get("identifier") if isinstance(attributes, dict) else None
        platform = attributes.get("platform") if isinstance(attributes, dict) else None
        resource_id = bundle.get("id")
        if not isinstance(identifier, str) or not identifier or not isinstance(resource_id, str) or not resource_id:
            invalid_count += 1
            continue
        if identifier != bundle_id:
            continue
        if platform not in {"IOS", "UNIVERSAL"}:
            invalid_count += 1
            continue
        exact_bundles.append(bundle)
        eligible_platform_counts[platform] += 1
    summary = BundleLookupSummary(
        resource_count=len(bundle_data),
        exact_identifier_count=sum(
            1
            for bundle in bundle_data
            if isinstance(bundle, dict)
            and isinstance(bundle.get("attributes"), dict)
            and bundle["attributes"].get("identifier") == bundle_id
        ),
        eligible_platform_counts=dict(eligible_platform_counts),
    )
    if invalid_count:
        raise BundleLookupError(
            "nonconforming-resource",
            summary=summary,
        )
    if summary.exact_identifier_count != 1:
        raise BundleLookupError("exact-identifier-cardinality", summary=summary)
    if len(exact_bundles) != 1:
        raise BundleLookupError("ineligible-platform", summary=summary)
    return BundleLookupResult(exact_bundles[0]["id"], summary)


def _resolve_app_binding(token: str, app_name: str, bundle_id: str) -> AppBindingSummary:
    """Resolve one typed ASC app and compare its bundle ID without exposing it."""
    response = _asc_request(
        token,
        "GET",
        "/apps?" + urlencode({"filter[name]": app_name}),
    )
    data = _require_data(response, "data")
    if not isinstance(data, list):
        raise AppBindingError("invalid-resource-list")
    summary = AppBindingSummary(app_count=len(data))
    if len(data) != 1:
        raise AppBindingError("app-cardinality", summary=summary)
    app = data[0]
    if not isinstance(app, dict) or app.get("type") != "apps":
        raise AppBindingError("nonconforming-resource", summary=summary)
    attributes = app.get("attributes")
    observed_bundle_id = attributes.get("bundleId") if isinstance(attributes, dict) else None
    if not isinstance(observed_bundle_id, str) or not observed_bundle_id:
        raise AppBindingError("missing-bundle-id", summary=summary)
    summary = AppBindingSummary(
        app_count=1,
        configured_bundle_match=observed_bundle_id == bundle_id,
    )
    if not summary.configured_bundle_match:
        raise AppBindingError("bundle-mismatch", summary=summary)
    return summary


def _find_existing_profile(
    token: str,
    bundle_resource_id: str,
    profile_name: str = PROFILE_NAME,
) -> ExistingProfileResult | None:
    """Find one active, named App Store profile or return None for create fallback."""
    _status_event("App Store Connect existing profile lookup started (up to 30 seconds)")
    response = _asc_request(
        token,
        "GET",
        f"/bundleIds/{bundle_resource_id}/profiles?"
        + urlencode({"fields[profiles]": "name,profileType,profileState,profileContent"}),
    )
    data = _require_data(response, "data")
    if not isinstance(data, list):
        raise SigningError("App Store Connect profile lookup returned an invalid resource list")
    matches: list[tuple[str, dict[str, Any]]] = []
    for profile in data:
        if not isinstance(profile, dict) or profile.get("type") != "profiles":
            raise SigningError("App Store Connect profile lookup returned a nonconforming resource")
        profile_id = profile.get("id")
        attributes = profile.get("attributes")
        if not isinstance(profile_id, str) or not profile_id or not isinstance(attributes, dict):
            raise SigningError("App Store Connect profile lookup returned a malformed resource")
        if (
            attributes.get("name") == profile_name
            and attributes.get("profileType") == "IOS_APP_STORE"
            and attributes.get("profileState") == "ACTIVE"
        ):
            matches.append((profile_id, attributes))
    if len(matches) > 1:
        raise SigningError("multiple active matching App Store Connect profiles found")
    if not matches:
        return None
    profile_id, attributes = matches[0]
    profile_content = attributes.get("profileContent")
    if not isinstance(profile_content, str) or not profile_content:
        raise SigningError("matching App Store Connect profile omitted profile content")
    try:
        profile_bytes = base64.b64decode(profile_content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SigningError("matching App Store Connect profile content was invalid") from exc
    if not profile_bytes:
        raise SigningError("matching App Store Connect profile content was empty")
    return ExistingProfileResult(profile_id, profile_bytes)


def _lookup_only() -> int:
    """Perform one authenticated, read-only bundle lookup with redacted output."""
    result: dict[str, object] = {
        "status": "blocked",
        "resource_count": 0,
        "exact_identifier_count": 0,
        "eligible_platform_counts": {},
    }
    if os.environ.get("QUIZZLER_SIGNING_BWS_CONSUMER") != BWS_MARKER:
        result["reason"] = "pinned-bws-marker-required"
        print(json.dumps(result, sort_keys=True))
        return 1
    error = configuration_error()
    if error:
        result["reason"] = "configuration-error"
        print(json.dumps(result, sort_keys=True))
        return 1
    try:
        token = _jwt_token()
        _status_event("App Store Connect bundle lookup started (up to 30 seconds)")
        resolved = _resolve_bundle_id_resource(token, _config()["bundle_id"])
        summary = resolved.summary
        result["status"] = "ok"
        result.pop("reason", None)
        result["resource_count"] = summary.resource_count
        result["exact_identifier_count"] = summary.exact_identifier_count
        result["eligible_platform_counts"] = summary.eligible_platform_counts or {}
    except BundleLookupError as exc:
        result["reason"] = exc.reason
        result["resource_count"] = exc.summary.resource_count
        result["exact_identifier_count"] = exc.summary.exact_identifier_count
        result["eligible_platform_counts"] = exc.summary.eligible_platform_counts or {}
    except AscHTTPError as exc:
        result["reason"] = f"asc-http-{exc.status}"
    except SigningError:
        result["reason"] = "lookup-failed"
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def _app_binding_only() -> int:
    """Perform one authenticated, read-only app binding lookup with redacted output."""
    result: dict[str, object] = {
        "status": "blocked",
        "app_count": 0,
        "configured_bundle_match": False,
    }
    if os.environ.get("QUIZZLER_SIGNING_BWS_CONSUMER") != BWS_MARKER:
        result["reason"] = "pinned-bws-marker-required"
        print(json.dumps(result, sort_keys=True))
        return 1
    error = configuration_error()
    if error:
        result["reason"] = "configuration-error"
        print(json.dumps(result, sort_keys=True))
        return 1
    try:
        token = _jwt_token()
        _status_event("App Store Connect app binding lookup started (up to 30 seconds)")
        summary = _resolve_app_binding(token, _config()["app_store_name"], _config()["bundle_id"])
        result["status"] = "ok"
        result.pop("reason", None)
        result["app_count"] = summary.app_count
        result["configured_bundle_match"] = summary.configured_bundle_match
    except AppBindingError as exc:
        result["reason"] = exc.reason
        result["app_count"] = exc.summary.app_count
        result["configured_bundle_match"] = exc.summary.configured_bundle_match
    except AscHTTPError as exc:
        result["reason"] = f"asc-http-{exc.status}"
    except SigningError:
        result["reason"] = "lookup-failed"
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _execute(evidence_path: Path) -> int:
    if os.environ.get("QUIZZLER_SIGNING_BWS_CONSUMER") != BWS_MARKER:
        print("FAIL: signing bootstrap must be launched through the pinned BWS consumer")
        return 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("FAIL: signing bootstrap requires an attended terminal")
        return 1
    if configuration_error():
        print(f"FAIL: signing bootstrap is fail-closed; {configuration_error()}")
        return 1
    evidence: dict[str, Any] = {
        "consumer": CONSUMER,
        "script_sha256": SCRIPT_SHA256,
        "bundle_id": _config()["bundle_id"],
        "certificate": {"status": "not-started"},
        "profile": {"status": "not-started"},
    }
    try:
        token = _jwt_token()
        bundle_id = _config()["bundle_id"]
        _status_event("App Store Connect bundle lookup started (up to 30 seconds)")
        bundle_resource_id = _resolve_bundle_id_resource(token, bundle_id).resource_id

        existing_profile = _find_existing_profile(token, bundle_resource_id)
        if existing_profile is not None:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            profile_path = PROFILES_DIR / SAFE_PROFILE_NAME
            profile_path.write_bytes(existing_profile.profile_bytes)
            profile_hash = hashlib.sha256(existing_profile.profile_bytes).hexdigest()
            evidence["certificate"] = {"status": "reused-existing-profile"}
            evidence["profile"] = {
                "id": existing_profile.profile_id,
                "status": "installed",
                "sha256": profile_hash,
            }
            _write_evidence(evidence_path, evidence)
            print(f"==> Existing signing profile reused; public evidence written to {evidence_path}")
            return 0

        certificate = _select_local_distribution_certificate(token)
        cert_id = certificate.certificate_id
        versioned_name = f"{PROFILE_NAME}-{cert_id}"
        versioned_profile = _find_existing_profile(token, bundle_resource_id, versioned_name)
        if versioned_profile is not None:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
            profile_path = PROFILES_DIR / SAFE_PROFILE_NAME
            profile_path.write_bytes(versioned_profile.profile_bytes)
            evidence["certificate"] = {"id": cert_id, "status": "reused-existing-profile"}
            evidence["profile"] = {
                "id": versioned_profile.profile_id,
                "status": "installed",
                "sha256": hashlib.sha256(versioned_profile.profile_bytes).hexdigest(),
            }
            _write_evidence(evidence_path, evidence)
            print(f"==> Versioned signing profile reused; public evidence written to {evidence_path}")
            return 0

        profile_response = _asc_request(
            token,
            "POST",
            "/profiles",
            {"data": {"type": "profiles", "attributes": {"name": versioned_name, "profileType": "IOS_APP_STORE"}, "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bundle_resource_id}}, "certificates": {"data": [{"type": "certificates", "id": cert_id}]}}}},
        )
        profile_id = _require_data(profile_response, "data", "id")
        profile_content = _require_data(profile_response, "data", "attributes", "profileContent")
        if not isinstance(profile_id, str) or not isinstance(profile_content, str):
            raise SigningError("App Store Connect profile response has invalid public fields")
        try:
            profile_bytes = base64.b64decode(profile_content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SigningError("App Store Connect profile response had invalid content") from exc
        if not profile_bytes:
            raise SigningError("App Store Connect profile response had empty content")
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profile_path = PROFILES_DIR / SAFE_PROFILE_NAME
        profile_path.write_bytes(profile_bytes)
        evidence["certificate"] = {"id": cert_id, "status": "reused-local-certificate"}
        evidence["profile"] = {"id": profile_id, "status": "installed", "sha256": hashlib.sha256(profile_bytes).hexdigest()}
        _write_evidence(evidence_path, evidence)
        print(f"==> Signing bootstrap complete; public evidence written to {evidence_path}")
        return 0
    except AscHTTPError as exc:
        evidence["failure"] = {"status": exc.status, "classification": exc.classification}
        _write_evidence(evidence_path, evidence)
        print(f"FAIL: {exc}")
        return 1
    except SigningError as exc:
        evidence["failure"] = {"classification": str(exc)}
        _write_evidence(evidence_path, evidence)
        print(f"FAIL: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="emit the inert plan (default)")
    parser.add_argument("--lookup-only", action="store_true", help="perform one redacted, read-only bundle lookup")
    parser.add_argument("--app-binding-only", action="store_true", help="perform one redacted, read-only app binding lookup")
    parser.add_argument("--execute", action="store_true", help="perform the attended bootstrap")
    parser.add_argument("--approve", action="store_true", help="explicitly approve the attended bootstrap")
    parser.add_argument("--evidence-path", type=Path, help="gitignored path for public evidence")
    args = parser.parse_args(argv)
    if args.app_binding_only:
        return _app_binding_only()
    if args.lookup_only:
        return _lookup_only()
    if not args.execute:
        print(json.dumps(bootstrap_plan(), indent=2, sort_keys=True))
        return 0
    if not args.approve:
        print("FAIL: signing bootstrap is fail-closed; explicit --approve is required")
        return 1
    if args.evidence_path is None:
        print("FAIL: signing bootstrap is fail-closed; --evidence-path is required")
        return 1
    return _execute(args.evidence_path.expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
