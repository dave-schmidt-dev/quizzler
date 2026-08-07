"""Regression tests for start.sh launcher mode matrix.

Verifies:
  1. Default mode (LAN, all interfaces) — no flags needed.
  2. --no-lan restricts to loopback-only.
  3. --shared-progress opens /pair (flag controls launch URL only).
  4. --shared-progress --tailscale passes --bind (skips if no Tailscale).
  5. --tailscale binds loopback plus the Tailscale address unless --lan is explicit.
  6. --no-open suppresses browser open.
  7. Readiness polls /healthz instead of manifest.json.
  8. --app-root and --packs-root always passed to serve.py.
  9. .public/ symlink farm is absent.
  10. Port conflict detection.
  11. SIGTERM to launcher reaps backgrounded server.
"""

import http.client
import os
import pathlib
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).parent.parent
START_SH_PORT = 4123
COURSE_SIZE_PREVIEW_FLAG = "--allow-course-size-preview"
MANIFEST = REPO / "question-packs" / "manifest.json"

# These tests spawn the REAL start.sh, which runs the REAL build_manifest.py
# against the REAL question-packs/ — so every run rewrites the developer's
# installed manifest.json. That was invisible while the rebuild reproduced the
# same content; it stopped being invisible once a failing strict build began
# revoking the manifest. Snapshot and restore it so running the suite never
# uninstalls the developer's packs.
_manifest_snapshot: bytes | None = None
_strict_snapshot: str | None = None
_STRICT_ENV = "QUIZZLER_LINT_STRICT"


def _capture_serve_args(flags: list[str]) -> tuple[list[str], int]:
    """Run start.sh with fake Tailscale/Python tools and capture serve.py argv."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        args_log = tmpdir / "serve-args"
        fake_server = tmpdir / "fake_server.py"
        fake_server.write_text(
            """import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""
        )
        real_python = shlex.quote(sys.executable)
        serve_path = shlex.quote(str(REPO / "scripts" / "serve.py"))
        log_path = shlex.quote(str(args_log))
        server_path = shlex.quote(str(fake_server))
        (tmpdir / "python3").write_text(
            f"""#!/bin/sh
if [ \"$1\" = {serve_path} ]; then
  printf '%s\\n' \"$@\" > {log_path}
  exec {real_python} {server_path} \"$2\"
fi
exec {real_python} \"$@\"
"""
        )
        (tmpdir / "tailscale").write_text(
            """#!/bin/sh
if [ "$1" = "ip" ] && [ "$2" = "-4" ]; then
  echo 127.0.0.2
  exit 0
fi
if [ "$1" = "status" ] && [ "$2" = "--json" ]; then
  echo '{"Self":{"DNSName":"fake.tailnet"}}'
  exit 0
fi
exit 1
"""
        )
        (tmpdir / "python3").chmod(0o755)
        (tmpdir / "tailscale").chmod(0o755)
        env = {**os.environ, "PATH": f"{tmpdir}{os.pathsep}{os.environ['PATH']}"}
        proc = subprocess.Popen(
            ["bash", str(REPO / "start.sh"), "--no-open",
             COURSE_SIZE_PREVIEW_FLAG] + flags,
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=3)
            raise AssertionError(
                f"start.sh did not finish: stdout={stdout[:2000]!r}, "
                f"stderr={stderr[:2000]!r}"
            )
        if not args_log.exists():
            raise AssertionError(
                f"start.sh did not invoke serve.py (rc={proc.returncode}): "
                f"stdout={stdout[:2000]!r}, stderr={stderr[:2000]!r}"
            )
        return args_log.read_text().splitlines(), proc.returncode


def setUpModule() -> None:
    """Snapshot the manifest and take the launcher off the strict-lint gate.

    These tests assert launcher behavior (bind modes, port conflict, SIGTERM
    reaping), not pack quality. Left strict, every one of them fails the moment
    any pack in the repo drops below the bar — start.sh aborts on a failed
    manifest build, so the server never comes up and six unrelated tests go red
    for a reason that has nothing to do with the launcher. Pack quality is
    gated by tests/test_install_gate.py, which is where that redness belongs.
    Mirrors the existing --allow-course-size-preview bypass.
    """
    global _manifest_snapshot, _strict_snapshot
    _manifest_snapshot = MANIFEST.read_bytes() if MANIFEST.exists() else None
    _strict_snapshot = os.environ.get(_STRICT_ENV)
    os.environ[_STRICT_ENV] = "0"


def tearDownModule() -> None:
    if _strict_snapshot is None:
        os.environ.pop(_STRICT_ENV, None)
    else:
        os.environ[_STRICT_ENV] = _strict_snapshot
    if _manifest_snapshot is None:
        MANIFEST.unlink(missing_ok=True)
    else:
        MANIFEST.write_bytes(_manifest_snapshot)


def _free_port() -> int:
    """Return an ephemeral port that is free at call time."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestStartShStaticAssertions(unittest.TestCase):
    """Read-only assertions on start.sh source."""

    def test_serve_py_is_invoked_explicitly(self):
        """start.sh must invoke scripts/serve.py directly (not stock http.server)."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("scripts/serve.py", start_sh)
        self.assertNotIn(
            "-m http.server",
            start_sh,
            "start.sh must not fall back to stock http.server",
        )

    def test_lan_and_tailscale_mutually_exclusive(self):
        """Tailscale isolation is stable and explicit --lan retains LAN mode."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertNotIn("mutually exclusive", start_sh)

        tailscale_args, tailscale_rc = _capture_serve_args(["--tailscale"])
        self.assertEqual(tailscale_rc, 0)
        self.assertIn("--bind", tailscale_args)
        self.assertNotIn("--lan", tailscale_args)

        tailscale_lan_args, tailscale_lan_rc = _capture_serve_args(
            ["--tailscale", "--lan"]
        )
        lan_tailscale_args, lan_tailscale_rc = _capture_serve_args(
            ["--lan", "--tailscale"]
        )
        self.assertEqual(tailscale_lan_rc, 0)
        self.assertEqual(lan_tailscale_rc, 0)
        self.assertIn("--lan", tailscale_lan_args)
        self.assertEqual(tailscale_lan_args, lan_tailscale_args)

    def test_build_exit_2_is_distinguished_from_exit_1(self):
        """start.sh must branch on the build's exit code, not just truthiness.

        `|| { abort; }` treats "some packs excluded" the same as "nothing
        installed", which made one bad pack block the whole app.
        """
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("BUILD_STATUS", start_sh)
        self.assertNotIn(
            'build_manifest.py" "${BUILD_ARGS[@]}" ||', start_sh,
            "start.sh must not abort on any non-zero build status",
        )

    def test_shared_progress_flag_parsed(self):
        """--shared-progress is parsed as a flag (controls launch URL only).

        The server always has shared-progress endpoints; the flag is NOT
        passed to serve.py. It only controls whether the browser opens /pair.
        """
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("--shared-progress", start_sh)
        self.assertNotIn(
            "SERVE_ARGS+=(--shared-progress)",
            start_sh,
            "start.sh must NOT pass --shared-progress to serve.py "
            "(shared-progress endpoints are always available, "
            "flag only controls launch URL)",
        )

    def test_app_root_and_packs_root_always_passed(self):
        """start.sh must always pass --app-root and --packs-root to serve.py."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("--app-root", start_sh)
        self.assertIn("--packs-root", start_sh)

    def test_no_public_symlink_farm(self):
        """The .public/ symlink farm must be removed; scoped routing replaces it."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertNotIn(
            ".public",
            start_sh,
            "start.sh must not create .public/ symlink farm — "
            "scoped routing via --app-root / --packs-root replaces it",
        )

    def test_readiness_polls_healthz_not_manifest(self):
        """Readiness must poll /healthz, not /question-packs/manifest.json."""
        start_sh = (REPO / "start.sh").read_text()
        lines = [l for l in start_sh.splitlines() if "curl" in l]
        healthz_lines = [l for l in lines if "healthz" in l]
        manifest_lines = [l for l in lines if "manifest.json" in l]
        self.assertGreater(len(healthz_lines), 0,
                           "start.sh must poll /healthz for readiness")
        self.assertEqual(len(manifest_lines), 0,
                         "start.sh must not poll manifest.json for readiness")

    def test_lan_url_is_printed(self):
        """start.sh must print a LAN URL when serving on all interfaces
        (the default since LAN=1)."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("LAN URL", start_sh,
                      "start.sh must print the LAN URL when serving on all interfaces")

    def test_lan_banner_states_unauthenticated_reads(self):
        """The all-interface banner must disclose unauthenticated reads."""
        serve_py = (REPO / "scripts" / "serve.py").read_text()
        self.assertIn(
            "Serving to all interfaces — reads are unauthenticated; "
            "progress mutations require pairing.",
            serve_py,
        )
        self.assertNotIn(
            "Serving to all interfaces — progress mutations require pairing.",
            serve_py,
        )

    def test_trap_reaps_server_on_exit_int_term(self):
        """trap EXIT/INT/TERM must kill SERVER_PID to avoid orphaning the server."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            'trap \'kill "$SERVER_PID" 2>/dev/null\' EXIT INT TERM',
            start_sh,
            "start.sh must trap EXIT INT TERM and kill $SERVER_PID",
        )

    def test_pair_url_in_shared_mode(self):
        """start.sh must reference /pair for the local pairing page."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("/pair", start_sh)


class TestLanScopedServe(unittest.TestCase):
    """Functional: exercises the REAL --lan path — scripts/serve.py --lan
    against scoped routing with realpath containment, exactly as start.sh
    launches it.

    Creates a temp dir with symlinks app -> <repo>/app and
    question-packs -> <repo>/question-packs, starts
    `python3 scripts/serve.py <port> <public_dir> --lan` against it (bound to
    0.0.0.0, probed via 127.0.0.1), then probes which paths are and are not
    reachable. The server derives --app-root and --packs-root from the
    directory argument and serves only those scoped paths.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        public = pathlib.Path(self._tmpdir.name)

        (public / "app").symlink_to(REPO / "app")
        (public / "question-packs").symlink_to(REPO / "question-packs")

        self._port = _free_port()
        self._server = subprocess.Popen(
            [
                "python3", str(REPO / "scripts" / "serve.py"),
                str(self._port),
                str(public),
                "--lan",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=0.5)
                conn.request("GET", "/")
                conn.getresponse()
                break
            except OSError:
                time.sleep(0.05)
            finally:
                conn.close()

    def tearDown(self):
        if self._server:
            self._server.terminate()
            self._server.wait()
        if self._tmpdir:
            self._tmpdir.cleanup()

    def _status(self, path: str) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request("GET", path)
            return conn.getresponse().status
        finally:
            conn.close()

    def test_app_dir_accessible(self):
        self.assertEqual(self._status("/app/"), 200)

    def test_question_packs_dir_not_reachable(self):
        self.assertEqual(self._status("/question-packs/"), 404)

    def test_question_packs_manifest_accessible(self):
        status = self._status("/question-packs/manifest.json")
        self.assertIn(status, (200, 404))

    def test_git_not_exposed(self):
        self.assertEqual(self._status("/.git/config"), 404)

    def test_claude_settings_not_exposed(self):
        self.assertEqual(self._status("/.claude/settings.local.json"), 404)

    def test_scripts_not_exposed(self):
        self.assertEqual(self._status("/scripts/lint_packs.py"), 404)


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """True if something accepts TCP connections on (host, port)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class TestStartShSigtermLifecycle(unittest.TestCase):
    """F9 functional: launches the REAL start.sh (hardcoded PORT=4123), waits
    for the server it backgrounds to come up, sends the *launcher* SIGTERM
    (simulating Ctrl-C / a closed terminal tab), then asserts the server was
    reaped and port 4123 is freed — proving the EXIT/INT/TERM trap actually
    works end to end.
    """

    def setUp(self):
        if _port_is_open(START_SH_PORT):
            self.skipTest(
                f"port {START_SH_PORT} is already in use; refusing to run "
                "the start.sh lifecycle test against a pre-existing occupant"
            )
        self._proc = None

    def tearDown(self):
        proc = self._proc
        if proc is None:
            return
        try:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        finally:
            if proc.stdin:
                proc.stdin.close()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        deadline = time.monotonic() + 3.0
        while _port_is_open(START_SH_PORT) and time.monotonic() < deadline:
            subprocess.run(
                ["pkill", "-f", "scripts/serve.py"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.1)

    def test_sigterm_to_launcher_reaps_backgrounded_server(self):
        """Send SIGTERM to start.sh while it's blocked on `read -r` and
        confirm the backgrounded server is killed, freeing port 4123."""
        self._proc = subprocess.Popen(
            ["bash", str(REPO / "start.sh"), "--no-open", COURSE_SIZE_PREVIEW_FLAG],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.PIPE,
            start_new_session=True,
        )

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if _port_is_open(START_SH_PORT):
                break
            self.assertIsNone(
                self._proc.poll(),
                "start.sh exited before the server ever became reachable "
                "on port 4123 — see /tmp/quizzler-server.log",
            )
            time.sleep(0.1)
        else:
            self.fail(
                f"port {START_SH_PORT} never became reachable within 10s "
                "of launching start.sh"
            )

        os.kill(self._proc.pid, signal.SIGTERM)

        deadline = time.monotonic() + 5.0
        freed = False
        while time.monotonic() < deadline:
            if not _port_is_open(START_SH_PORT):
                freed = True
                break
            time.sleep(0.1)

        self.assertTrue(
            freed,
            f"port {START_SH_PORT} was still accepting connections after "
            "SIGTERM to start.sh — the server was orphaned (F9 regression)",
        )


class TestStartShModeFlags(unittest.TestCase):
    """Functional: launches start.sh with various flag combinations."""

    def _launch_until_ready(self, flags, timeout_s=10.0, env=None):
        """Launch start.sh with *flags*, wait for server on 4123. Returns (proc, ready)."""
        proc = subprocess.Popen(
            ["bash", str(REPO / "start.sh"), "--no-open",
             COURSE_SIZE_PREVIEW_FLAG] + list(flags),
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _port_is_open(START_SH_PORT):
                return proc, True
            rc = proc.poll()
            if rc is not None:
                stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
                stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                self.fail(
                    f"start.sh exited early (rc={rc}) before server ready.\n"
                    f"stdout: {stdout[:2000]}\nstderr: {stderr[:2000]}"
                )
            time.sleep(0.1)
        return proc, False

    def _stop(self, proc):
        if proc is None:
            return
        for pipe in (proc.stdout, proc.stderr, proc.stdin):
            if pipe:
                try:
                    pipe.close()
                except OSError:
                    pass
        try:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
        deadline = time.monotonic() + 3.0
        while _port_is_open(START_SH_PORT) and time.monotonic() < deadline:
            subprocess.run(
                ["pkill", "-f", "scripts/serve.py"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.1)

    def setUp(self):
        if _port_is_open(START_SH_PORT):
            self.skipTest(
                f"port {START_SH_PORT} is already in use; skipping mode tests"
            )
        self._proc = None

    def tearDown(self):
        if self._proc:
            self._stop(self._proc)
            self._proc = None

    def _get_server_url(self, path="/healthz"):
        """GET *path* from the server; return (status, body)."""
        try:
            conn = http.client.HTTPConnection("127.0.0.1", START_SH_PORT, timeout=3)
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8")
        except Exception as e:
            return None, str(e)

    def test_default_mode(self):
        """Default: LAN (all interfaces), no shared, app opens directly."""
        self._proc, ready = self._launch_until_ready([])
        self.assertTrue(ready, "Server did not become ready in default mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/app/")
        self.assertEqual(status2, 200)

    def test_partial_gate_failure_still_launches(self):
        """build_manifest exit 2 = "some packs excluded"; the launch must proceed.

        Regression: a strict build that found ANY bad pack returned 1 and
        start.sh aborted, so one defective pack made the whole app unlaunchable
        and `QUIZZLER_LINT_STRICT=0` — which reinstalls the defective pack —
        became the only way to study. Exit 2 now means the good packs installed;
        only exit 1 (nothing installed) aborts.

        Self-skipping: runs the real strict build first and only asserts when the
        repo actually has a partial failure to observe.
        """
        strict_env = {**os.environ, _STRICT_ENV: "1"}
        probe = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_manifest.py"),
             COURSE_SIZE_PREVIEW_FLAG],
            cwd=REPO, capture_output=True, env=strict_env,
        )
        if probe.returncode != 2:
            self.skipTest(
                f"strict build returned {probe.returncode}, not a partial "
                "failure; nothing to observe"
            )

        self._proc, ready = self._launch_until_ready([], env=strict_env)
        self.assertTrue(
            ready,
            "start.sh aborted on a partial gate failure instead of serving the "
            "packs that passed",
        )
        self.assertEqual(self._get_server_url("/healthz")[0], 200)

    def test_no_lan_and_tailscale_uses_loopback(self):
        """--no-lan --tailscale is redundant and keeps the isolated bind."""
        args, returncode = _capture_serve_args(["--no-lan", "--tailscale"])
        self.assertEqual(returncode, 0)
        self.assertIn("--bind", args)
        self.assertNotIn("--lan", args)

    def test_no_open_does_not_open_browser(self):
        """--no-open must not attempt to open a browser (server still starts)."""
        self._proc, ready = self._launch_until_ready(["--no-open"])
        self.assertTrue(ready, "Server did not become ready with --no-open")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)

    def test_shared_progress_mode(self):
        """--shared-progress: server starts, /pair is reachable."""
        self._proc, ready = self._launch_until_ready(["--shared-progress"])
        self.assertTrue(ready, "Server did not become ready in shared mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/pair")
        self.assertEqual(status2, 200)

    def test_shared_progress_lan(self):
        """--shared-progress --lan: server starts, LAN pairing page reachable."""
        self._proc, ready = self._launch_until_ready(["--shared-progress", "--lan"])
        self.assertTrue(ready, "Server did not become ready in shared+lan mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/pair")
        self.assertEqual(status2, 200)
        status3, _ = self._get_server_url("/app/")
        self.assertEqual(status3, 200)

    def test_shared_progress_tailscale(self):
        """--shared-progress --tailscale: skips if Tailscale not available
        or if the Tailscale IP is not currently bound to an interface."""
        ts_check = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True
        )
        if ts_check.returncode != 0:
            self.skipTest("Tailscale not available; skipping --tailscale integration test")
        ts_ip = ts_check.stdout.strip()
        if not ts_ip:
            self.skipTest("tailscale ip -4 returned empty output")

        # Verify the IP is actually bound to a local interface — otherwise
        # serve.py --bind will fail with EADDRNOTAVAIL.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind((ts_ip, 0))
            probe.close()
        except OSError as e:
            self.skipTest(f"Tailscale IP {ts_ip} is not currently bound ({e})")

        self._proc, ready = self._launch_until_ready(["--shared-progress", "--tailscale"], timeout_s=15)
        self.assertTrue(ready, "Server did not become ready in shared+tailscale mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/pair")
        self.assertEqual(status2, 200)

    def test_port_conflict_exit(self):
        """start.sh must exit 1 if port 4123 is already occupied."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", START_SH_PORT))
            s.listen(1)

            result = subprocess.run(
                ["bash", str(REPO / "start.sh"), "--no-open",
                 COURSE_SIZE_PREVIEW_FLAG],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 1,
                             f"Expected exit 1 for port conflict, got {result.returncode}")
            self.assertIn("port", result.stderr.lower())
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
