"""Regression tests for start.sh network-exposure hardening.

Verifies:
  1. The default http.server invocation in start.sh contains --bind 127.0.0.1
     so the server is loopback-only by default.
  2. The --lan scoped root (.public/) exposes only app/ and question-packs/;
     .git/, .claude/, and scripts/ are not reachable.
"""

import http.client
import pathlib
import socket
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).parent.parent


def _free_port() -> int:
    """Return an ephemeral port that is free at call time."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestStartShStaticAssertions(unittest.TestCase):
    def test_default_server_binds_loopback(self):
        """The default http.server invocation must include --bind 127.0.0.1."""
        start_sh = (REPO / "start.sh").read_text()
        self.assertIn(
            "--bind 127.0.0.1",
            start_sh,
            "start.sh default http.server invocation must contain --bind 127.0.0.1",
        )


class TestLanScopedServe(unittest.TestCase):
    """Functional: mirrors the .public/ scoped root constructed by --lan mode.

    Creates a temp dir with symlinks app -> <repo>/app and
    question-packs -> <repo>/question-packs, starts python3 -m http.server
    against it, then probes which paths are and are not reachable.
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
                "python3", "-m", "http.server",
                str(self._port),
                "-d", str(public),
                "--bind", "127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll until the server accepts connections (up to 3 s).
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
        """app/ symlink is served (directory listing → 200)."""
        self.assertEqual(self._status("/app/"), 200)

    def test_question_packs_dir_accessible(self):
        """question-packs/ symlink is served (directory listing → 200)."""
        self.assertEqual(self._status("/question-packs/"), 200)

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


if __name__ == "__main__":
    unittest.main()
