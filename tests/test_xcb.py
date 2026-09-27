"""Hermetic checks for the Xcode wrapper and owned build-path guard."""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "app" / "scripts" / "xcb"
SCANNER = ROOT / "scripts" / "check_build_paths.py"


class XcbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="quizzler-xcb-test-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        (self.fixture / ".quizzler-xcb-fixture").touch()
        self.fake = self.fixture / "fake-xcodebuild"
        self.fake.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "with Path(os.environ['XCB_EVENTS']).open('a') as out:\n"
            "    out.write('start ' + str(time.monotonic()) + ' ' + repr(sys.argv[1:]) + '\\n')\n"
            "time.sleep(float(os.environ.get('XCB_DELAY', '0')))\n"
            "with Path(os.environ['XCB_EVENTS']).open('a') as out:\n"
            "    out.write('end ' + str(time.monotonic()) + '\\n')\n"
            "sys.exit(int(os.environ.get('XCB_EXIT', '0')))\n",
            encoding="utf-8",
        )
        self.fake.chmod(0o755)
        self.events = self.fixture / "events"
        self.env = {
            **os.environ,
            "QUIZZLER_XCB_FIXTURE_ROOT": str(self.fixture),
            "QUIZZLER_XCB_BIN": str(self.fake),
            "XCB_EVENTS": str(self.events),
        }

    def invoke(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WRAPPER), *args],
            env=env or self.env,
            cwd=self.fixture.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_build_uses_fixture_cache_independent_of_cwd(self) -> None:
        result = self.invoke("-project", "Example.xcodeproj", "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.fixture / ".build" / "DerivedData"), result.stderr)
        self.assertIn("'-derivedDataPath'", self.events.read_text())
        self.assertIn(str(self.fixture / ".build" / "DerivedData"), self.events.read_text())

    def test_rejects_both_caller_derived_data_spellings(self) -> None:
        for args in (("-derivedDataPath", "/tmp/leak", "build"), ("-derivedDataPath=/tmp/leak", "build")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("forbidden", result.stderr)
                self.assertFalse(self.events.exists())

    def test_passthrough_modes_do_not_inject_cache_path(self) -> None:
        for mode in ("-version", "-exportArchive"):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("-derivedDataPath", self.events.read_text())
        self.assertFalse((self.fixture / ".build").exists())

    def test_without_building_uses_shared_cache(self) -> None:
        result = self.invoke("test-without-building")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.fixture / ".build" / "DerivedData"), self.events.read_text())

    def test_builds_in_same_fixture_serialize(self) -> None:
        env = {**self.env, "XCB_DELAY": "0.45"}
        first = subprocess.Popen([sys.executable, str(WRAPPER), "build"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.08)
            second = subprocess.Popen([sys.executable, str(WRAPPER), "build"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(first.communicate(timeout=5)[0], "")
                self.assertEqual(second.communicate(timeout=5)[0], "")
                self.assertEqual((first.returncode, second.returncode), (0, 0))
            finally:
                if second.poll() is None:
                    second.kill()
                    second.wait()
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()
        events = [line.split() for line in self.events.read_text().splitlines()]
        self.assertEqual([line[0] for line in events], ["start", "end", "start", "end"])
        self.assertGreaterEqual(float(events[2][1]), float(events[1][1]))

    def test_fixture_override_requires_owned_temporary_marker(self) -> None:
        (self.fixture / ".quizzler-xcb-fixture").unlink()
        result = self.invoke("build")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.events.exists())

    def test_failure_releases_lock_for_next_build(self) -> None:
        failed = self.invoke("build", env={**self.env, "XCB_EXIT": "65"})
        self.assertEqual(failed.returncode, 65, failed.stderr)
        passed = self.invoke("build")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(self.events.read_text().count("start "), 2)

    def test_killed_wrapper_leaves_lock_with_running_build(self) -> None:
        first = subprocess.Popen(
            [sys.executable, str(WRAPPER), "build"],
            env={**self.env, "XCB_DELAY": "1.2"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not self.events.exists():
                time.sleep(0.01)
            self.assertTrue(self.events.exists(), "first build did not start")
            first.kill()
            first.wait(timeout=2)
            second = subprocess.Popen(
                [sys.executable, str(WRAPPER), "build"], env=self.env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                time.sleep(0.15)
                self.assertEqual(self.events.read_text().count("start "), 1)
                self.assertEqual(second.wait(timeout=5), 0)
            finally:
                if second.poll() is None:
                    second.kill()
                    second.wait()
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()
        self.assertEqual([line.split()[0] for line in self.events.read_text().splitlines()],
                         ["start", "end", "start", "end"])

    def test_grouped_release_runner_stops_child_on_timeout(self) -> None:
        sys.path.insert(0, str(ROOT / "app" / "scripts"))
        from xcode_process import run_grouped_xcode

        stopped = self.fixture / "child-stopped"
        child = self.fixture / "child.py"
        child.write_text(
            "import signal, sys, time\n"
            "from pathlib import Path\n"
            "def stop(_signal, _frame):\n"
            "    Path(sys.argv[1]).write_text('stopped')\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "time.sleep(30)\n"
        )
        child_pid = self.fixture / "child-pid"
        parent = self.fixture / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
            "Path(sys.argv[3]).write_text(str(child.pid))\n"
            "time.sleep(30)\n"
        )
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                run_grouped_xcode(
                    [sys.executable, str(parent), str(child), str(stopped), str(child_pid)],
                    cwd=self.fixture, timeout=2, on_progress=lambda: None,
                )
            self.assertTrue(stopped.exists(), "Xcode descendant missed the group signal")
        finally:
            if child_pid.exists():
                try:
                    os.kill(int(child_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_symlinked_cache_is_rejected(self) -> None:
        (self.fixture / ".build").symlink_to(self.fixture / "other")
        result = self.invoke("build")
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlinked .build", result.stderr)

    def test_build_path_guard_rejects_synthetic_legacy_command(self) -> None:
        spec = importlib.util.spec_from_file_location("check_build_paths", SCANNER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        bad = self.fixture / "bad.sh"
        bad.write_text('xcodebuild -derivedDataPath "${TMPDIR:-/tmp}/old-cache" build\n')
        findings = scanner.violations(bad)
        self.assertEqual(len(findings), 2, findings)
        for command in ('xcodebuild "${xcodebuild_args[@]}"', 'xcodebuild \\\n  -project app/Quizzler.xcodeproj'):
            with self.subTest(command=command):
                bad.write_text(command + "\n")
                self.assertEqual(len(scanner.violations(bad)), 1)

    def test_build_path_guard_discovers_new_script_and_doc(self) -> None:
        spec = importlib.util.spec_from_file_location("check_build_paths", SCANNER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        scripts = self.fixture / "scripts"
        docs = self.fixture / "docs"
        scripts.mkdir()
        docs.mkdir()
        (scripts / "new-build.sh").write_text("xcodebuild build\n")
        (docs / "new-build.md").write_text("```sh\nxcodebuild -derivedDataPath /tmp/legacy build\n```\n")
        paths = scanner.active_paths(self.fixture)
        self.assertIn(scripts / "new-build.sh", paths)
        self.assertIn(docs / "new-build.md", paths)
        self.assertEqual(len(scanner.violations(scripts / "new-build.sh")), 1)
        self.assertEqual(len(scanner.violations(docs / "new-build.md")), 2)
        (docs / "inline.md").write_text("`xcodebuild build`\n")
        self.assertEqual(len(scanner.violations(docs / "inline.md")), 1)
        (docs / "prose.md").write_text("Run `xcodebuild build-for-testing` after generation.\n")
        self.assertEqual(len(scanner.violations(docs / "prose.md")), 1)
        app = self.fixture / "app"
        app.mkdir()
        (app / "new-build.sh").write_text("xcodebuild build\n")
        (self.fixture / "new-build.sh").write_text("xcodebuild build\n")
        hooks = self.fixture / ".githooks"
        hooks.mkdir()
        (hooks / "pre-commit").write_text("xcodebuild build\n")
        self.assertIn(app / "new-build.sh", scanner.active_paths(self.fixture))
        self.assertIn(self.fixture / "new-build.sh", scanner.active_paths(self.fixture))
        self.assertIn(hooks / "pre-commit", scanner.active_paths(self.fixture))
        self.assertIn(ROOT / "app" / "test-gate.sh", scanner.active_paths(ROOT))
        self.assertIn(ROOT / "app" / "test-gate-selfcheck.sh", scanner.active_paths(ROOT))
        self.assertEqual(scanner.violations(ROOT / "app" / "test-gate.sh"), [])

    def test_build_path_guard_rejects_fixed_tmp_log_guidance(self) -> None:
        spec = importlib.util.spec_from_file_location("check_build_paths", SCANNER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        scripts = self.fixture / "scripts"
        docs = self.fixture / "docs"
        scripts.mkdir()
        docs.mkdir()
        script = scripts / "new-log.py"
        script.write_text('LOG_FILE = Path("/tmp/quizzler-foo.log")\n')
        doc = docs / "new-log.md"
        doc.write_text('Write the gate log to `/tmp/quizzler-foo.log`.\n')
        self.assertIn(script, scanner.active_paths(self.fixture))
        self.assertIn(doc, scanner.active_paths(self.fixture))
        self.assertEqual(len(scanner.violations(script)), 1)
        self.assertEqual(len(scanner.violations(doc)), 1)
        doc.write_text('Previously the log was at /tmp/quizzler-foo.log.\n')
        self.assertEqual(scanner.violations(doc), [])
        script.write_text('scratch = "${TMPDIR}/quizzler-foo.XXXXXX"\n')
        self.assertEqual(scanner.violations(script), [])


if __name__ == "__main__":
    unittest.main()
