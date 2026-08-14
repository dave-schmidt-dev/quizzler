#!/usr/bin/env python3
"""Aggregate v2 release-adapter quick checks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_adapter  # noqa: E402
from release_adapter import AdapterError, bind_artifact_attestation, central_runtime, freeze_release  # noqa: E402
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402
from test_release_readiness import Fixture  # noqa: E402


class ReleaseAdapterTests(unittest.TestCase):
    def test_adapter_uses_verified_central_module(self) -> None:
        module = central_runtime(DEFAULT_DESTINATION)
        self.assertEqual(Path(module.__file__).resolve(), DEFAULT_DESTINATION.resolve() / "release_tools" / "iterative_release.py")

    def test_v2_candidate_is_byte_stable_and_identity_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            before = fixture.manifest.read_bytes()
            self.assertEqual(freeze_release(fixture.request, state_directory=fixture.state, repository_root=fixture.root, runtime=DEFAULT_DESTINATION).read_bytes(), before)
            request = json.loads(fixture.request.read_text())
            request["sourceDigest"] = "b" * 64
            fixture.request.write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate-manifest-immutable"):
                freeze_release(fixture.request, state_directory=fixture.state, repository_root=fixture.root, runtime=DEFAULT_DESTINATION)

    def test_v1_request_and_manifest_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.write_text('{"formatVersion":"1.0.0"}', encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "release-request-invalid"):
                freeze_release(request, state_directory=root / "state", repository_root=root, runtime=DEFAULT_DESTINATION)

    def test_legacy_v1_manifest_is_rejected_at_artifact_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); candidate = root / "candidate"; candidate.mkdir()
            body = {"formatVersion": 1, "candidateId": "1.2.3-17", "release": {"marketingVersion": "1.2.3", "buildNumber": "17", "gitRevision": "head-a", "frozen": True}, "frozenInputs": {}}
            body["manifestSha256"] = __import__("hashlib").sha256(json.dumps({k: v for k, v in body.items()}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            (candidate / "1.2.3-17.json").write_text(json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "candidate-manifest-v1-rejected"):
                bind_artifact_attestation(candidate / "1.2.3-17.json", candidate / "missing.ipa", runtime=DEFAULT_DESTINATION)

    def test_attestation_is_candidate_local_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            with self.assertRaisesRegex(AdapterError, "artifact-outside-candidate"):
                bind_artifact_attestation(fixture.manifest, fixture.root / "outside.ipa", runtime=DEFAULT_DESTINATION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
