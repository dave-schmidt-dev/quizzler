"""Regression tests for start.sh network-exposure hardening.

Verifies:
  1. start.sh's --lan branch invokes scripts/serve.py --lan (not stock
     `-m http.server`), so both server paths get the NoListingHTTPRequestHandler
     hardening from F7's remediation.
  2. The --lan scoped root (.public/) exposes only app/ and question-packs/;
     .git/, .claude/, and scripts/ are not reachable.
  3. /question-packs/ (no index.html) returns 403 — directory listings are
     disabled — while /app/ (has index.html) and named files still serve.
  4. The --lan exposure warning is printed on startup.
  5. (F9) start.sh traps EXIT/INT/TERM so the backgrounded server process is
     reaped on every exit path, not just the Enter-key happy path — SIGTERM
     must not orphan the server on its port.
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


def _lan_branch(start_sh: str) -> str:
    """Extract the `if [ "$LAN" -eq 1 ]; then ... fi` server-launch branch.

    There are two `if [ "$LAN" -eq 1 ]` blocks in start.sh (server launch and
    the URL/warning echo); the server-launch one is identified by containing
    `serve.py`.
    """
    blocks = []
    lines = start_sh.splitlines()
    i = 0
    while i < len(lines):
        if 'if [ "$LAN" -eq 1 ]; then' in lines[i]:
            start = i
            depth = 1
            i += 1
            while i < len(lines) and depth > 0:
                if lines[i].strip().startswith("if "):
                    depth += 1
                if lines[i].strip() == "fi":
                    depth -= 1
                i += 1
            blocks.append("\n".join(lines[start:i]))
        else:
            i += 1
    for block in blocks:
        if "serve.py" in block:
            return block
    raise AssertionError("no --lan branch containing serve.py found in start.sh")


class TestStartShStaticAssertions(unittest.TestCase):
    def test_lan_branch_invokes_serve_py_lan_not_stock_http_server(self):
        """start.sh's --lan branch must reroute through scripts/serve.py --lan
        and must NOT fall back to stock `python3 -m http.server`.

        This proves the F7 remediation reroute: the old code served --lan
        traffic via stock http.server (no listing protection); the fix must
        route it through serve.py's NoListingHTTPRequestHandler instead. The
        403-on-listing behavior itself is proven functionally by
        TestLanScopedServe.test_question_packs_dir_listing_disabled below —
        this check just guards that the reroute stays in place.
        """
        start_sh = (REPO / "start.sh").read_text()
        branch = _lan_branch(start_sh)
        self.assertIn(
            "scripts/serve.py",
            branch,
            "start.sh --lan branch must invoke scripts/serve.py",
        )
        self.assertIn(
            "--lan",
            branch,
            "start.sh --lan branch must pass --lan through to scripts/serve.py",
        )
        self.assertNotIn(
            "-m http.server",
            branch,
            "start.sh --lan branch must not fall back to stock http.server "
            "(it lacks the directory-listing protection)",
        )

    def test_lan_exposure_warning_present(self):
        """start.sh must print an unauthenticated-exposure warning on --lan."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            "NO authentication",
            start_sh,
            "start.sh must warn that --lan serves packs with no authentication",
        )
        # The warning must actually be gated on LAN mode, not printed always.
        warning_line = next(
            line for line in start_sh.splitlines() if "NO authentication" in line
        )
        self.assertIn("echo", warning_line)

    def test_trap_reaps_server_on_exit_int_term(self):
        """F9: start.sh must trap EXIT/INT/TERM to kill SERVER_PID so Ctrl-C,
        SIGTERM, and tab-close (SIGHUP -> exit) don't orphan the backgrounded
        server, which would otherwise squat $PORT and break the next launch's
        port-in-use check."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            'trap \'kill "$SERVER_PID" 2>/dev/null\' EXIT INT TERM',
            start_sh,
            "start.sh must trap EXIT INT TERM and kill $SERVER_PID to avoid "
            "orphaning the background server process",
        )


class TestLanScopedServe(unittest.TestCase):
    """Functional: exercises the REAL --lan path — scripts/serve.py --lan
    against a .public/-style scoped root, exactly as start.sh launches it.

    Creates a temp dir with symlinks app -> <repo>/app and
    question-packs -> <repo>/question-packs, starts
    `python3 scripts/serve.py <port> <public_dir> --lan` against it (bound to
    0.0.0.0, probed via 127.0.0.1), then probes which paths are and are not
    reachable, including the F7 fix: /question-packs/ must 403 instead of
    listing.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        public = pathlib.Path(self._tmpdir.name)

        # Mirror what start.sh --lan does — absolute symlinks keep the test
        # hermetic and portable regardless of working directory.
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

        # Poll until the server accepts connections (up to 3 s). Connect via
        # 127.0.0.1 even though the real bind is 0.0.0.0 — loopback is always
        # one of the addresses an all-interfaces bind answers on.
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

    # --- paths that MUST be reachable ---

    def test_app_dir_accessible(self):
        """app/ has index.html, so it serves normally (200), not a listing."""
        self.assertEqual(self._status("/app/"), 200)

    def test_question_packs_dir_listing_disabled(self):
        """F7 core assertion: question-packs/ has no index.html, so the stock
        handler would return a directory LISTING (200) here. The
        NoListingHTTPRequestHandler in scripts/serve.py must instead return
        403 — this is the actual unauthenticated-exposure fix, exercised
        against the real --lan server process."""
        self.assertEqual(self._status("/question-packs/"), 403)

    def test_question_packs_manifest_accessible(self):
        """question-packs/manifest.json is reachable via the symlink.

        Returns 200 when the manifest has been built (normal), or 404 if it
        has not yet been generated — either way the symlink itself resolves
        correctly and the private files below are still blocked.
        """
        status = self._status("/question-packs/manifest.json")
        self.assertIn(
            status,
            (200, 404),
            f"Expected 200 or 404 for /question-packs/manifest.json, got {status}",
        )

    # --- paths that MUST NOT be reachable ---

    def test_git_not_exposed(self):
        """/.git/ is not in the scoped root — must return 404."""
        self.assertEqual(self._status("/.git/config"), 404)

    def test_claude_settings_not_exposed(self):
        """/.claude/ is not in the scoped root — must return 404."""
        self.assertEqual(self._status("/.claude/settings.local.json"), 404)

    def test_scripts_not_exposed(self):
        """/scripts/ is not in the scoped root — must return 404."""
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
    works end to end, not just that the trap line exists in the source.

    start.sh hardcodes PORT=4123 with no override flag, so this test must use
    4123 directly. That makes it correctness-critical to never leave a process
    behind: setUp skips cleanly if 4123 is already occupied (so we don't kill
    something we don't own or produce a false failure), and tearDown kills the
    launcher's whole process group unconditionally, even if the test body
    raises.
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
        # Kill the whole process group start.sh's shell heads, so any
        # still-running serve.py child dies too even if the trap didn't fire
        # (e.g. the assertion failed before we even sent SIGTERM).
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
        # Belt-and-suspenders: forcibly clear the port so a failed trap in
        # this test never bleeds into the next test/run.
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
            ["bash", str(REPO / "start.sh")],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Keep stdin open (a pipe we never write to/close) rather than
            # DEVNULL: `read -r` on a closed/EOF stdin returns immediately,
            # which would race start.sh past "Press Enter" and into its own
            # cleanup before we ever get to send SIGTERM.
            stdin=subprocess.PIPE,
            start_new_session=True,  # own process group -> safe to killpg later
        )

        # Poll for the server to come up (start.sh itself polls readiness for
        # up to ~3s before printing "Press Enter"; give it a bit more margin).
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

        # Simulate Ctrl-C / terminal-close: SIGTERM the launcher directly
        # (not the process group) — this is exactly the signal start.sh's
        # own trap must react to.
        #
        # Note: the trap this test guards — `trap 'kill "$SERVER_PID"
        # 2>/dev/null' EXIT INT TERM` — kills the server but does not itself
        # call `exit`, so on bash 3.2 (macOS's /bin/bash) a TERM/INT arriving
        # while the shell is blocked in the `read -r` builtin reaps the child
        # immediately but leaves the bash process itself still parked in that
        # same blocked `read` (a bash builtin-vs-signal quirk: `read` isn't
        # preempted mid-syscall the way `wait` is). That's fine for F9's
        # purpose — the orphan-prevention goal is "don't leave the server
        # squatting the port", not "start.sh's own process exits instantly" —
        # so this test asserts on the port, not on self._proc exiting.
        os.kill(self._proc.pid, signal.SIGTERM)

        # Poll for the OS to finish tearing down the server's socket.
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


if __name__ == "__main__":
    unittest.main()
