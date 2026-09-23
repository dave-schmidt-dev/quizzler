"""Unit tests for app/scripts/install_mac_app.py.

Hermetic test suite: never builds, never touches /Applications, never runs
lsregister, osascript, codesign, ditto, kill, or xcodebuild, and never launches
an app. The only child processes are short-lived `sys.executable` scripts that
exercise CommandRunner.run_streaming (log streaming, heartbeat, timeout).
"""

from __future__ import annotations

import importlib.util
import io
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

# Load install_mac_app from its file path using importlib
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "app" / "scripts" / "install_mac_app.py"
_spec = importlib.util.spec_from_file_location("install_mac_app", SCRIPT_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load module spec from {SCRIPT_PATH}")
install_mac_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_mac_app)

BUNDLE_ID = install_mac_app.BUNDLE_ID
ICLOUD_CONTAINER = install_mac_app.ICLOUD_CONTAINER


def plant_bundle(
    path: Path,
    identifier: str | None = None,
    version: str = "1.0",
    build: str = "1",
) -> Path:
    """Plant a synthetic app bundle with Info.plist."""
    contents = path / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    if identifier is not None:
        plist_data = {
            "CFBundleIdentifier": identifier,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": build,
        }
        with open(contents / "Info.plist", "wb") as fp:
            plistlib.dump(plist_data, fp)
    return path


class FakeRunner:
    """Fake command runner for hermetic test execution."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.build_returncode: int = 0
        self.build_output: str = "Build succeeded\n** BUILD SUCCEEDED **\n"
        self.verify_product_returncode: int = 0
        self.verify_target_returncode: int = 0
        self.entitlements_returncode: int = 0
        self.entitlements_xml: str = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            '    <key>com.apple.developer.icloud-container-identifiers</key>\n'
            '    <array>\n'
            f'        <string>{ICLOUD_CONTAINER}</string>\n'
            '    </array>\n'
            '</dict>\n'
            '</plist>'
        )
        self.ps_outputs: list[str] = []
        self.default_ps_output: str = ""
        self.lsregister_dump_outputs: list[str] = []
        self.default_lsregister_dump_output: str = ""
        self.git_describe_output: str = "v1.0.0-1-gabcdef-dirty\n"
        self.ditto_callback: Callable[[list[str]], None] | None = None
        self.ditto_returncode: int = 0
        self.xcodebuild_callback: Callable[[list[str]], None] | None = None
        # Raise subprocess.TimeoutExpired for any call this predicate accepts.
        self.timeout_when: Callable[[list[str]], bool] | None = None
        # When set, the streaming build reports one heartbeat at this elapsed time.
        self.heartbeat_at: float | None = None

    def _fake_build(self, argv: list[str], cwd: Path | str | None) -> None:
        if self.xcodebuild_callback:
            self.xcodebuild_callback(argv)
        elif self.build_returncode == 0 and cwd:
            product_dir = (
                Path(cwd)
                / "app"
                / "build"
                / "mac-install"
                / "Build"
                / "Products"
                / "Debug-maccatalyst"
                / "QuizzleriOS.app"
            )
            plant_bundle(product_dir, identifier=BUNDLE_ID, version="2.0.0", build="100")

    def run_streaming(
        self,
        argv: list[str],
        *,
        log_path: Path,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        heartbeat: Callable[[float], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Synchronous stand-in for the streaming build: no process, no waiting."""
        self.commands.append(argv)
        self.timeouts.append(timeout)
        if self.timeout_when is not None and self.timeout_when(argv):
            raise subprocess.TimeoutExpired(argv, timeout or 0)
        if self.heartbeat_at is not None and heartbeat is not None:
            heartbeat(self.heartbeat_at)
        self._fake_build(argv, cwd)
        Path(log_path).write_text(self.build_output, encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode=self.build_returncode)

    def run(
        self,
        argv: list[str],
        *,
        check: bool = False,
        capture_output: bool = True,
        text: bool = True,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        self.timeouts.append(timeout)
        if self.timeout_when is not None and self.timeout_when(argv):
            raise subprocess.TimeoutExpired(argv, timeout or 0)
        tool = argv[0]

        if tool == "xcodebuild":
            self._fake_build(argv, cwd)
            return subprocess.CompletedProcess(
                argv,
                returncode=self.build_returncode,
                stdout=self.build_output,
                stderr="",
            )

        if tool == "codesign":
            if "--entitlements" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    returncode=self.entitlements_returncode,
                    stdout=self.entitlements_xml,
                    stderr="",
                )
            # verify
            target_path = argv[-1]
            if "QuizzleriOS.app" in target_path:
                rc = self.verify_product_returncode
            else:
                rc = self.verify_target_returncode
            return subprocess.CompletedProcess(argv, returncode=rc, stdout="", stderr="")

        if tool == "osascript":
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool == "ps":
            out = self.ps_outputs.pop(0) if self.ps_outputs else self.default_ps_output
            return subprocess.CompletedProcess(argv, returncode=0, stdout=out, stderr="")

        if tool == "ditto":
            if self.ditto_returncode != 0:
                # A failed copy leaves nothing behind in this fake.
                return subprocess.CompletedProcess(
                    argv, returncode=self.ditto_returncode, stdout="", stderr="ditto: failed"
                )
            if self.ditto_callback:
                self.ditto_callback(argv)
            else:
                src = Path(argv[1])
                dst = Path(argv[2])
                if dst.exists():
                    shutil.rmtree(dst)
                if src.is_dir():
                    shutil.copytree(src, dst)
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool.endswith("lsregister"):
            if "-dump" in argv:
                out = (
                    self.lsregister_dump_outputs.pop(0)
                    if self.lsregister_dump_outputs
                    else self.default_lsregister_dump_output
                )
                return subprocess.CompletedProcess(argv, returncode=0, stdout=out, stderr="")
            # -u or -f
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        if tool == "git":
            return subprocess.CompletedProcess(
                argv,
                returncode=0,
                stdout=self.git_describe_output,
                stderr="",
            )

        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")


class TestInstallMacApp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="quizzler-install-test-")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

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

    # 7. install() end-to-end happy path
    def test_install_end_to_end_happy_path(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        product = plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
            version="2.0.0",
            build="100",
        )
        stale_build_app = plant_bundle(
            repo / "app" / "build" / "old" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )

        target_app = dest / "Quizzler.app"
        running_app = plant_bundle(self.tmp_dir / "elsewhere" / "Quizzler.app", identifier=BUNDLE_ID)

        fake_runner = FakeRunner()
        # A copy is running until asked to quit.
        fake_runner.ps_outputs = [f"4242 {running_app}/Contents/MacOS/Quizzler\n"]
        # First dump during sweep: lists stale path and target
        # Second dump after sweep: lists ONLY target
        fake_runner.lsregister_dump_outputs = [
            f"""--------------------------------------------------------------------------------
path:                 /tmp/old-build.app
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------""",
            f"""--------------------------------------------------------------------------------
path:                 {target_app}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------""",
        ]

        stderr_buf = io.StringIO()
        stdout_buf = io.StringIO()

        with redirect_stdout(stdout_buf):
            ret = install_mac_app.install(
                destination=dest,
                repo_root=repo,
                runner=fake_runner,
                skip_build=False,
                sleep_fn=lambda _: None,
                kill_fn=lambda _pid, _sig: None,
                stderr=stderr_buf,
            )

        self.assertEqual(ret, 0)
        self.assertEqual(stdout_buf.getvalue(), "", "stdout must remain strictly empty")

        stderr_lines = [line.strip() for line in stderr_buf.getvalue().splitlines() if line.strip()]
        self.assertTrue(len(stderr_lines) > 0)
        self.assertTrue(
            stderr_lines[-1].startswith("install.complete"),
            f"Final stderr line must start with install.complete, got: {stderr_lines[-1]}",
        )
        self.assertIn(f"path={target_app}", stderr_lines[-1])
        self.assertIn("version=2.0.0", stderr_lines[-1])
        self.assertIn("build=100", stderr_lines[-1])
        self.assertIn("commit=v1.0.0-1-gabcdef-dirty", stderr_lines[-1])

        # Verify call order in fake_runner
        # Order must be: build, verify, quit, ditto, prune, sweep (-u stale, -f target), resolution dump
        cmd_tools = [cmd[0] if not cmd[0].endswith("lsregister") else "lsregister" for cmd in fake_runner.commands]
        self.assertIn("xcodebuild", cmd_tools)
        self.assertIn("codesign", cmd_tools)
        self.assertIn("osascript", cmd_tools)
        self.assertIn("ditto", cmd_tools)
        self.assertIn("lsregister", cmd_tools)

        build_idx = cmd_tools.index("xcodebuild")
        verify_idx = cmd_tools.index("codesign")
        quit_idx = cmd_tools.index("osascript")
        ditto_idx = cmd_tools.index("ditto")
        first_lsregister_idx = cmd_tools.index("lsregister")

        self.assertTrue(
            build_idx < verify_idx < quit_idx < ditto_idx < first_lsregister_idx,
            f"Call order invalid: {cmd_tools}",
        )

        # Verify sweep -u and -f commands
        u_calls = [c for c in fake_runner.commands if "-u" in c]
        f_calls = [c for c in fake_runner.commands if "-f" in c]
        self.assertEqual(len(u_calls), 1)
        self.assertEqual(u_calls[0][-1], "/tmp/old-build.app")
        self.assertEqual(len(f_calls), 1)
        self.assertEqual(f_calls[0][-1], str(target_app))

        # Check that prune removed stale app under repo/app/build
        self.assertFalse(stale_build_app.exists())
        self.assertTrue(product.exists())

        # Check target app was created and has correct Info.plist
        self.assertTrue(target_app.exists())
        self.assertEqual(install_mac_app.bundle_identifier(target_app), BUNDLE_ID)

    # 8. Refusal & error conditions
    def test_refuses_non_writable_destination(self) -> None:
        dest = self.tmp_dir / "nonexistent" / "dir"
        stderr_buf = io.StringIO()
        fake_runner = FakeRunner()

        ret = install_mac_app.install(
            destination=dest,
            repo_root=self.tmp_dir,
            runner=fake_runner,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("install.error destination is not a writable directory", stderr_buf.getvalue())

    def test_refuses_target_with_different_identifier(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        plant_bundle(dest / "Quizzler.app", identifier="com.other.app")

        fake_runner = FakeRunner()
        stderr_buf = io.StringIO()

        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("exists and is not com.zerodelta.quizzler; refusing to replace it", stderr_buf.getvalue())

    def test_refuses_conflicting_bundle_in_destination(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        conflicting = plant_bundle(dest / "Duplicate.app", identifier=BUNDLE_ID)

        fake_runner = FakeRunner()
        stderr_buf = io.StringIO()

        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("already claims com.zerodelta.quizzler:", stderr_buf.getvalue())
        self.assertIn(str(conflicting), stderr_buf.getvalue())
        self.assertIn("move it to the Trash", stderr_buf.getvalue())

    def test_fails_when_post_sweep_dump_lists_foreign_path(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )

        fake_runner = FakeRunner()
        # Post-sweep dump still lists another path
        fake_runner.lsregister_dump_outputs = [
            "",  # initial sweep dump
            f"""--------------------------------------------------------------------------------
path:                 /Library/Stuck.app
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------""",
        ]

        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("does not resolve to", stderr_buf.getvalue())
        self.assertIn("/Library/Stuck.app", stderr_buf.getvalue())

    def test_refuses_when_built_product_signature_fails(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )

        fake_runner = FakeRunner()
        fake_runner.verify_product_returncode = 1

        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("built app failed signature verification", stderr_buf.getvalue())

    def test_refuses_when_built_product_missing_icloud_entitlement(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )

        fake_runner = FakeRunner()
        fake_runner.entitlements_xml = "<plist><dict></dict></plist>"

        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("built app missing iCloud container entitlement", stderr_buf.getvalue())

    def test_fails_when_build_fails(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        fake_runner = FakeRunner()
        fake_runner.build_returncode = 1
        fake_runner.build_output = "error: clang failed with exit code 1\n"

        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=False,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("install.build failed", stderr_buf.getvalue())
        self.assertIn("error: clang failed with exit code 1", stderr_buf.getvalue())
        log_file = repo / "app" / "build" / "mac-install.log"
        self.assertTrue(log_file.is_file())

    def test_refuses_when_running_copy_will_not_exit(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        product = plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        running_app = plant_bundle(self.tmp_dir / "Running.app", identifier=BUNDLE_ID)

        fake_runner = FakeRunner()
        fake_runner.default_ps_output = f"  999 {running_app}/Contents/MacOS/Running\n"

        signals_sent: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            signals_sent.append((pid, sig))

        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=fake_runner,
            skip_build=True,
            sleep_fn=lambda _: None,
            kill_fn=fake_kill,
            stderr=stderr_buf,
        )
        self.assertEqual(ret, 1)
        self.assertIn("a running copy would not exit: 999", stderr_buf.getvalue())
        self.assertIn((999, signal.SIGTERM), signals_sent)
        self.assertIn((999, signal.SIGKILL), signals_sent)

    def test_idempotent_second_run_succeeds(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        target = dest / "Quizzler.app"

        def make_runner() -> FakeRunner:
            r = FakeRunner()
            r.default_lsregister_dump_output = f"""--------------------------------------------------------------------------------
path:                 {target}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------"""
            return r

        # Run 1
        ret1 = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=make_runner(),
            skip_build=True,
            stderr=io.StringIO(),
        )
        self.assertEqual(ret1, 0)
        self.assertTrue(target.exists())

        # Run 2: Nothing changed, target exists with matching identifier
        stderr_buf2 = io.StringIO()
        ret2 = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=make_runner(),
            skip_build=True,
            stderr=stderr_buf2,
        )
        self.assertEqual(ret2, 0)
        self.assertTrue(
            stderr_buf2.getvalue().splitlines()[-1].startswith("install.complete")
        )

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

    def test_does_not_address_the_app_when_no_copy_is_running(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()
        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
        )
        target = dest / "Quizzler.app"
        runner = FakeRunner()
        runner.default_lsregister_dump_output = f"""--------------------------------------------------------------------------------
path:                 {target}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------"""
        ret = install_mac_app.install(
            destination=dest,
            repo_root=repo,
            runner=runner,
            skip_build=True,
            sleep_fn=lambda _: None,
            kill_fn=lambda _pid, _sig: self.fail("nothing should be signalled"),
            stderr=io.StringIO(),
        )
        self.assertEqual(ret, 0)
        self.assertNotIn("osascript", [cmd[0] for cmd in runner.commands])

    # 9. MCI-2: the existing install survives a failed replacement
    def _replace_fixture(self, old_version: str | None = "1.0") -> tuple[Path, Path, Path, FakeRunner]:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()
        plant_bundle(
            repo / "app" / "build" / "mac-install" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
            identifier=BUNDLE_ID,
            version="2.0.0",
            build="100",
        )
        target = dest / "Quizzler.app"
        if old_version is not None:
            plant_bundle(target, identifier=BUNDLE_ID, version=old_version, build="1")
        runner = FakeRunner()
        runner.default_lsregister_dump_output = f"""--------------------------------------------------------------------------------
path:                 {target}
identifier:           {BUNDLE_ID}
--------------------------------------------------------------------------------"""
        return repo, dest, target, runner

    def _install(self, repo: Path, dest: Path, runner: FakeRunner) -> tuple[int, str, str]:
        stderr_buf = io.StringIO()
        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            ret = install_mac_app.install(
                destination=dest,
                repo_root=repo,
                runner=runner,
                skip_build=True,
                sleep_fn=lambda _: None,
                kill_fn=lambda _pid, _sig: None,
                stderr=stderr_buf,
            )
        return ret, stdout_buf.getvalue(), stderr_buf.getvalue()

    @staticmethod
    def _version(app: Path) -> str:
        with open(app / "Contents" / "Info.plist", "rb") as fp:
            return str(plistlib.load(fp)["CFBundleShortVersionString"])

    def test_ditto_failure_leaves_old_target_intact_and_no_staging(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        runner.ditto_returncode = 1
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 1)
        self.assertEqual(out, "")
        self.assertIn("install.error failed to copy", err)
        self.assertEqual(self._version(target), "1.0")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["Quizzler.app"])

    def test_post_copy_signature_failure_leaves_old_target_intact(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        runner.verify_target_returncode = 1
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 1)
        self.assertEqual(out, "")
        self.assertIn("install.error installed app failed signature verification", err)
        self.assertEqual(self._version(target), "1.0")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["Quizzler.app"])
        self.assertFalse(any(c[0].endswith("lsregister") for c in runner.commands))

    def test_post_copy_verify_timeout_rolls_back(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        runner.timeout_when = lambda argv: argv[0] == "codesign" and "QuizzleriOS.app" not in argv[-1]
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 1)
        self.assertEqual(out, "")
        self.assertIn("install.error replace timed out", err)
        self.assertEqual(self._version(target), "1.0")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["Quizzler.app"])

    def test_successful_replace_leaves_no_staging_or_backup(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(self._version(target), "2.0.0")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["Quizzler.app"])
        # The post-copy verification runs against the staged copy, before the swap.
        verifies = [c for c in runner.commands if c[:3] == ["codesign", "--verify", "--strict"]]
        self.assertEqual(len(verifies), 1)
        self.assertNotEqual(verifies[0][-1], str(target))
        self.assertTrue(Path(verifies[0][-1]).parent.name.startswith(".Quizzler.app.installing-"))

    def test_leftover_siblings_from_a_crashed_run_are_not_conflicts_and_are_cleaned(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        leftover_staging = plant_bundle(
            dest / ".Quizzler.app.installing-99999" / "Quizzler.app", identifier=BUNDLE_ID
        )
        leftover_backup = plant_bundle(dest / ".Quizzler.app.previous-99999", identifier=BUNDLE_ID)
        # Without the install-time skip, a staged bundle would read as a second claimant.
        self.assertEqual(
            install_mac_app.conflicting_bundles(dest, BUNDLE_ID, keep=target), [leftover_staging]
        )
        self.assertEqual(
            install_mac_app.conflicting_bundles(
                dest, BUNDLE_ID, keep=target, skip_prefix=".Quizzler.app."
            ),
            [],
        )
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 0, err)
        self.assertEqual(out, "")
        self.assertFalse(leftover_backup.exists())
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["Quizzler.app"])
        self.assertEqual(self._version(target), "2.0.0")

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

    # 11. MCI-3: every command is bounded and the build reports progress
    def test_codesign_timeout_reports_error_and_returns_1(self) -> None:
        repo, dest, target, runner = self._replace_fixture()
        runner.timeout_when = lambda argv: argv[0] == "codesign"
        ret, out, err = self._install(repo, dest, runner)
        self.assertEqual(ret, 1)
        self.assertEqual(out, "")
        self.assertIn("install.error verify timed out", err)
        self.assertNotIn("ditto", [c[0] for c in runner.commands])
        self.assertEqual(self._version(target), "1.0")

    def test_build_timeout_reports_error_and_returns_1(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()
        runner = FakeRunner()
        runner.timeout_when = lambda argv: argv[0] == "xcodebuild"
        stderr_buf = io.StringIO()
        ret = install_mac_app.install(
            destination=dest, repo_root=repo, runner=runner, skip_build=False, stderr=stderr_buf
        )
        self.assertEqual(ret, 1)
        self.assertIn("install.error build timed out", stderr_buf.getvalue())

    def test_every_command_carries_a_timeout(self) -> None:
        repo, dest, _target, runner = self._replace_fixture()
        running_app = plant_bundle(self.tmp_dir / "elsewhere" / "Quizzler.app", identifier=BUNDLE_ID)
        runner.ps_outputs = [f"4242 {running_app}/Contents/MacOS/Quizzler\n"]
        runner.heartbeat_at = 30.0
        stderr_buf = io.StringIO()
        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            ret = install_mac_app.install(
                destination=dest,
                repo_root=repo,
                runner=runner,
                skip_build=False,
                sleep_fn=lambda _: None,
                kill_fn=lambda _pid, _sig: None,
                stderr=stderr_buf,
            )
        self.assertEqual(ret, 0, stderr_buf.getvalue())
        self.assertEqual(stdout_buf.getvalue(), "")
        self.assertIn("install.build running elapsed=30s", stderr_buf.getvalue())
        tools = {c[0] for c in runner.commands}
        self.assertTrue({"xcodebuild", "codesign", "ps", "osascript", "ditto", "git"} <= tools, tools)
        for argv, timeout in zip(runner.commands, runner.timeouts):
            if argv[0] == "xcodebuild":
                expected = 1800
            elif argv[0].endswith("lsregister") and "-dump" in argv:
                expected = 120
            else:
                expected = 60
            self.assertEqual(timeout, expected, argv)
        self.assertIn("-quiet", next(c for c in runner.commands if c[0] == "xcodebuild"))

    def test_command_runner_streams_to_log_with_heartbeat(self) -> None:
        log_path = self.tmp_dir / "logs" / "build.log"
        beats: list[float] = []
        res = install_mac_app.CommandRunner().run_streaming(
            [sys.executable, "-c", "import time; print('hello'); time.sleep(0.4)"],
            log_path=log_path,
            timeout=30,
            heartbeat=beats.append,
            interval=0.1,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("hello", log_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(beats), 1)
        self.assertEqual(beats, sorted(beats))

    def test_command_runner_streaming_times_out(self) -> None:
        log_path = self.tmp_dir / "build.log"
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            install_mac_app.CommandRunner().run_streaming(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                log_path=log_path,
                timeout=0.3,
                heartbeat=lambda _s: None,
                interval=0.1,
            )
        self.assertLess(time.monotonic() - started, 15)

    def test_command_runner_timeout_stops_the_builds_children(self) -> None:
        log_path = self.tmp_dir / "build.log"
        pid_file = self.tmp_dir / "child.pid"
        # The parent starts a sleeping child (as xcodebuild starts compilers) and records its pid.
        script = (
            "import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            f"open({str(pid_file)!r}, 'w').write(str(c.pid)); time.sleep(30)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            install_mac_app.CommandRunner().run_streaming(
                [sys.executable, "-c", script],
                log_path=log_path,
                timeout=1.0,
                heartbeat=lambda _s: None,
                interval=0.1,
            )
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail("the build's child process outlived the timeout")


if __name__ == "__main__":
    unittest.main()
