#!/usr/bin/env python3
"""Public command contract and aggregate Task 4.4 quick check."""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_release_restart import ReleaseRestartTests  # noqa: F401,E402
from test_release_security import ReleaseSecurityTests  # noqa: F401,E402
from test_testflight_workflow import TestFlightWorkflowTests  # noqa: F401,E402


ROOT = Path(__file__).resolve().parents[2]


class DeployTestFlightCommandTests(unittest.TestCase):
    def test_only_public_command_requires_attended_flag_and_emits_no_secret(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        denied = subprocess.run([str(command)], text=True, capture_output=True, check=False)
        self.assertEqual(denied.returncode, 64)
        self.assertIn("explicit --attended invocation is required", denied.stderr)
        self.assertEqual(denied.stdout, "")
        self.assertIn('"${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', command.read_text(encoding="utf-8"))
        self.assertIn("quizzler-testflight-upload -- --attended", command.read_text(encoding="utf-8"))
        self.assertIn('CENTRAL_ROOT="$ROOT/../apple_developer"', command.read_text(encoding="utf-8"))

    def test_release_status_and_release_testflight_wrap_the_central_cli(self) -> None:
        # Content-only: this module is exercised by the release-workflow gate
        # leg against a tracked-objects-only tree, and the moment a real
        # candidate is ever prepared for quizzler, release-status's output
        # changes. Actual fail-closed execution is verified manually and
        # reported, not baked into this permanent assertion.
        for name in ("release-status", "release-testflight"):
            path = ROOT / "app" / name
            source = path.read_text(encoding="utf-8")
            self.assertIn("release_tools", source)
            self.assertIn('CENTRAL_ROOT="$ROOT/../apple_developer"', source)
            self.assertIn('--adapter "$ADAPTER"', source)

    def test_unmarked_entry_uses_only_the_fixed_bws_consumer(self) -> None:
        command = ROOT / "app" / "deploy-testflight"
        source = command.read_text(encoding="utf-8")
        self.assertIn('exec /opt/homebrew/bin/python3 "${HOME:?}/Documents/Projects/bws/bws-secret-exec.py"', source)
        self.assertIn('quizzler-testflight-upload -- --attended', source)
        self.assertNotIn('exec bws-secret-exec ', source)

    def test_marked_entry_wraps_the_central_release_tools_cli(self) -> None:
        source = (ROOT / "app" / "deploy-testflight").read_text(encoding="utf-8")
        self.assertIn('QUIZZLER_TESTFLIGHT_BWS_CONSUMER', source)
        self.assertIn('PYTHONPATH="$CENTRAL_ROOT" /opt/homebrew/bin/python3 -m release_tools testflight', source)
        self.assertIn('--adapter "$ADAPTER" --repository "$ROOT"', source)
        self.assertNotIn("testflight_workflow.py", source)


RELEASE_TESTFLIGHT_CANDIDATES = (
    "for candidate_interpreter in /opt/homebrew/bin/python3 /usr/local/bin/python3; do"
)
HOMEBREW_PYTHON = Path("/opt/homebrew/bin/python3")


class ReleaseTestFlightInterpreterTests(unittest.TestCase):
    """app/release-testflight pinned Xcode's 3.9 /usr/bin/python3, below release_tools' 3.11 floor."""

    def _run_copied_wrapper(
        self, candidates: tuple[Path, ...] | None, central_main: str
    ) -> subprocess.CompletedProcess[str]:
        """Run a copy of the wrapper against a stand-in framework in a temporary checkout."""
        source = (ROOT / "app" / "release-testflight").read_text(encoding="utf-8")
        self.assertIn(RELEASE_TESTFLIGHT_CANDIDATES, source)
        if candidates is not None:
            source = source.replace(
                RELEASE_TESTFLIGHT_CANDIDATES,
                "for candidate_interpreter in " + " ".join(shlex.quote(str(path)) for path in candidates) + "; do",
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "quizzler"
            (project / "app").mkdir(parents=True)
            (project / ".release").mkdir()
            (project / ".release" / "release-adapter.json").write_text("{}", encoding="utf-8")
            wrapper = project / "app" / "release-testflight"
            wrapper.write_text(source, encoding="utf-8")
            wrapper.chmod(0o755)
            subprocess.run(("git", "-C", str(project), "init", "-q"), check=True)
            central = root / "apple_developer" / "release_tools"
            central.mkdir(parents=True)
            (central / "__init__.py").write_text("", encoding="utf-8")
            (central / "__main__.py").write_text(central_main, encoding="utf-8")
            # Hooks and launchd run with a minimal PATH; the wrapper must not depend on it.
            # The temporary cwd keeps a regression from ever reaching the real framework.
            environment = {"PATH": "/usr/bin:/bin", "HOME": temporary}
            return subprocess.run(
                (str(wrapper),), capture_output=True, text=True, check=False, env=environment, cwd=project
            )

    def _advertised_python(self, path: Path, version: tuple[int, int]) -> None:
        """Create a stand-in that answers the wrapper's floor probe for one version."""
        major, minor = version
        path.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "-c" ]]; then\n'
            '  [[ "${2:-}" =~ \\(([0-9]+),\\ ([0-9]+)\\) ]] || exit 98\n'
            "  required_major=\"${BASH_REMATCH[1]}\"\n"
            "  required_minor=\"${BASH_REMATCH[2]}\"\n"
            f"  if (( {major} > required_major || ({major} == required_major && {minor} >= required_minor) )); then exit 0; fi\n"
            "  exit 1\n"
            "fi\n"
            f"printf 'selected-{major}.{minor}\\n' >&2\n"
            f'exec {shlex.quote(sys.executable)} "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    def test_wrapper_never_executes_the_system_python(self) -> None:
        source = (ROOT / "app" / "release-testflight").read_text(encoding="utf-8")
        code = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
        self.assertFalse([line for line in code if "/usr/bin/python3" in line])
        self.assertIn(RELEASE_TESTFLIGHT_CANDIDATES, source)
        self.assertIn("PYTHON_MINIMUM_MAJOR=3\nPYTHON_MINIMUM_MINOR=11\n", source)
        self.assertIn(
            'exec env PYTHONSAFEPATH=1 PYTHONPATH="$CENTRAL_ROOT" "$PYTHON_BIN" -m release_tools testflight',
            source,
        )

    @unittest.skipUnless(HOMEBREW_PYTHON.is_file(), "Homebrew python3 is not installed")
    def test_wrapper_runs_release_tools_on_homebrew_python_above_the_floor(self) -> None:
        result = self._run_copied_wrapper(
            None,
            "import sys\nprint(sys.version_info[0], sys.version_info[1], sys.prefix, sys.argv[1])\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        major, minor, prefix, command = result.stdout.split()
        self.assertGreaterEqual((int(major), int(minor)), (3, 11))
        self.assertEqual(Path(prefix).resolve(), Path(
            subprocess.run(
                (str(HOMEBREW_PYTHON), "-c", "import sys; print(sys.prefix)"),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        ).resolve())
        self.assertEqual(command, "testflight")

    def test_wrapper_rejects_an_interpreter_below_the_floor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            below_floor = Path(temporary) / "python3"
            self._advertised_python(below_floor, (3, 9))
            result = self._run_copied_wrapper((below_floor,), "raise SystemExit(99)\n")
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("no python3 >= 3.11 is available", result.stderr)
        self.assertNotIn("selected-3.9", result.stderr)

    def test_wrapper_selects_the_first_supported_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            below_floor = Path(temporary) / "python3.9"
            supported = Path(temporary) / "python3.11"
            self._advertised_python(below_floor, (3, 9))
            self._advertised_python(supported, (3, 11))
            result = self._run_copied_wrapper((below_floor, supported), "print('testflight-ok')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "testflight-ok\n")
        self.assertIn("selected-3.11", result.stderr)
        self.assertNotIn("selected-3.9", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
