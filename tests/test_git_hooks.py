"""Contract tests for the local hook boundary."""

from __future__ import annotations

import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".githooks"


class GitHookContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (HOOKS / name).read_text(encoding="utf-8")

    def test_hooks_are_executable_and_install_script_selects_canonical_path(self):
        for name in ("pre-commit", "pre-push"):
            mode = (HOOKS / name).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, name)
        install = (ROOT / "scripts/hooks/install.sh").read_text(encoding="utf-8")
        self.assertIn("git config core.hooksPath .githooks", install)

    def test_pre_commit_runs_lint_and_dead_code_only_for_native_changes(self):
        source = self.read("pre-commit")
        self.assertIn("swiftlint lint", source)
        self.assertIn(
            "periphery scan --config app/.periphery.yml --strict --disable-update-check",
            source,
        )
        self.assertIn("git diff --cached", source)
        self.assertIn('[[ "$path" == app/* && "$path" == *.swift ]]', source)
        self.assertNotIn('[[ "$path" == app/*.swift ]]', source)
        self.assertNotIn("npm test", source)
        self.assertNotIn("certification_fresh", source)
        self.assertNotIn("post-tool", source.lower())

    def test_pre_push_runs_both_heavy_gates_and_does_not_reenter_commit_hook(self):
        source = self.read("pre-push")
        self.assertIn("./app/test-gate.sh", source)
        self.assertIn("./app/test-gate.sh --phase native", source)
        self.assertIn("npm test", source)
        self.assertIn("certification_fresh", source)
        self.assertIn("read -r local_ref local_sha remote_ref remote_sha", source)
        self.assertIn("git diff --diff-filter=ACMR --name-only", source)
        self.assertNotIn("mapfile", source)
        self.assertNotIn("pre-commit", source)

    def test_no_post_tool_hook_is_recreated(self):
        for path in (ROOT / ".claude", ROOT / ".codex"):
            if path.exists():
                self.assertFalse(any("hook" in item.name.lower() for item in path.iterdir()))


if __name__ == "__main__":
    unittest.main()
