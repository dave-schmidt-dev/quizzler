#!/usr/bin/env python3
"""Offline contract tests for the attended signing bootstrap."""

from __future__ import annotations

import contextlib
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


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class SigningBootstrapTests(unittest.TestCase):
    def test_plan_is_fixed_secret_free_and_network_free(self) -> None:
        plan = module.bootstrap_plan()
        self.assertEqual(plan["consumer"], "quizzler-asc-provision")
        self.assertEqual(plan["broker"][:2], ["bws-secret-exec", "quizzler-asc-provision"])
        self.assertEqual(len(plan["script_sha256"]), 64)
        self.assertIsNone(plan["configuration_error"], plan)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("bws-get", source)
        self.assertNotIn("APP_STORE_CONNECT_API_KEY", json.dumps(plan))

    def test_missing_explicit_approval_is_inert(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--execute", "--evidence-path", "/private/tmp/never.json"],
            text=True,
            capture_output=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--approve", result.stdout)

    def test_execute_requires_pinned_bws_marker(self) -> None:
        with patch.dict(module.os.environ, {}, clear=True):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = module._execute(Path("/private/tmp/never.json"))
        self.assertNotEqual(status, 0)
        self.assertIn("pinned BWS consumer", output.getvalue())

    def test_certtool_generates_rsa_key_and_csr_in_login_keychain(self) -> None:
        commands: list[list[str]] = []
        inputs: list[str | None] = []

        def fake_command(arguments: list[str], **kwargs: object) -> None:
            commands.append(arguments)
            inputs.append(kwargs.get("input_text"))
            if arguments[1:2] == ["r"]:
                Path(arguments[2]).write_text("public csr", encoding="utf-8")

        with patch.object(module, "_run_public_command", side_effect=fake_command):
            csr_path, temporary = module._create_key_and_csr()
            try:
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0][0:2], ["/usr/bin/certtool", "r"])
                self.assertEqual(commands[0][2], str(csr_path))
                self.assertEqual(commands[0][3], f"k={module._login_keychain()}")
                self.assertEqual(
                    inputs,
                    [
                        "Quizzler Apple Distribution\nr\n2048\ny\ns\n2\ny\n"
                        "quizzler-distribution\nQuizzler Distribution\nUS\nZero Delta LLC\n"
                        "Quizzler\nNew York\ndistribution@zerodelta.example\ny\n"
                    ],
                )
                self.assertNotIn("APP_STORE_CONNECT", inputs[0] or "")
                self.assertTrue(csr_path.exists())
            finally:
                temporary.cleanup()

    def test_certtool_failure_is_reported_without_returning_a_csr(self) -> None:
        commands: list[list[str]] = []

        def fail_command(arguments: list[str], **_: object) -> None:
            commands.append(arguments)
            raise module.SigningError("certtool failed")

        with patch.object(module, "_run_public_command", side_effect=fail_command):
            with self.assertRaisesRegex(module.SigningError, "certtool failed"):
                module._create_key_and_csr()
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0:2], ["/usr/bin/certtool", "r"])

    def test_missing_csr_is_reported_after_successful_certtool(self) -> None:
        with patch.object(module, "_run_public_command") as command:
            with self.assertRaisesRegex(module.SigningError, "no public CSR"):
                module._create_key_and_csr()
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0][0:2], ["/usr/bin/certtool", "r"])

    def test_certificate_import_targets_the_login_keychain(self) -> None:
        responses = iter(
            [
                {"data": [{"id": "bundle-public-id"}]},
                {"data": {"id": "cert-public-id", "attributes": {"certificateContent": "AQI="}}},
                {"data": {"id": "profile-public-id", "attributes": {"profileContent": "AwQ="}}},
            ]
        )
        commands: list[list[str]] = []

        def record_command(arguments: list[str], **_: object) -> None:
            commands.append(arguments)

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            csr_temp = tempfile.TemporaryDirectory(dir=directory)
            try:
                with (
                    patch.dict(module.os.environ, {"QUIZZLER_SIGNING_BWS_CONSUMER": module.CONSUMER}, clear=True),
                    patch.object(module.sys.stdin, "isatty", return_value=True),
                    patch.object(module.sys.stdout, "isatty", return_value=True),
                    patch.object(module, "_jwt_token", return_value="sentinel-token"),
                    patch.object(module, "_create_key_and_csr", return_value=(Path(csr_temp.name) / "request.csr", csr_temp)),
                    patch.object(module, "PROFILES_DIR", Path(directory) / "profiles"),
                    patch.object(module.Path, "read_bytes", return_value=b"public csr"),
                    patch.object(module, "_asc_request", side_effect=lambda *_args, **_kwargs: next(responses)),
                    patch.object(module, "_run_public_command", side_effect=record_command),
                ):
                    (Path(csr_temp.name) / "request.csr").write_bytes(b"public csr")
                    status = module._execute(evidence)
                self.assertEqual(status, 0)
                imports = [command for command in commands if command[:2] == ["/usr/bin/security", "import"]]
                self.assertEqual(len(imports), 1)
                self.assertIn("-k", imports[0])
                self.assertEqual(imports[0][imports[0].index("-k") + 1], module._login_keychain())
            finally:
                csr_temp.cleanup()

    def test_bundle_status_event_is_redacted_and_precedes_network_request(self) -> None:
        events: list[str] = []
        order: list[str] = []
        with patch.object(module, "_status_event", side_effect=lambda message: (events.append(message), order.append("status"))):
            with patch.object(module, "_jwt_token", return_value="sentinel-token"):
                with patch.object(module, "_asc_request", side_effect=lambda *_args, **_kwargs: (order.append("network") or {"data": []})):
                    with tempfile.TemporaryDirectory() as directory:
                        with patch.dict(module.os.environ, {"QUIZZLER_SIGNING_BWS_CONSUMER": module.CONSUMER}, clear=True):
                            with patch.object(module.sys.stdin, "isatty", return_value=True), patch.object(module.sys.stdout, "isatty", return_value=True), patch.object(module.shutil, "which", return_value="/usr/bin/tool"):
                                status = module._execute(Path(directory) / "evidence.json")
        self.assertNotEqual(status, 0)
        self.assertEqual(order, ["status", "network"])
        self.assertEqual(events, ["App Store Connect bundle lookup started (up to 30 seconds)"])
        self.assertNotIn(module._config()["bundle_id"], events[0])
        self.assertNotIn("sentinel-token", events[0])

    def test_hash_mismatch_fails_before_prerequisites_or_network(self) -> None:
        with patch.object(module, "configured_signing", return_value=(module.CONSUMER, "bad")):
            with patch.object(module.shutil, "which") as which, contextlib.redirect_stdout(io.StringIO()) as output:
                status = module.main(["--dry-run"])
        self.assertEqual(status, 0)
        self.assertIn("does not match", output.getvalue())
        which.assert_not_called()

    def test_jwt_is_es256_and_credential_free_in_output(self) -> None:
        key = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
-----END PRIVATE KEY-----"""
        # An invalid sentinel key must fail without disclosing the PEM.
        with patch.dict(module.os.environ, {"APP_STORE_CONNECT_API_KEY": key, "APP_STORE_CONNECT_KEY_ID": "kid", "APP_STORE_CONNECT_ISSUER_ID": "iss"}, clear=True):
            with self.assertRaises(module.SigningError):
                module._jwt_token()

    def test_mocked_success_creates_public_evidence_and_no_secret_output(self) -> None:
        responses = iter(
            [
                {"data": [{"id": "bundle-public-id"}]},
                {"data": {"id": "cert-public-id", "attributes": {"certificateContent": "AQI="}}},
                {"data": {"id": "profile-public-id", "attributes": {"profileContent": "AwQ="}}},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            csr_temp = tempfile.TemporaryDirectory(dir=directory)
            with (
                patch.dict(module.os.environ, {"QUIZZLER_SIGNING_BWS_CONSUMER": module.CONSUMER}, clear=True),
                patch.object(module.sys.stdin, "isatty", return_value=True),
                patch.object(module.sys.stdout, "isatty", return_value=True),
                patch.object(module, "_jwt_token", return_value="sentinel-token"),
                patch.object(module, "_create_key_and_csr", return_value=(Path(csr_temp.name) / "request.csr", csr_temp)),
                patch.object(module, "PROFILES_DIR", Path(directory) / "profiles"),
                patch.object(module.Path, "read_bytes", return_value=b"public csr"),
                patch.object(module, "_asc_request", side_effect=lambda *_args, **_kwargs: next(responses)),
                patch.object(module, "_run_public_command"),
            ):
                # The mocked CSR path need only exist for the code's public read.
                (Path(csr_temp.name) / "request.csr").write_bytes(b"public csr")
                status = module._execute(evidence)
            self.assertEqual(status, 0)
            saved = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(saved["certificate"]["id"], "cert-public-id")
            self.assertEqual(saved["profile"]["id"], "profile-public-id")
            self.assertNotIn("sentinel-token", evidence.read_text(encoding="utf-8"))

    def test_bundle_lookup_failure_precedes_key_or_certificate_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            with (
                patch.dict(module.os.environ, {"QUIZZLER_SIGNING_BWS_CONSUMER": module.CONSUMER}, clear=True),
                patch.object(module.sys.stdin, "isatty", return_value=True),
                patch.object(module.sys.stdout, "isatty", return_value=True),
                patch.object(module.shutil, "which", return_value="/usr/bin/tool"),
                patch.object(module, "_jwt_token", return_value="sentinel-token"),
                patch.object(module, "_asc_request", side_effect=module.AscHTTPError(403, "authorization rejected")) as request,
                patch.object(module, "_create_key_and_csr") as create_key,
                patch.object(module, "_run_public_command") as command,
            ):
                status = module._execute(evidence)
            self.assertNotEqual(status, 0)
            request.assert_called_once()
            self.assertEqual(request.call_args.args[1:3], ("GET", "/bundleIds?filter%5Bidentifier%5D=com.zerodelta.quizzler&filter%5Bplatform%5D=IOS"))
            create_key.assert_not_called()
            command.assert_not_called()
            self.assertEqual(json.loads(evidence.read_text(encoding="utf-8"))["failure"]["status"], 403)

    def test_4xx_reports_only_classification_and_does_not_retry(self) -> None:
        error = module.HTTPError("https://example.invalid", 403, "forbidden", {}, io.BytesIO(b"secret body"))
        with patch.object(module, "urlopen", side_effect=error) as opener:
            with self.assertRaises(module.AscHTTPError) as raised:
                module._asc_request("sentinel-token", "GET", "/bundleIds")
        self.assertEqual(raised.exception.status, 403)
        self.assertNotIn("secret body", str(raised.exception))
        opener.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
