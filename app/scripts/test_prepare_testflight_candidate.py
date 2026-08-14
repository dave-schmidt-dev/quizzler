#!/usr/bin/env python3
"""Contract tests for credential-free v2 candidate bootstrap."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_testflight_candidate import (  # noqa: E402
    CandidatePreparationError,
    _committed_versions,
    prepare_candidate,
)
from release_candidate import CandidateSourceError, source_snapshot  # noqa: E402


PROJECT = """// !$*UTF8*$!
{\n\tobjects = {\n\t\tAAAAAAAAAAAAAAAAAAAAAAAA /* QuizzleriOS */ = {\n\t\t\tisa = PBXNativeTarget;\n\t\t\tbuildConfigurationList = BBBBBBBBBBBBBBBBBBBBBBBB /* Build configuration list for PBXNativeTarget \"QuizzleriOS\" */;\n\t\t};\n\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* Release */ = {\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {\n\t\t\t\tMARKETING_VERSION = 1.2.3;\n\t\t\t\tCURRENT_PROJECT_VERSION = 17;\n\t\t\t};\n\t\t\tname = Release;\n\t\t};\n\t\tBBBBBBBBBBBBBBBBBBBBBBBB /* Build configuration list for PBXNativeTarget \"QuizzleriOS\" */ = {\n\t\t\tisa = XCConfigurationList;\n\t\t\tbuildConfigurations = (\n\t\t\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* Release */,\n\t\t\t);\n\t\t};\n\t};\n}\n"""
CONFIG = """release_product_identifier = \"quizzler-ios\"\nrelease_state_directory = \"app/releases/state\"\nrelease_candidate_format = \"2.0.0\"\nrelease_lane = \"standard\"\nrelease_prebuild_requirements = [\"production-schema\", \"device-acceptance\"]\nrelease_readiness_requirements = [\"production-schema\", \"device-acceptance\", \"asc-build\", \"testflight-receipt\"]\n"""


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["/usr/bin/git", "-C", str(root), *args], text=True, capture_output=True, check=True)
    return result.stdout


class CandidateBootstrapTests(unittest.TestCase):
    def _fixture(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "app" / "Quizzler.xcodeproj").mkdir(parents=True)
        (root / "app" / "scripts").mkdir(parents=True)
        (root / "app" / "Quizzler.xcodeproj" / "project.pbxproj").write_text(PROJECT, encoding="utf-8")
        (root / "app" / "release-config.toml").write_text(CONFIG, encoding="utf-8")
        (root / "app" / "scripts" / "release_adapter.py").write_text("adapter = True\n", encoding="utf-8")
        (root / ".gitignore").write_text("app/releases/state/\n", encoding="utf-8")
        git(root, "init")
        git(root, "config", "user.email", "tests@example.invalid")
        git(root, "config", "user.name", "Test")
        git(root, "add", "app", ".gitignore")
        git(root, "commit", "-m", "fixture")
        return root

    def _freezer(self, root: Path, requests: list[dict[str, object]]):
        def freeze(request: Path, **_: object) -> Path:
            requests.append(json.loads(request.read_text(encoding="utf-8")))
            candidate = root / "app" / "releases" / "state" / "candidates" / "1.2.3-17"
            candidate.mkdir(parents=True, exist_ok=True)
            manifest = candidate / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            return manifest
        return freeze

    def test_committed_versions_are_release_target_values(self) -> None:
        self.assertEqual(_committed_versions(PROJECT), ("1.2.3", "17"))

    def test_clean_app_freezes_snapshot_then_writes_readiness_skeleton(self) -> None:
        root = self._fixture()
        requests: list[dict[str, object]] = []
        manifest, readiness = prepare_candidate(root, freezer=self._freezer(root, requests))
        self.assertTrue(manifest.is_file())
        self.assertTrue(readiness.is_file())
        self.assertEqual(len(requests), 1)
        request = requests[0]
        revision = git(root, "rev-parse", "HEAD").strip()
        snapshot = source_snapshot(root, revision)
        self.assertEqual(request["gitRevision"], revision)
        self.assertEqual(request["sourceDigest"], snapshot.digest)
        self.assertEqual(request["marketingVersion"], "1.2.3")
        self.assertEqual(request["buildNumber"], "17")
        self.assertEqual(request["adapterDigest"], hashlib.sha256((root / "app" / "scripts" / "release_adapter.py").read_bytes()).hexdigest())
        skeleton = json.loads(readiness.read_text(encoding="utf-8"))
        self.assertEqual(skeleton["candidateManifest"], "app/releases/state/candidates/1.2.3-17/manifest.json")
        self.assertEqual(skeleton["evidence"]["device"]["sha256"], "0" * 64)

    def test_tracked_release_evidence_is_excluded_from_source_snapshot(self) -> None:
        root = self._fixture()
        evidence = root / "app" / "releases" / "evidence"
        evidence.mkdir(parents=True)
        (evidence / "device.json").write_text("{}\n", encoding="utf-8")
        git(root, "add", "app/releases/evidence/device.json")
        git(root, "commit", "-m", "tracked release evidence")

        snapshot = source_snapshot(root, git(root, "rev-parse", "HEAD").strip())

        self.assertIn("app/Quizzler.xcodeproj/project.pbxproj", {path for path, _, _ in snapshot.entries})
        self.assertNotIn("app/releases/evidence/device.json", {path for path, _, _ in snapshot.entries})

    def test_unrelated_root_work_does_not_block_native_candidate(self) -> None:
        root = self._fixture()
        (root / "question-packs").mkdir()
        (root / "question-packs" / "draft.json").write_text("{}", encoding="utf-8")
        requests: list[dict[str, object]] = []
        prepare_candidate(root, freezer=self._freezer(root, requests))
        self.assertEqual(len(requests), 1)

    def test_dirty_or_untracked_app_path_fails_before_freeze(self) -> None:
        root = self._fixture()
        (root / "app" / "QuizzleriOS.swift").write_text("new archive input", encoding="utf-8")
        called: list[bool] = []
        def freezer(*_: object, **__: object) -> Path:
            called.append(True)
            raise AssertionError("must not freeze")
        with self.assertRaisesRegex(CandidateSourceError, "candidate-working-tree-dirty"):
            prepare_candidate(root, freezer=freezer)
        self.assertEqual(called, [])
        self.assertFalse((root / "app" / "releases" / "state" / "current-readiness.json").exists())

    def test_project_reference_outside_app_rejects_the_declared_scope(self) -> None:
        root = self._fixture()
        project = root / "app" / "Quizzler.xcodeproj" / "project.pbxproj"
        project.write_text(PROJECT.replace("objects = {", "objects = {\n\t\tpath = ../question-packs/cissp.json;"), encoding="utf-8")
        git(root, "add", "app/Quizzler.xcodeproj/project.pbxproj")
        git(root, "commit", "-m", "external resource")
        with self.assertRaisesRegex(CandidateSourceError, "candidate-project-external-input"):
            source_snapshot(root, git(root, "rev-parse", "HEAD").strip())

    def test_existing_other_candidate_skeleton_is_not_overwritten(self) -> None:
        root = self._fixture()
        state = root / "app" / "releases" / "state"; state.mkdir(parents=True)
        (state / "current-readiness.json").write_text('{"formatVersion":"2.0.0","candidateManifest":"app/releases/state/candidates/other/manifest.json","evidence":{}}', encoding="utf-8")
        with self.assertRaisesRegex(CandidatePreparationError, "candidate-readiness-identity-drift"):
            prepare_candidate(root, freezer=self._freezer(root, []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
