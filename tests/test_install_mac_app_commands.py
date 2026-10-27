"""Command execution and timeout tests for install_mac_app."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
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
        runner.timeout_when = lambda argv: argv[0].endswith("/xcb")
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
        tools = {"xcb" if c[0].endswith("/xcb") else c[0] for c in runner.commands}
        self.assertTrue({"xcb", "codesign", "ps", "osascript", "ditto", "git"} <= tools, tools)
        for argv, timeout in zip(runner.commands, runner.timeouts):
            if argv[0].endswith("/xcb"):
                expected = 1800
            elif argv[0].endswith("lsregister") and "-dump" in argv:
                expected = 120
            else:
                expected = 60
            self.assertEqual(timeout, expected, argv)
        self.assertIn("-quiet", next(c for c in runner.commands if c[0].endswith("/xcb")))

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
