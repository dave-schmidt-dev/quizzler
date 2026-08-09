#!/usr/bin/env python3
"""Attended, fail-closed bootstrap for Quizzler distribution signing.

The default mode is a secret-free plan.  The live path is intentionally
reachable only through the pinned BWS consumer, requires an explicit approval
flag, and keeps the private key in the login Keychain for its entire lifetime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any
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


def _login_keychain() -> str:
    """Return the login keychain shared by key generation and certificate import."""
    return str(Path.home() / "Library" / "Keychains" / "login.keychain-db")


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
            "generate an RSA-2048 key and CSR in the login Keychain",
            "POST /v1/certificates with the public CSR",
            "import the returned public certificate into Keychain",
            "resolve the configured bundle ID and POST /v1/profiles",
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


def _run_public_command(arguments: list[str], *, label: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    print(f"==> {label}", flush=True)
    try:
        result = subprocess.run(
            arguments,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SigningError(f"{label} failed or timed out") from exc
    if result.returncode != 0:
        raise SigningError(f"{label} failed")
    return result


def _create_key_and_csr() -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    keychain = _login_keychain()
    temporary = tempfile.TemporaryDirectory(prefix="quizzler-signing-")
    csr_path = Path(temporary.name) / "distribution.csr"
    try:
        _run_public_command(
            ["/usr/bin/certtool", "r", str(csr_path), f"k={keychain}"],
            label="Creating the private key and public CSR in the login Keychain",
            input_text=(
                "Quizzler Apple Distribution\nr\n2048\ny\ns\n2\ny\n"
                "quizzler-distribution\nQuizzler Distribution\nUS\nZero Delta LLC\n"
                "Quizzler\nNew York\ndistribution@zerodelta.example\ny\n"
            ),
        )
    except Exception:
        temporary.cleanup()
        raise
    if not csr_path.is_file():
        temporary.cleanup()
        raise SigningError("CSR creation produced no public CSR")
    return csr_path, temporary


def _require_data(response: dict[str, Any], *keys: str) -> Any:
    value: Any = response
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise SigningError("App Store Connect response omitted a required public field")
        value = value[key]
    return value


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
    required = ["security", "certtool"]
    missing = [tool for tool in required if shutil.which(f"/usr/bin/{tool}") is None]
    if missing:
        print(f"FAIL: signing prerequisites missing ({', '.join(missing)}); human setup required")
        return 1

    evidence: dict[str, Any] = {
        "consumer": CONSUMER,
        "script_sha256": SCRIPT_SHA256,
        "bundle_id": _config()["bundle_id"],
        "certificate": {"status": "not-started"},
        "profile": {"status": "not-started"},
    }
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        token = _jwt_token()
        bundle_id = _config()["bundle_id"]
        _status_event("App Store Connect bundle lookup started (up to 30 seconds)")
        bundle_response = _asc_request(token, "GET", "/bundleIds?" + urlencode({"filter[identifier]": bundle_id}))
        bundle_data = _require_data(bundle_response, "data")
        if not isinstance(bundle_data, list) or len(bundle_data) != 1 or not isinstance(bundle_data[0], dict):
            raise SigningError("configured bundle ID was not resolved uniquely in App Store Connect")
        bundle_resource_id = bundle_data[0].get("id")
        if not isinstance(bundle_resource_id, str) or not bundle_resource_id:
            raise SigningError("App Store Connect bundle ID response omitted its public ID")

        csr_path, temporary = _create_key_and_csr()
        csr_bytes = csr_path.read_bytes()
        evidence["csr_sha256"] = hashlib.sha256(csr_bytes).hexdigest()
        certificate = _asc_request(
            token,
            "POST",
            "/certificates",
            {"data": {"type": "certificates", "attributes": {"certificateType": "DISTRIBUTION", "csrContent": csr_bytes.decode()}}},
        )
        cert_id = _require_data(certificate, "data", "id")
        cert_content = _require_data(certificate, "data", "attributes", "certificateContent")
        if not isinstance(cert_id, str) or not isinstance(cert_content, str):
            raise SigningError("App Store Connect certificate response has invalid public fields")
        cert_der = base64.b64decode(cert_content, validate=True)
        cert_path = Path(temporary.name) / "distribution.cer"
        cert_path.write_bytes(cert_der)
        _run_public_command(
            ["/usr/bin/security", "import", str(cert_path), "-k", _login_keychain(), "-T", "/usr/bin/codesign"],
            label="Importing the public certificate into the login Keychain",
        )
        evidence["certificate"] = {"id": cert_id, "status": "imported", "sha256": hashlib.sha256(cert_der).hexdigest()}

        profile_response = _asc_request(
            token,
            "POST",
            "/profiles",
            {"data": {"type": "profiles", "attributes": {"name": PROFILE_NAME, "profileType": "IOS_APP_STORE"}, "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bundle_resource_id}}, "certificates": {"data": [{"type": "certificates", "id": cert_id}]}}}},
        )
        profile_id = _require_data(profile_response, "data", "id")
        profile_content = _require_data(profile_response, "data", "attributes", "profileContent")
        if not isinstance(profile_id, str) or not isinstance(profile_content, str):
            raise SigningError("App Store Connect profile response has invalid public fields")
        profile_bytes = base64.b64decode(profile_content, validate=True)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profile_path = PROFILES_DIR / SAFE_PROFILE_NAME
        profile_path.write_bytes(profile_bytes)
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
    finally:
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="emit the inert plan (default)")
    parser.add_argument("--execute", action="store_true", help="perform the attended bootstrap")
    parser.add_argument("--approve", action="store_true", help="explicitly approve the attended bootstrap")
    parser.add_argument("--evidence-path", type=Path, help="gitignored path for public evidence")
    args = parser.parse_args(argv)
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
