"""Contract and behavior tests for the local hook boundary.

The contract tests pin the hook shape. The behavior tests below prove the
property that shape exists for: a hook validates the git object set it gates
(the index for a commit, the pushed commits for a push), never the working
tree. Staging clean content and then editing the file must not produce a
passing commit, and a dirty or untracked working file must not fail a push
whose objects are sound.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".githooks"
SAMPLE_PACK = "question-packs/samples/sample-pack.json"
ZERO = "0" * 40


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git",) + args, cwd=cwd, capture_output=True, text=True, check=True
    )


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

    def test_both_hooks_validate_content_through_the_object_snapshot_helper(self):
        helper = HOOKS / "lib/object-snapshot.sh"
        self.assertTrue(helper.exists(), "object-snapshot helper is absent")
        source = helper.read_text(encoding="utf-8")
        # The index and commit snapshots must come from git plumbing, not from
        # a working-tree copy that could carry unstaged content.
        self.assertIn("git checkout-index -a --prefix=", source)
        self.assertIn("git archive --format=tar", source)
        for name in ("pre-commit", "pre-push"):
            self.assertIn(
                'source "$ROOT/.githooks/lib/object-snapshot.sh"',
                self.read(name),
                name,
            )

    def test_no_post_tool_hook_is_recreated(self):
        for path in (ROOT / ".claude", ROOT / ".codex"):
            if path.exists():
                self.assertFalse(any("hook" in item.name.lower() for item in path.iterdir()))


class ObjectSnapshotTests(unittest.TestCase):
    """The snapshot helper must reproduce a git object set exactly."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.email", "hooks@example.invalid", cwd=self.repo)
        git("config", "user.name", "Hook Test", cwd=self.repo)

    def snapshot(self, call: str) -> Path:
        dest = Path(tempfile.mkdtemp(dir=self.tmp))
        script = f'set -euo pipefail\nsource "{HOOKS}/lib/object-snapshot.sh"\n{call} "{dest}"\n'
        proc = subprocess.run(
            ["bash", "-c", script], cwd=self.repo, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return dest

    def test_index_snapshot_excludes_unstaged_edits_and_untracked_files(self):
        tracked = self.repo / "tracked.txt"
        tracked.write_text("staged content\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        # Both of these are outside the staged object set.
        tracked.write_text("dirty working-tree content\n", encoding="utf-8")
        (self.repo / "untracked.txt").write_text("never committed\n", encoding="utf-8")

        dest = self.snapshot("snapshot_index")
        self.assertEqual((dest / "tracked.txt").read_text(encoding="utf-8"), "staged content\n")
        self.assertFalse((dest / "untracked.txt").exists())

    def test_commit_snapshot_excludes_staged_and_unstaged_content(self):
        tracked = self.repo / "tracked.txt"
        tracked.write_text("committed\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        git("commit", "-qm", "seed", cwd=self.repo)
        tracked.write_text("staged but unpushed\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
        tracked.write_text("dirty working-tree content\n", encoding="utf-8")
        (self.repo / "untracked.txt").write_text("never committed\n", encoding="utf-8")

        dest = self.snapshot("snapshot_commit HEAD")
        self.assertEqual((dest / "tracked.txt").read_text(encoding="utf-8"), "committed\n")
        self.assertFalse((dest / "untracked.txt").exists())


class HookObjectBoundaryTests(unittest.TestCase):
    """End-to-end: the hooks gate object-set content, not the working tree."""

    @classmethod
    def setUpClass(cls) -> None:
        original = (ROOT / SAMPLE_PACK).read_text(encoding="utf-8")
        data = json.loads(original)
        # A trailing newline is a real blob difference with identical JSON, so
        # the staged object differs from HEAD while still linting clean.
        cls.clean_pack = original + "\n"
        invalid = json.loads(json.dumps(data))
        invalid["questions"][0]["explanation"] = ""
        cls.invalid_pack = json.dumps(invalid, indent=2, ensure_ascii=False) + "\n"
        uncertified = json.loads(json.dumps(data))
        uncertified.pop("certification", None)
        cls.uncertified_pack = json.dumps(uncertified, indent=2, ensure_ascii=False) + "\n"

    def clone(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        clone = tmp / "clone"
        subprocess.run(
            ["git", "clone", "--local", "--quiet", str(ROOT), str(clone)],
            check=True,
            capture_output=True,
        )
        git("config", "user.email", "hooks@example.invalid", cwd=clone)
        git("config", "user.name", "Hook Test", cwd=clone)
        # Always exercise the current hook source, not the cloned HEAD's copy.
        shutil.rmtree(clone / ".githooks")
        shutil.copytree(HOOKS, clone / ".githooks")
        return clone

    def run_hook(self, clone: Path, name: str, stdin: str = "", env_extra: dict | None = None):
        env = dict(os.environ)
        env.pop("SKIP_HOOKS", None)
        env.update(env_extra or {})
        return subprocess.run(
            [str(clone / ".githooks" / name)],
            cwd=clone,
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
        )

    def stub_heavy_gates(self, clone: Path) -> dict:
        """Replace the aggregate/browser gates so only object selection is under test."""
        gate = clone / "app/test-gate.sh"
        gate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gate.chmod(gate.stat().st_mode | stat.S_IXUSR)
        bindir = clone.parent / "stub-bin"
        bindir.mkdir(exist_ok=True)
        npm = bindir / "npm"
        npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        npm.chmod(0o755)
        return {"PATH": f"{bindir}:{os.environ['PATH']}"}

    def test_pre_commit_passes_when_only_the_working_tree_is_invalid(self):
        clone = self.clone()
        pack = clone / SAMPLE_PACK
        pack.write_text(self.clean_pack, encoding="utf-8")
        git("add", SAMPLE_PACK, cwd=clone)
        # The invalid content is never staged, so it is outside the gated set.
        pack.write_text(self.invalid_pack, encoding="utf-8")
        (clone / "question-packs/samples/untracked-pack.json").write_text(
            "{ not json", encoding="utf-8"
        )

        result = self.run_hook(clone, "pre-commit")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pre_commit_fails_when_the_staged_object_is_invalid(self):
        clone = self.clone()
        pack = clone / SAMPLE_PACK
        pack.write_text(self.invalid_pack, encoding="utf-8")
        git("add", SAMPLE_PACK, cwd=clone)
        # A clean working tree must not rescue an invalid staged blob.
        pack.write_text(self.clean_pack, encoding="utf-8")

        result = self.run_hook(clone, "pre-commit")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pre_push_passes_when_only_the_working_tree_is_uncertified(self):
        clone = self.clone()
        env_extra = self.stub_heavy_gates(clone)
        pack = clone / SAMPLE_PACK
        pack.write_text(self.clean_pack, encoding="utf-8")
        git("add", SAMPLE_PACK, cwd=clone)
        git("commit", "-qm", "certified pack", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        base = git("rev-parse", "HEAD~1", cwd=clone).stdout.strip()
        # Outside the pushed range: neither can satisfy nor fail the gate.
        pack.write_text(self.uncertified_pack, encoding="utf-8")

        result = self.run_hook(
            clone,
            "pre-push",
            stdin=f"refs/heads/main {head} refs/heads/main {base}\n",
            env_extra=env_extra,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pre_push_fails_when_the_pushed_range_is_uncertified(self):
        clone = self.clone()
        env_extra = self.stub_heavy_gates(clone)
        pack = clone / SAMPLE_PACK
        pack.write_text(self.uncertified_pack, encoding="utf-8")
        git("add", SAMPLE_PACK, cwd=clone)
        git("commit", "-qm", "uncertified pack", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        base = git("rev-parse", "HEAD~1", cwd=clone).stdout.strip()
        # A clean working tree must not rescue an uncertified pushed object.
        pack.write_text(self.clean_pack, encoding="utf-8")

        result = self.run_hook(
            clone,
            "pre-push",
            stdin=f"refs/heads/main {head} refs/heads/main {base}\n",
            env_extra=env_extra,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pre_push_validates_every_commit_of_a_new_branch(self):
        clone = self.clone()
        env_extra = self.stub_heavy_gates(clone)
        pack = clone / SAMPLE_PACK
        pack.write_text(self.uncertified_pack, encoding="utf-8")
        git("add", SAMPLE_PACK, cwd=clone)
        git("commit", "-qm", "uncertified pack", cwd=clone)
        head = git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        # A brand-new remote ref has no base; the whole history is the set.
        result = self.run_hook(
            clone,
            "pre-push",
            stdin=f"refs/heads/topic {head} refs/heads/topic {ZERO}\n",
            env_extra=env_extra,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
