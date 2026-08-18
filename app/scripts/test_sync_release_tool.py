#!/usr/bin/env python3
"""Offline tests for the hash-bound central runtime synchronization.

Every case builds its own central checkout under a temporary workspace. The
suite used to verify the developer's real ``../apple_developer`` clone, which
made an independently-evolving repository able to fail this one: a commit that
touched none of the vendored files still broke the gate. What this repository
can honestly assert is the mechanism (exact copy, drift detection, path
refusals) plus the integrity of the vendored bytes it actually ships.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_release_tool import (  # noqa: E402
    DEFAULT_AUTHORITY,
    DEFAULT_DESTINATION,
    SyncError,
    load_authority,
    sync_runtime,
    verify_central,
    verify_runtime,
)

CENTRAL_FILES = {
    "release_tools/__init__.py": "",
    "release_tools/iterative_release.py": "def run():\n    return 'release'\n",
    "release_tools/fixtures/composite-success.json": '{"kind": "fixture"}\n',
}
REPORTS = {
    "2026-08-08-quizzler-launchpad-flow-report.html": "<html>launchpad</html>\n",
    "2026-08-08-quizzler-question-shell-report.html": "<html>shell</html>\n",
}


class FixtureWorkspace:
    """A throwaway workspace holding a quizzler tree beside a central checkout."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.central = root / "apple_developer"
        self.authority_path = root / "quizzler" / "app" / "design-authority-manifest.json"
        self.authority_path.parent.mkdir(parents=True)
        for relative, body in {**CENTRAL_FILES, **REPORTS}.items():
            target = self.central / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self.write_authority(self.build_authority())

    def build_authority(self) -> dict:
        return {
            "formatVersion": "2.0.0",
            "centralSource": {
                "path": "../apple_developer",
                "files": [
                    {"path": relative, "sha256": self.digest(relative)}
                    for relative in sorted(CENTRAL_FILES)
                ],
            },
            "designAuthorities": [
                {"path": relative, "sha256": self.digest(relative)} for relative in sorted(REPORTS)
            ],
        }

    def digest(self, relative: str) -> str:
        return hashlib.sha256((self.central / relative).read_bytes()).hexdigest()

    def write_authority(self, document: dict) -> None:
        self.authority_path.write_text(json.dumps(document), encoding="utf-8")


class SyncMechanismTests(unittest.TestCase):
    """Copy, verify, and drift behaviour, proven against a fixture checkout."""

    def workspace(self) -> FixtureWorkspace:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return FixtureWorkspace(Path(temporary.name))

    def test_authority_and_runtime_manifest_contain_only_portable_references(self) -> None:
        space = self.workspace()
        authority = load_authority(space.authority_path)
        self.assertEqual(authority["formatVersion"], "2.0.0")
        self.assertEqual(authority["centralSource"]["path"], "../apple_developer")
        self.assertTrue(all(not Path(entry["path"]).is_absolute() for entry in authority["designAuthorities"]))
        destination = space.root / "runtime"
        manifest = sync_runtime(destination, authority_path=space.authority_path)
        encoded = (destination / "sync-manifest.json").read_text(encoding="utf-8")
        self.assertEqual(manifest["formatVersion"], "2.0.0")
        self.assertEqual(manifest["centralSource"]["path"], "../apple_developer")
        self.assertNotIn(str(space.root), encoded)
        self.assertNotIn("sourcePath", manifest)

    def test_sync_is_exact_and_runtime_tampering_fails(self) -> None:
        space = self.workspace()
        destination = space.root / "runtime"
        first = sync_runtime(destination, authority_path=space.authority_path)
        before = (destination / "sync-manifest.json").read_bytes()
        second = sync_runtime(destination, authority_path=space.authority_path)
        self.assertEqual(first, second)
        self.assertEqual((destination / "sync-manifest.json").read_bytes(), before)
        target = destination / "release_tools" / "iterative_release.py"
        target.write_bytes(target.read_bytes() + b"\n# tampered\n")
        with self.assertRaisesRegex(SyncError, "runtime-file-drift"):
            verify_runtime(destination, space.authority_path)

    def test_central_content_change_fails_without_a_reviewed_hash_update(self) -> None:
        space = self.workspace()
        verify_central(load_authority(space.authority_path), space.authority_path)
        source = space.central / "release_tools" / "iterative_release.py"
        source.write_text("def run():\n    return 'changed'\n", encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "central-source-drift"):
            verify_central(load_authority(space.authority_path), space.authority_path)

    def test_unrelated_central_commits_do_not_fail_verification(self) -> None:
        # The point of dropping the revision pin: files the manifest does not
        # declare may change freely without breaking this repository's gate.
        space = self.workspace()
        (space.central / "release_tools" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
        (space.central / "README.md").write_text("# central\n", encoding="utf-8")
        verify_central(load_authority(space.authority_path), space.authority_path)

    def test_declared_hash_mismatch_fails(self) -> None:
        space = self.workspace()
        authority = space.build_authority()
        authority["centralSource"]["files"][0]["sha256"] = "0" * 64
        space.write_authority(authority)
        with self.assertRaisesRegex(SyncError, "central-source-drift"):
            verify_central(load_authority(space.authority_path), space.authority_path)

    def test_design_authority_report_drift_fails(self) -> None:
        space = self.workspace()
        report = space.central / sorted(REPORTS)[0]
        report.write_text("<html>edited</html>\n", encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "design-authority-drift"):
            verify_central(load_authority(space.authority_path), space.authority_path)

    def test_absolute_and_traversal_authority_references_fail_closed(self) -> None:
        space = self.workspace()
        authority = space.build_authority()
        authority["centralSource"]["path"] = str(Path("/") / "Users" / "example" / "apple_developer")
        space.write_authority(authority)
        with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
            load_authority(space.authority_path)

        authority["centralSource"]["path"] = "../../apple_developer"
        space.write_authority(authority)
        with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
            load_authority(space.authority_path)

        authority["centralSource"]["path"] = "../apple_developer"
        authority["designAuthorities"][0]["path"] = "../outside.html"
        space.write_authority(authority)
        with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
            load_authority(space.authority_path)

    def test_central_source_symlink_fails_closed(self) -> None:
        space = self.workspace()
        authority = space.build_authority()
        authority["centralSource"]["path"] = "apple_developer"
        space.write_authority(authority)
        os.symlink(space.central, space.authority_path.parent.parent / "apple_developer")
        with self.assertRaisesRegex(SyncError, "central-source-drift"):
            verify_central(load_authority(space.authority_path), space.authority_path)

    def test_manifest_and_extra_file_tampering_fail(self) -> None:
        space = self.workspace()
        destination = space.root / "runtime"
        sync_runtime(destination, authority_path=space.authority_path)
        manifest_path = destination / "sync-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sourceRevision"] = "0" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "runtime-manifest-drift"):
            verify_runtime(destination, space.authority_path)
        sync_runtime(destination, authority_path=space.authority_path)
        (destination / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(SyncError, "runtime-file-set-drift"):
            verify_runtime(destination, space.authority_path)


class VendoredRuntimeTests(unittest.TestCase):
    """What this repository ships, verified without reaching outside it."""

    def test_committed_vendored_runtime_matches_its_manifest(self) -> None:
        manifest = verify_runtime(DEFAULT_DESTINATION)
        self.assertEqual(manifest["formatVersion"], "2.0.0")
        self.assertTrue(manifest["centralSource"]["files"])

    def test_authority_declares_no_central_revision(self) -> None:
        # Re-adding a revision pin re-couples this gate to an external clone.
        authority = load_authority(DEFAULT_AUTHORITY)
        self.assertNotIn("revision", authority["centralSource"])
        self.assertNotIn("revision", json.loads(DEFAULT_AUTHORITY.read_text(encoding="utf-8"))["centralSource"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
