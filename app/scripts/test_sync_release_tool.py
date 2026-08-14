#!/usr/bin/env python3
"""Offline tests for the hash-bound central runtime synchronization."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_release_tool import (  # noqa: E402
    DEFAULT_AUTHORITY,
    SyncError,
    load_authority,
    sync_runtime,
    verify_central,
    verify_runtime,
)


class SyncReleaseToolTests(unittest.TestCase):
    def test_reviewed_central_source_and_reports_match(self) -> None:
        verify_central(load_authority(DEFAULT_AUTHORITY))

    def test_authority_and_runtime_manifest_contain_only_portable_references(self) -> None:
        authority = load_authority(DEFAULT_AUTHORITY)
        self.assertEqual(authority["formatVersion"], "2.0.0")
        self.assertEqual(authority["centralSource"]["path"], "../apple_developer")
        self.assertTrue(all(not Path(entry["path"]).is_absolute() for entry in authority["designAuthorities"]))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "runtime"
            manifest = sync_runtime(destination)
            encoded = (destination / "sync-manifest.json").read_text(encoding="utf-8")
        self.assertEqual(manifest["formatVersion"], "2.0.0")
        self.assertEqual(manifest["centralSource"]["path"], "../apple_developer")
        self.assertNotIn(str(Path("/") / "Users") + "/", encoded)
        self.assertNotIn("sourcePath", manifest)

    def test_sync_is_exact_and_runtime_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "runtime"
            first = sync_runtime(destination)
            before = (destination / "sync-manifest.json").read_bytes()
            second = sync_runtime(destination)
            self.assertEqual(first, second)
            self.assertEqual((destination / "sync-manifest.json").read_bytes(), before)
            target = destination / "release_tools" / "iterative_release.py"
            target.write_bytes(target.read_bytes() + b"\n# tampered\n")
            with self.assertRaisesRegex(SyncError, "runtime-file-drift"):
                verify_runtime(destination)

    def test_unreviewed_central_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = json.loads(DEFAULT_AUTHORITY.read_text(encoding="utf-8"))
            authority["centralSource"]["files"][0]["sha256"] = "0" * 64
            path = Path(temporary) / "quizzler" / "app" / "authority.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(authority), encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "central-source-drift"):
                verify_central(load_authority(path), path)

    def test_absolute_and_traversal_authority_references_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = json.loads(DEFAULT_AUTHORITY.read_text(encoding="utf-8"))
            authority["centralSource"]["path"] = str(Path("/") / "Users" / "example" / "apple_developer")
            path = Path(temporary) / "authority.json"
            path.write_text(json.dumps(authority), encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
                load_authority(path)

    def test_central_source_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = json.loads(DEFAULT_AUTHORITY.read_text(encoding="utf-8"))
            authority["centralSource"]["path"] = "apple_developer"
            path = Path(temporary) / "quizzler" / "app" / "authority.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(authority), encoding="utf-8")
            source_root = DEFAULT_AUTHORITY.parent.parent.parent / "apple_developer"
            os.symlink(source_root, path.parent.parent / "apple_developer")
            with self.assertRaisesRegex(SyncError, "central-source-drift"):
                verify_central(load_authority(path), path)

            authority["centralSource"]["path"] = "../../apple_developer"
            path.write_text(json.dumps(authority), encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
                load_authority(path)

            authority["centralSource"]["path"] = "../apple_developer"
            authority["designAuthorities"][0]["path"] = "../outside.html"
            path.write_text(json.dumps(authority), encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "authority-manifest-invalid"):
                load_authority(path)

    def test_manifest_and_extra_file_tampering_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "runtime"
            sync_runtime(destination)
            manifest_path = destination / "sync-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sourceRevision"] = "0" * 40
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "runtime-manifest-drift"):
                verify_runtime(destination)
            sync_runtime(destination)
            (destination / "unexpected.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(SyncError, "runtime-file-set-drift"):
                verify_runtime(destination)


if __name__ == "__main__":
    unittest.main(verbosity=2)
