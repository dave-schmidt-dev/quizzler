"""Registration and bundle resolution tests for install_mac_app."""

from __future__ import annotations

import os
import plistlib
import shutil
import unittest
from pathlib import Path

from tests.install_mac_app_fixtures import (
    BUNDLE_ID,
    FakeRunner,
    InstallMacAppTestCase,
    install_mac_app,
    plant_bundle,
)


class TestInstallMacApp(InstallMacAppTestCase):
    # 1. bundle_identifier tests
    def test_bundle_identifier_reads_planted_plist(self) -> None:
        app = plant_bundle(self.tmp_dir / "App.app", identifier=BUNDLE_ID)
        self.assertEqual(install_mac_app.bundle_identifier(app), BUNDLE_ID)

    def test_bundle_identifier_missing_plist(self) -> None:
        app = plant_bundle(self.tmp_dir / "NoPlist.app", identifier=None)
        self.assertIsNone(install_mac_app.bundle_identifier(app))

    def test_bundle_identifier_corrupted_plist(self) -> None:
        app = self.tmp_dir / "Corrupt.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(b"not a valid plist")
        self.assertIsNone(install_mac_app.bundle_identifier(app))

    def test_bundle_identifier_missing_directory(self) -> None:
        self.assertIsNone(install_mac_app.bundle_identifier(self.tmp_dir / "Absent.app"))

    # 2. conflicting_bundles tests
    def test_conflicting_bundles_clean_destination_reports_no_conflict(self) -> None:
        dest = self.tmp_dir / "Applications"
        dest.mkdir()
        target = plant_bundle(dest / "Quizzler.app", identifier=BUNDLE_ID)
        conflicts = install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target)
        self.assertEqual(conflicts, [])

    def test_conflicting_bundles_reports_second_matching_bundle(self) -> None:
        dest = self.tmp_dir / "Applications"
        dest.mkdir()
        target = plant_bundle(dest / "Quizzler.app", identifier=BUNDLE_ID)
        duplicate = plant_bundle(dest / "OldQuizzler.app", identifier=BUNDLE_ID)
        conflicts = install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target)
        self.assertEqual(conflicts, [duplicate])

    def test_conflicting_bundles_ignores_unrelated_identifier(self) -> None:
        dest = self.tmp_dir / "Applications"
        dest.mkdir()
        target = plant_bundle(dest / "Quizzler.app", identifier=BUNDLE_ID)
        plant_bundle(dest / "OtherApp.app", identifier="com.example.unrelated")
        conflicts = install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target)
        self.assertEqual(conflicts, [])

    def test_conflicting_bundles_finds_nested_directory_app(self) -> None:
        dest = self.tmp_dir / "Applications"
        dest.mkdir()
        target = plant_bundle(dest / "Quizzler.app", identifier=BUNDLE_ID)
        nested_dir = dest / "Utilities"
        nested_dir.mkdir()
        nested_duplicate = plant_bundle(nested_dir / "OldQuizzler.app", identifier=BUNDLE_ID)
        conflicts = install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target)
        self.assertEqual(conflicts, [nested_duplicate])

    def test_conflicting_bundles_does_not_descend_into_found_app(self) -> None:
        dest = self.tmp_dir / "Applications"
        dest.mkdir()
        target = plant_bundle(dest / "Quizzler.app", identifier=BUNDLE_ID)
        helper = plant_bundle(
            target / "Contents" / "Library" / "LoginItems" / "Helper.app",
            identifier=BUNDLE_ID,
        )
        self.assertTrue(helper.exists())
        conflicts = install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target)
        self.assertEqual(conflicts, [])

    def test_conflicting_bundles_missing_directory_returns_empty(self) -> None:
        conflicts = install_mac_app.conflicting_bundles(self.tmp_dir / "nonexistent", BUNDLE_ID)
        self.assertEqual(conflicts, [])

    # 3. parse_lsregister_dump tests
    def test_parse_lsregister_dump_filters_and_strips_suffix(self) -> None:
        dump_fixture = """
--------------------------------------------------------------------------------
bundle id:            101
path:                 /Applications/Quizzler.app (0x123abc)
identifier:           com.zerodelta.quizzler
claim:
path:                 /Applications/Quizzler.app/Contents/Resources/helper (0x456def)
identifier:           com.zerodelta.quizzler.helper
--------------------------------------------------------------------------------
bundle id:            102
path:                 /Applications/Safari.app (0x999999)
identifier:           com.apple.Safari
--------------------------------------------------------------------------------
bundle id:            103
path:                 /Users/dave/build/QuizzleriOS.app
identifier:           com.zerodelta.quizzler
--------------------------------------------------------------------------------
"""
        paths = install_mac_app.parse_lsregister_dump(dump_fixture, BUNDLE_ID)
        expected = [
            "/Applications/Quizzler.app",
            "/Users/dave/build/QuizzleriOS.app",
        ]
        self.assertEqual(paths, expected)

    def test_parse_lsregister_dump_unregistered_id_returns_empty(self) -> None:
        dump_fixture = """
--------------------------------------------------------------------------------
path:                 /Applications/Safari.app (0x111)
identifier:           com.apple.Safari
--------------------------------------------------------------------------------
"""
        paths = install_mac_app.parse_lsregister_dump(dump_fixture, "com.zerodelta.quizzler")
        self.assertEqual(paths, [])

    # 4. prune_build_products tests
    def test_prune_removes_matching_under_app_build_keeps_keep_and_spares_outside(self) -> None:
        repo = self.tmp_dir / "repo"
        build_root = repo / "app" / "build"
        build_root.mkdir(parents=True)

        kept = plant_bundle(
            build_root / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        stale = plant_bundle(
            build_root / "quickcheck" / "Build" / "Products" / "Debug" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        foreign = plant_bundle(
            build_root / "runner" / "Runner.app",
            identifier="com.example.runner",
        )
        outside = plant_bundle(
            repo / "outside" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )

        removed = install_mac_app.prune_build_products(repo, BUNDLE_ID, keep=kept)
        self.assertEqual(removed, [stale])
        self.assertFalse(stale.exists())
        self.assertTrue(kept.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(outside.exists())

    def test_prune_missing_build_root_handled(self) -> None:
        repo = self.tmp_dir / "empty-repo"
        repo.mkdir()
        removed = install_mac_app.prune_build_products(repo, BUNDLE_ID)
        self.assertEqual(removed, [])

    # 5. running_bundle_pids tests
    def test_running_bundle_pids_from_fixture(self) -> None:
        app_match = plant_bundle(self.tmp_dir / "Quizzler.app", identifier=BUNDLE_ID)
        app_other = plant_bundle(self.tmp_dir / "Other.app", identifier="com.example.other")

        ps_fixture = f"""
  101 {app_match}/Contents/MacOS/Quizzler
  102 {app_other}/Contents/MacOS/Other
  103 /usr/bin/python3
  104 {app_match}/Contents/MacOS/Quizzler
"""
        pids = install_mac_app.running_bundle_pids(BUNDLE_ID, ps_fixture)
        self.assertEqual(pids, [101, 104])

        other_pids = install_mac_app.running_bundle_pids("com.example.other", ps_fixture)
        self.assertEqual(other_pids, [102])

        none_pids = install_mac_app.running_bundle_pids("com.nobody", ps_fixture)
        self.assertEqual(none_pids, [])

    # 6. sweep_registrations tests
    def test_sweep_registrations_unregisters_stale_and_registers_keep(self) -> None:
        fake_runner = FakeRunner()
        keep_target = "/Applications/Quizzler.app"
        dump_output = f"""
--------------------------------------------------------------------------------
path:                 /tmp/stale/Quizzler.app
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------
path:                 {keep_target}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------
"""
        fake_runner.default_lsregister_dump_output = dump_output
        install_mac_app.sweep_registrations(fake_runner, BUNDLE_ID, keep=keep_target)

        # Expected calls:
        # 1. lsregister -dump
        # 2. lsregister -u /tmp/stale/Quizzler.app
        # 3. lsregister -f /Applications/Quizzler.app
        self.assertEqual(len(fake_runner.commands), 3)
        self.assertIn("-dump", fake_runner.commands[0])
        self.assertEqual(fake_runner.commands[1], [str(install_mac_app.LSREGISTER), "-u", "/tmp/stale/Quizzler.app"])
        self.assertEqual(fake_runner.commands[2], [str(install_mac_app.LSREGISTER), "-f", keep_target])

    def test_conflicting_bundles_names_a_wrapped_ios_install(self) -> None:
        dest = self.tmp_dir / "dest"
        wrapper = dest / "Quizzler-backup.app"
        inner = wrapper / "Wrapper" / "QuizzleriOS.app"
        inner.mkdir(parents=True)
        with open(inner / "Info.plist", "wb") as fp:
            plistlib.dump({"CFBundleIdentifier": BUNDLE_ID}, fp)
        self.assertEqual(install_mac_app.wrapped_bundle_identifiers(wrapper), [BUNDLE_ID])
        self.assertEqual(
            install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=dest / "Quizzler.app"),
            [wrapper],
        )

    def test_prune_never_touches_an_archive(self) -> None:
        repo = self.tmp_dir / "repo"
        archived = plant_bundle(
            repo / "app" / "build" / "testflight" / "1.0.0-1" / "Quizzler.xcarchive"
            / "Products" / "Applications" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        self.assertEqual(install_mac_app.prune_build_products(repo, BUNDLE_ID), [])
        self.assertTrue(archived.exists())

    # 10. MCI-1: pruning never follows a symlink
    def test_prune_does_not_follow_a_symlink_to_an_outside_bundle(self) -> None:
        repo = self.tmp_dir / "repo"
        build_root = repo / "app" / "build"
        build_root.mkdir(parents=True)
        elsewhere = self.tmp_dir / "elsewhere"
        outside = plant_bundle(elsewhere / "QuizzleriOS.app", identifier=BUNDLE_ID)
        os.symlink(elsewhere, build_root / "link")
        self.assertEqual(install_mac_app.prune_build_products(repo, BUNDLE_ID), [])
        self.assertTrue(outside.exists())

    def test_prune_does_not_reach_an_archive_through_a_symlink(self) -> None:
        repo = self.tmp_dir / "repo"
        build_root = repo / "app" / "build"
        applications = build_root / "testflight" / "1.0.0-1" / "Quizzler.xcarchive" / "Products" / "Applications"
        archived = plant_bundle(applications / "QuizzleriOS.app", identifier=BUNDLE_ID)
        os.symlink(applications, build_root / "shortcut")
        os.symlink(archived, build_root / "Linked.app")
        self.assertEqual(install_mac_app.prune_build_products(repo, BUNDLE_ID), [])
        self.assertTrue(archived.exists())

    def test_symlinked_build_root_prunes_nothing(self) -> None:
        repo, dest, _target, runner = self._replace_fixture(old_version=None)
        real_build = self.tmp_dir / "real-build"
        # Move the product out and point app/build at it, so the build root is a symlink.
        (repo / "app" / "build").mkdir(parents=True)
        shutil.move(str(repo / "app" / "build"), str(real_build))
        os.symlink(real_build, repo / "app" / "build")
        victim = plant_bundle(real_build / "old" / "QuizzleriOS.app", identifier=BUNDLE_ID)
        self.assertEqual(install_mac_app.prune_build_products(repo, BUNDLE_ID), [])
        self.assertTrue(victim.exists())

        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 0, err)
        self.assertEqual(out, "")
        self.assertIn("install.prune skipped reason=", err)
        self.assertTrue(victim.exists())


if __name__ == "__main__":
    unittest.main()
