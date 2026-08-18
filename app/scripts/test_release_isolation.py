#!/usr/bin/env python3
"""INV-5: prove the release adapter from the intended objects, not a worktree.

A developer worktree carries untracked build output, generated release state,
exported evidence, editor scratch files, and an inherited process environment
that a fresh checkout does not. Any of those can make adapter discovery or
runtime provenance look sound when the committed objects alone would not
support it — the release-workflow leg would then certify something a clean
checkout cannot reproduce.

These checks re-run the adapter contract inside a tree materialized from the
git index (tracked objects only, no untracked or unstaged content) with the
release environment scrubbed, and prove the isolated run still refuses a
tampered runtime.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VENDORED_RUNTIME = "app/vendor/apple-release/runtime/release_tools/iterative_release.py"
# The isolated run must never re-enter this module, or the leg would recurse.
ISOLATED_SUITES = ("test_release_adapter", "test_release_readiness")
RAN_RE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)


class ReleaseIsolationTests(unittest.TestCase):
    def materialize(self) -> Path:
        """Check out the git index: exactly the objects a commit would carry."""
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            inside.returncode,
            0,
            "release isolation requires a git work tree; it must not be skipped silently",
        )
        destination = Path(tempfile.mkdtemp(prefix="quizzler-release-isolation."))
        self.addCleanup(shutil.rmtree, destination, ignore_errors=True)
        checkout = subprocess.run(
            ["git", "checkout-index", "-a", f"--prefix={destination}/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(checkout.returncode, 0, checkout.stderr)
        return destination

    def run_isolated(self, tree: Path) -> subprocess.CompletedProcess:
        """Run the adapter suites with no inherited release environment."""
        home = tree / ".isolated-home"
        home.mkdir(exist_ok=True)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "TMPDIR": str(home),
            "LANG": "en_US.UTF-8",
            # Keep bytecode out of the isolated tree so a second run cannot be
            # served stale objects.
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            [sys.executable, "-m", "unittest", *ISOLATED_SUITES],
            cwd=tree / "app" / "scripts",
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_isolated_suites_never_re_enter_this_module(self):
        self.assertNotIn("test_release_isolation", ISOLATED_SUITES)

    def test_isolated_tree_excludes_untracked_developer_state(self):
        tree = self.materialize()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        for relative in untracked:
            self.assertFalse(
                (tree / relative).exists(),
                f"untracked developer file leaked into the isolated tree: {relative}",
            )
        # The tracked runtime the adapter verifies must be present, or the
        # isolated run would prove nothing.
        self.assertTrue((tree / VENDORED_RUNTIME).is_file())

    def test_adapter_contract_holds_on_tracked_objects_alone(self):
        tree = self.materialize()
        result = self.run_isolated(tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        match = RAN_RE.search(result.stderr) or RAN_RE.search(result.stdout)
        self.assertIsNotNone(match, "isolated run emitted no test count")
        self.assertGreater(int(match.group(1)), 0, "isolated run executed zero tests")

    def test_isolated_run_still_refuses_a_tampered_runtime(self):
        tree = self.materialize()
        runtime = tree / VENDORED_RUNTIME
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        result = self.run_isolated(tree)
        self.assertNotEqual(result.returncode, 0, "tampered runtime passed the isolated adapter run")
        self.assertIn("runtime-file-drift", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
