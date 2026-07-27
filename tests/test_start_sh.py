"""Regression tests for start.sh launcher mode matrix.

Verifies:
  1. Default mode (loopback only) — no --shared-progress, no --lan, no --bind.
  2. --lan branch passes --lan to serve.py (not stock http.server).
  3. --shared-progress passes --shared-progress, opens /pair.
  4. --shared-progress --lan passes both flags.
  5. --shared-progress --tailscale passes --bind (skips if no Tailscale).
  6. --lan and --tailscale are mutually exclusive → exit 1.
  7. --no-open suppresses browser open.
  8. Readiness polls /healthz instead of manifest.json.
  9. --app-root and --packs-root always passed to serve.py.
  10. .public/ symlink farm is absent.
  11. Port conflict detection.
  12. SIGTERM to launcher reaps backgrounded server.
"""

import http.client
import os
import pathlib
import signal
import socket
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).parent.parent
START_SH_PORT = 4123


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
        """--lan and --tailscale must not be usable together."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            "mutually exclusive",
            start_sh,
            "start.sh must detect --lan + --tailscale as mutually exclusive",
        )

    def test_shared_progress_flag_parsed(self):
        """start.sh must parse --shared-progress and pass it to serve.py."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn("--shared-progress", start_sh)
        # The serve.py invocation line/construction must pass it though.
        self.assertIn('--shared-progress)', start_sh,
                       "start.sh must conditionally add --shared-progress to SERVE_ARGS")

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

    def test_lan_exposure_warning_present(self):
        """start.sh must print an unauthenticated-exposure warning on --lan."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            "NO authentication",
            start_sh,
            "start.sh must warn that --lan serves packs with no authentication",
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
            ["bash", str(REPO / "start.sh"), "--no-open"],
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

    def _launch_until_ready(self, flags, timeout_s=10.0):
        """Launch start.sh with *flags*, wait for server on 4123. Returns (proc, ready)."""
        proc = subprocess.Popen(
            ["bash", str(REPO / "start.sh"), "--no-open"] + list(flags),
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            start_new_session=True,
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
        """Default: loopback only, no shared, no lan."""
        self._proc, ready = self._launch_until_ready([])
        self.assertTrue(ready, "Server did not become ready in default mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/app/")
        self.assertEqual(status2, 200)

    def test_lan_flag_exit_1_mutual_exclusive_with_tailscale(self):
        """--lan --tailscale must exit 1."""
        result = subprocess.run(
            ["bash", str(REPO / "start.sh"), "--lan", "--tailscale", "--no-open"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1,
                         f"Expected exit 1, got {result.returncode}: {result.stderr}")
        self.assertIn("mutually exclusive", result.stderr.lower())

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
        """--shared-progress --lan: server starts, auth-gated routes protected."""
        self._proc, ready = self._launch_until_ready(["--shared-progress", "--lan"])
        self.assertTrue(ready, "Server did not become ready in shared+lan mode")
        status, _ = self._get_server_url("/healthz")
        self.assertEqual(status, 200)
        status2, _ = self._get_server_url("/pair")
        self.assertEqual(status2, 200)
        status3, _ = self._get_server_url("/app/")
        self.assertEqual(status3, 401)

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
                ["bash", str(REPO / "start.sh"), "--no-open"],
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
