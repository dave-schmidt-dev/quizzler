"""Unit tests for app/scripts/install_mac_app.py.

Hermetic test suite: never builds, never touches /Applications, never runs
lsregister, osascript, codesign, ditto, kill, or xcodebuild, and never launches
an app. The only child processes are short-lived `sys.executable` scripts that
exercise CommandRunner.run_streaming (log streaming, heartbeat, timeout).
"""

from __future__ import annotations

import io
import signal
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.install_mac_app_fixtures import (
    BUNDLE_ID,
    FakeRunner,
    InstallMacAppTestCase,
    install_mac_app,
    plant_bundle,
)


class TestInstallMacApp(InstallMacAppTestCase):
    # 7. install() end-to-end happy path
    def test_install_end_to_end_happy_path(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        product = plant_bundle(
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
        cmd_tools = ["xcb" if cmd[0].endswith("/xcb") else "lsregister" if cmd[0].endswith("lsregister") else cmd[0] for cmd in fake_runner.commands]
        self.assertIn("xcb", cmd_tools)
        self.assertIn("codesign", cmd_tools)
        self.assertIn("osascript", cmd_tools)
        self.assertIn("ditto", cmd_tools)
        self.assertIn("lsregister", cmd_tools)

        build_idx = cmd_tools.index("xcb")
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
        log_file = repo / ".logs" / "mac-install.log"
        self.assertTrue(log_file.is_file())

    def test_refuses_when_running_copy_will_not_exit(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()

        product = plant_bundle(
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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

    def test_does_not_address_the_app_when_no_copy_is_running(self) -> None:
        repo = self.tmp_dir / "repo"
        dest = self.tmp_dir / "dest"
        dest.mkdir()
        plant_bundle(
            repo / ".build" / "DerivedData" / "Build" / "Products" / "Debug-maccatalyst" / "QuizzleriOS.app",
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


if __name__ == "__main__":
    unittest.main()
