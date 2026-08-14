#!/usr/bin/env python3
"""Restart boundaries for TestFlight promotion."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_release_readiness import Fixture  # noqa: E402
from test_testflight_workflow import FakeProvider  # noqa: E402
from testflight_workflow import (ArchiveArtifact, IpaArtifact, ReleaseIdentity, WorkflowError,
                                 run_candidate_workflow, run_workflow)  # noqa: E402


class V2Provider:
    def __init__(self, root: Path, *, fail_poll: bool = False) -> None:
        self.root = root
        self.fail_poll = fail_poll
        self.calls: list[str] = []
        self.uploads = 0
        self.identity = ReleaseIdentity("1.2.3-17", "1.2.3", "17", "head-a")

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_poll and name == "poll":
            raise RuntimeError("poll interrupted")

    def verify_runtime(self) -> None: self._call("runtime")
    def run_full_gate(self) -> None: self._call("gate")
    def verify_signing_ready(self, _: ReleaseIdentity) -> None: self._call("signing")
    def verify_readiness(self) -> ReleaseIdentity: self._call("readiness"); return self.identity
    def archive(self, _: ReleaseIdentity) -> ArchiveArtifact:
        self._call("archive"); path = self.root / "archive.bin"; path.write_bytes(b"archive"); return ArchiveArtifact(path, __import__("hashlib").sha256(path.read_bytes()).hexdigest())
    def inspect_archive(self, *_: object) -> None: self._call("inspect")
    def package_ipa(self, *_: object) -> IpaArtifact:
        self._call("package"); path = self.root / "ipa.bin"; path.write_bytes(b"ipa"); return IpaArtifact(path, __import__("hashlib").sha256(path.read_bytes()).hexdigest())
    def run_final_validation(self, *_: object) -> None: self._call("validation")
    def attended_upload(self, *_: object) -> str: self._call("upload"); self.uploads += 1; return "asc-build-17"
    def poll_exact_build(self, *_: object) -> None: self._call("poll")
    def resolve_compliance(self, *_: object) -> None: self._call("compliance")
    def assign_internal_group(self, *_: object) -> None: self._call("group")
    def verify_receipt(self, *_: object) -> None: self._call("receipt")
    def record_evidence(self, *_: object) -> None: self._call("evidence")
    def notify(self, *_: object) -> None: self._call("notify")


class ReleaseRestartTests(unittest.TestCase):
    def test_v2_resume_uses_attestation_and_never_reuploads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            fixture.candidate.joinpath("artifact-attestation.json").unlink()
            first = V2Provider(fixture.candidate, fail_poll=True)
            with self.assertRaisesRegex(WorkflowError, "provider-operation-failed"):
                run_candidate_workflow(first, manifest_path=fixture.manifest, attended=True, on_status=lambda _: None)
            self.assertEqual(first.uploads, 1)
            second = V2Provider(fixture.candidate)
            state = run_candidate_workflow(second, manifest_path=fixture.manifest, attended=True, on_status=lambda _: None)
            self.assertEqual(state["stage"], "complete")
            self.assertEqual(second.uploads, 0)
            self.assertNotIn("archive", second.calls)
            self.assertNotIn("package", second.calls)

    def test_post_upload_resume_never_reuploads_or_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            first = FakeProvider(root, failure="poll:asc-build-17")
            with self.assertRaisesRegex(WorkflowError, "provider-operation-failed"):
                run_workflow(first, state_path=state_path, attended=True, on_status=lambda _: None)
            self.assertIn("upload", first.calls)

            second = FakeProvider(root)
            state = run_workflow(second, state_path=state_path, attended=True, on_status=lambda _: None)
            self.assertEqual(state["stage"], "complete")
            self.assertNotIn("upload", second.calls)
            self.assertNotIn("archive", second.calls)
            self.assertNotIn("package", second.calls)
            self.assertEqual(second.calls, [
                "readiness", "poll:asc-build-17", "compliance:asc-build-17", "group:asc-build-17",
                "receipt:asc-build-17", "evidence:asc-build-17", "notify:asc-build-17",
            ])

    def test_resume_rejects_wrong_frozen_identity_before_upload_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            first = FakeProvider(root, failure="upload")
            with self.assertRaisesRegex(WorkflowError, "provider-operation-failed"):
                run_workflow(first, state_path=state_path, attended=True, on_status=lambda _: None)
            self.assertTrue(state_path.exists())

            wrong = FakeProvider(root)
            wrong.identity = wrong.identity.__class__("1.2.3-18", "1.2.3", "18", "head-a")
            with self.assertRaisesRegex(WorkflowError, "testflight-resume-identity-drift"):
                run_workflow(wrong, state_path=state_path, attended=True, on_status=lambda _: None)
            self.assertEqual(wrong.calls, ["readiness"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
