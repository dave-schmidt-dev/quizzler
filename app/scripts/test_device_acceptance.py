#!/usr/bin/env python3
"""Focused device-acceptance verifier contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_acceptance import (  # noqa: E402
    DeviceAcceptanceError,
    collect_attended_device_evidence,
    verify_device_evidence,
)
from release_readiness import ReadinessError  # noqa: E402
from test_release_readiness import Fixture  # noqa: E402
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


class DeviceAcceptanceTests(unittest.TestCase):
    def test_verify_only_rederives_one_physical_production_device(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            report = verify_device_evidence(
                fixture.manifest, fixture.device, repository_root=fixture.root, runtime=DEFAULT_DESTINATION
            )
            self.assertEqual(report["decision"], "verified")
            self.assertEqual(report["deviceCount"], 1)
            self.assertFalse((fixture.candidate / "device-evidence.json").exists())

    def test_rejects_simulator_nonproduction_extra_device_and_hash_mismatch(self) -> None:
        mutations = (
            (lambda value: value["devices"][0].__setitem__("platform", "simulator"), "device-evidence-invalid"),
            (lambda value: value["preflightBuild"].__setitem__("cloudKitContainerEnvironment", "Development"), "device-preflight-attestation-invalid"),
            (lambda value: value["devices"][0].__setitem__("cloudKitContainerEnvironment", "Development"), "device-evidence-invalid"),
            (lambda value: value["devices"][0].__setitem__("sourceDigest", "e" * 64), "device-evidence-invalid"),
            (lambda value: value["devices"][0].__setitem__("cloudKitContainerIdentifier", "iCloud.com.example.other"), "device-evidence-invalid"),
            (lambda value: value["devices"].clear(), "device-evidence-invalid"),
            (lambda value: value["devices"].append(dict(value["devices"][0])), "device-evidence-invalid"),
            (lambda value: value["devices"][0].__setitem__("signedBuildSha256", "e" * 64), "device-evidence-invalid"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                value = json.loads(fixture.device.read_text(encoding="utf-8"))
                mutate(value)
                write_json(fixture.device, value)
                with self.assertRaisesRegex((DeviceAcceptanceError, ReadinessError), expected):
                    verify_device_evidence(
                        fixture.manifest, fixture.device, repository_root=fixture.root, runtime=DEFAULT_DESTINATION
                    )

    def test_attended_ingest_is_explicit_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            output = fixture.root / "evidence" / "collected-device.json"
            statuses: list[str] = []
            collect_attended_device_evidence(
                fixture.manifest,
                fixture.device,
                output,
                repository_root=fixture.root,
                runtime=DEFAULT_DESTINATION,
                on_status=statuses.append,
            )
            self.assertEqual(json.loads(output.read_text()), json.loads(fixture.device.read_text()))
            self.assertEqual(statuses, ["device-attended-ingest-started", "device-attended-ingest-complete"])
            collect_attended_device_evidence(
                fixture.manifest, fixture.device, output, repository_root=fixture.root, runtime=DEFAULT_DESTINATION
            )

    def test_verify_cli_emits_status_before_local_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            command = [
                sys.executable,
                str(Path(__file__).with_name("device_acceptance.py")),
                "--candidate",
                str(fixture.manifest),
                "--evidence",
                str(fixture.device),
                "--repository",
                str(fixture.root),
                "--verify-only",
            ]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("STATUS device-local-validation-started", result.stderr)
            self.assertIn("STATUS device-local-validation-complete", result.stderr)
            self.assertIn('"decision":"verified"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
