#!/usr/bin/env python3
"""Offline contract tests for the attended signing bootstrap."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "provision_signing.py"
spec = importlib.util.spec_from_file_location("provision_signing", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _bundle_response() -> dict[str, object]:
    return {
        "data": [{
            "type": "bundleIds",
            "id": "bundle-public-id",
            "attributes": {"identifier": "com.zerodelta.quizzler", "platform": "IOS"},
        }]
    }


def _profile(profile_id: str, name: str, content: bytes = b"profile") -> dict[str, object]:
    return {
        "type": "profiles",
        "id": profile_id,
        "attributes": {
            "name": name,
            "profileType": "IOS_APP_STORE",
            "profileState": "ACTIVE",
            "profileContent": base64.b64encode(content).decode(),
        },
    }


class SigningBootstrapTests(unittest.TestCase):
    def _execute(self, responses: list[dict[str, object]], directory: str, *, local_serials: set[str] | None = None) -> tuple[int, list[tuple[str, str]], Path]:
        calls: list[tuple[str, str]] = []
        response_iter = iter(responses)

        def request(_token: str, method: str, path: str, *_body: object, **_: object) -> dict[str, object]:
            calls.append((method, path))
            return next(response_iter)

        evidence = Path(directory) / "evidence.json"
        with (
            patch.dict(module.os.environ, {"QUIZZLER_SIGNING_BWS_CONSUMER": module.CONSUMER}, clear=True),
            patch.object(module.sys.stdin, "isatty", return_value=True),
            patch.object(module.sys.stdout, "isatty", return_value=True),
            patch.object(module, "_jwt_token", return_value="sentinel-token"),
            patch.object(module, "_asc_request", side_effect=request),
            patch.object(module, "local_certificate_serials", return_value=local_serials or set()),
            patch.object(module, "PROFILES_DIR", Path(directory) / "profiles"),
        ):
            status = module._execute(evidence)
        return status, calls, evidence

    def test_plan_is_fixed_secret_free_and_network_free(self) -> None:
        plan = module.bootstrap_plan()
        self.assertEqual(plan["consumer"], module.CONSUMER)
        self.assertEqual(len(plan["script_sha256"]), 64)
        self.assertIsNone(plan["configuration_error"], plan)
        self.assertNotIn("bws-get", SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn("APP_STORE_CONNECT_API_KEY", json.dumps(plan))

    def test_lookup_only_requires_marker_without_building_jwt(self) -> None:
        with patch.dict(module.os.environ, {}, clear=True), patch.object(module, "_jwt_token") as jwt:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = module._lookup_only()
        self.assertNotEqual(status, 0)
        jwt.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["reason"], "pinned-bws-marker-required")

    def test_app_binding_lookup_uses_name_filter_and_matches_configured_bundle(self) -> None:
        with patch.object(module, "_asc_request", return_value={"data": [{"type": "apps", "id": "app-id", "attributes": {"bundleId": "com.zerodelta.quizzler"}}]}) as request:
            result = module._resolve_app_binding("sentinel-token", "Quzzler", "com.zerodelta.quizzler")
        self.assertEqual(result, module.AppBindingSummary(1, True))
        self.assertEqual(request.call_args.args[1:], ("GET", "/apps?filter%5Bname%5D=Quzzler"))

    def test_app_binding_rejects_mismatch_and_cardinality(self) -> None:
        for payload, reason in (
            ({"data": []}, "app-cardinality"),
            ({"data": [{"type": "apps", "id": "one", "attributes": {"bundleId": "com.other.app"}}]}, "bundle-mismatch"),
        ):
            with self.subTest(reason=reason), patch.object(module, "_asc_request", return_value=payload):
                with self.assertRaisesRegex(module.AppBindingError, reason):
                    module._resolve_app_binding("sentinel-token", "Quzzler", "com.zerodelta.quizzler")

    def test_missing_explicit_approval_is_inert(self) -> None:
        result = subprocess.run([sys.executable, str(SCRIPT), "--execute", "--evidence-path", "/private/tmp/never.json"], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--approve", result.stdout)

    def test_execute_requires_pinned_bws_marker(self) -> None:
        with patch.dict(module.os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            status = module._execute(Path("/private/tmp/never.json"))
        self.assertNotEqual(status, 0)
        self.assertIn("pinned BWS consumer", output.getvalue())

    def test_local_certificate_serials_uses_security_and_openssl(self) -> None:
        listing = subprocess.CompletedProcess([], 0, "prefix\n-----BEGIN CERTIFICATE-----\ncert-one\n-----END CERTIFICATE-----\n-----BEGIN CERTIFICATE-----\ncert-two\n-----END CERTIFICATE-----\n", "")
        serial_one = subprocess.CompletedProcess([], 0, "serial=ab12\n", "")
        serial_two = subprocess.CompletedProcess([], 0, "serial=CD34\n", "")
        with patch.object(module.subprocess, "run", side_effect=[listing, serial_one, serial_two]) as run:
            self.assertEqual(module.local_certificate_serials("Apple Distribution"), {"AB12", "CD34"})
        self.assertEqual(run.call_args_list[0].args[0], ["security", "find-certificate", "-a", "-c", "Apple Distribution", "-p"])
        self.assertEqual(run.call_args_list[1].args[0], ["openssl", "x509", "-noout", "-serial"])

    def test_certificate_selector_fails_closed_for_zero_matches(self) -> None:
        with patch.object(module, "_asc_request", return_value={"data": [{"type": "certificates", "id": "cert-id", "attributes": {"serialNumber": "A1", "expirationDate": "2027-01-01T00:00:00Z"}}]}), patch.object(module, "local_certificate_serials", return_value=set()):
            with self.assertRaisesRegex(module.SigningError, "no App Store Connect distribution certificate"):
                module._select_local_distribution_certificate("sentinel-token")

    def test_certificate_selector_uses_latest_expiration_and_requests_fields(self) -> None:
        payload = {"data": [
            {"type": "certificates", "id": "cert-one", "attributes": {"serialNumber": "A1", "expirationDate": "2027-01-01T00:00:00Z"}},
            {"type": "certificates", "id": "cert-two", "attributes": {"serialNumber": "B2", "expirationDate": "2028-01-01T00:00:00Z"}},
        ]}
        with patch.object(module, "_asc_request", return_value=payload) as request, patch.object(module, "local_certificate_serials", return_value={"A1", "B2"}):
            result = module._select_local_distribution_certificate("sentinel-token")
        self.assertEqual(result, module.DistributionCertificateResult("cert-two", "B2"))
        path = request.call_args.args[2]
        self.assertIn("filter%5BcertificateType%5D=DISTRIBUTION", path)
        self.assertIn("fields%5Bcertificates%5D=serialNumber%2CexpirationDate", path)

    def test_certificate_selector_fails_closed_for_malformed_expiration(self) -> None:
        payload = {"data": [{"type": "certificates", "id": "cert-id", "attributes": {"serialNumber": "A1", "expirationDate": "not-a-date"}}]}
        with patch.object(module, "_asc_request", return_value=payload), patch.object(module, "local_certificate_serials", return_value={"A1"}):
            with self.assertRaisesRegex(module.SigningError, "invalid expiration date"):
                module._select_local_distribution_certificate("sentinel-token")

    def test_certificate_selector_fails_closed_for_latest_expiration_tie(self) -> None:
        expiration = "2028-01-01T00:00:00Z"
        payload = {"data": [
            {"type": "certificates", "id": "cert-one", "attributes": {"serialNumber": "A1", "expirationDate": expiration}},
            {"type": "certificates", "id": "cert-two", "attributes": {"serialNumber": "B2", "expirationDate": expiration}},
        ]}
        with patch.object(module, "_asc_request", return_value=payload), patch.object(module, "local_certificate_serials", return_value={"A1", "B2"}):
            with self.assertRaisesRegex(module.SigningError, "share the latest expiration"):
                module._select_local_distribution_certificate("sentinel-token")

    def test_fixed_active_profile_reuse_skips_certificate_lookup_and_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status, calls, evidence = self._execute([_bundle_response(), {"data": [_profile("fixed-id", module.PROFILE_NAME, b"fixed")]}], directory, local_serials={"A1"})
            self.assertEqual(status, 0)
            self.assertEqual([method for method, _ in calls], ["GET", "GET"])
            self.assertEqual(json.loads(evidence.read_text())["certificate"]["status"], "reused-existing-profile")
            self.assertEqual((Path(directory) / "profiles" / module.SAFE_PROFILE_NAME).read_bytes(), b"fixed")

    def test_multiple_fixed_profiles_fail_before_certificate_lookup_or_mutation(self) -> None:
        profiles = {"data": [_profile("one", module.PROFILE_NAME), _profile("two", module.PROFILE_NAME)]}
        with tempfile.TemporaryDirectory() as directory:
            status, calls, evidence = self._execute([_bundle_response(), profiles], directory, local_serials={"A1"})
            self.assertNotEqual(status, 0)
            self.assertEqual([method for method, _ in calls], ["GET", "GET"])
            self.assertNotIn("POST", [method for method, _ in calls])
            self.assertIn("multiple active matching", evidence.read_text())

    def test_versioned_profile_reuse_prechecks_before_post(self) -> None:
        cert_id = "cert-id"
        versioned_name = f"{module.PROFILE_NAME}-{cert_id}"
        responses = [_bundle_response(), {"data": []}, {"data": [{"type": "certificates", "id": cert_id, "attributes": {"serialNumber": "A1", "expirationDate": "2028-01-01T00:00:00Z"}}]}, {"data": [_profile("versioned-id", versioned_name, b"versioned")]}]
        with tempfile.TemporaryDirectory() as directory:
            status, calls, evidence = self._execute(responses, directory, local_serials={"A1"})
            self.assertEqual(status, 0)
            self.assertEqual([method for method, _ in calls], ["GET", "GET", "GET", "GET"])
            saved = json.loads(evidence.read_text())
            self.assertEqual(saved["profile"]["id"], "versioned-id")
            self.assertEqual((Path(directory) / "profiles" / module.SAFE_PROFILE_NAME).read_bytes(), b"versioned")

    def test_versioned_profile_creation_posts_only_profile_and_never_deletes(self) -> None:
        cert_id = "cert-id"
        versioned_name = f"{module.PROFILE_NAME}-{cert_id}"
        responses = [
            _bundle_response(), {"data": []},
            {"data": [{"type": "certificates", "id": cert_id, "attributes": {"serialNumber": "A1", "expirationDate": "2028-01-01T00:00:00Z"}}]},
            {"data": []},
            {"data": {"type": "profiles", "id": "created-id", "attributes": {"profileContent": base64.b64encode(b"created").decode()}}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            status, calls, evidence = self._execute(responses, directory, local_serials={"A1"})
            self.assertEqual(status, 0)
            self.assertEqual([method for method, _ in calls], ["GET", "GET", "GET", "GET", "POST"])
            self.assertNotIn(("POST", "/certificates"), calls)
            self.assertFalse(any(method == "DELETE" for method, _ in calls))
            post = calls[-1]
            self.assertEqual(post, ("POST", "/profiles"))
            saved = json.loads(evidence.read_text())
            self.assertEqual(saved["certificate"], {"id": cert_id, "status": "reused-local-certificate"})
            self.assertEqual(saved["profile"]["id"], "created-id")

    def test_no_matching_certificate_fails_before_profile_post(self) -> None:
        responses = [_bundle_response(), {"data": []}, {"data": [{"type": "certificates", "id": "cert-id", "attributes": {"serialNumber": "A1", "expirationDate": "2028-01-01T00:00:00Z"}}]}]
        with tempfile.TemporaryDirectory() as directory:
            status, calls, evidence = self._execute(responses, directory, local_serials=set())
            self.assertNotEqual(status, 0)
            self.assertEqual([method for method, _ in calls], ["GET", "GET", "GET"])
            self.assertFalse((Path(directory) / "profiles" / module.SAFE_PROFILE_NAME).exists())
            self.assertNotIn("POST", [method for method, _ in calls])
            self.assertIn("no App Store Connect distribution certificate", evidence.read_text())

    def test_bundle_lookup_requires_one_exact_ios_or_universal_resource(self) -> None:
        payload = {"data": [
            {"type": "bundleIds", "id": "prefix", "attributes": {"identifier": "com.zerodelta.quizzler.dev", "platform": "IOS"}},
            {"type": "bundleIds", "id": "exact", "attributes": {"identifier": "com.zerodelta.quizzler", "platform": "UNIVERSAL"}},
        ]}
        with patch.object(module, "_asc_request", return_value=payload):
            result = module._resolve_bundle_id_resource("sentinel-token", "com.zerodelta.quizzler")
        self.assertEqual(result.resource_id, "exact")

    def test_bundle_lookup_rejects_zero_or_multiple_exact_resources(self) -> None:
        cases = (
            {"data": []},
            {"data": [{"type": "bundleIds", "id": "one", "attributes": {"identifier": "com.zerodelta.quizzler", "platform": "IOS"}}, {"type": "bundleIds", "id": "two", "attributes": {"identifier": "com.zerodelta.quizzler", "platform": "UNIVERSAL"}}]},
        )
        for payload in cases:
            with self.subTest(payload=payload), patch.object(module, "_asc_request", return_value=payload):
                with self.assertRaisesRegex(module.BundleLookupError, "exact-identifier-cardinality"):
                    module._resolve_bundle_id_resource("sentinel-token", "com.zerodelta.quizzler")

    def test_4xx_reports_only_classification_and_does_not_retry(self) -> None:
        error = module.HTTPError("https://example.invalid", 403, "forbidden", {}, io.BytesIO(b"secret body"))
        with patch.object(module, "urlopen", side_effect=error) as opener:
            with self.assertRaises(module.AscHTTPError) as raised:
                module._asc_request("sentinel-token", "GET", "/bundleIds")
        self.assertEqual(raised.exception.status, 403)
        self.assertNotIn("secret body", str(raised.exception))
        opener.assert_called_once()

    def test_4xx_retains_only_a_structural_asc_error_code(self) -> None:
        body = b'{"errors":[{"code":"ENTITY_ERROR.ATTRIBUTE.INVALID","detail":"secret body"}]}'
        error = module.HTTPError("https://example.invalid", 409, "conflict", {}, io.BytesIO(body))
        with patch.object(module, "urlopen", side_effect=error):
            with self.assertRaises(module.AscHTTPError) as raised:
                module._asc_request("sentinel-token", "POST", "/buildUploads")
        self.assertEqual(raised.exception.error_code, "ENTITY_ERROR.ATTRIBUTE.INVALID")
        self.assertNotIn("secret body", str(raised.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
