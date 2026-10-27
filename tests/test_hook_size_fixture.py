"""The hook fixture keeps retired size exceptions and source bytes coherent."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from tests import test_git_hooks as hooks


class FileSizeFixtureTests(unittest.TestCase):
    def test_retired_exception_cannot_copy_an_untracked_parent_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, clone = root / "current", root / "clone"
            (current / "scripts").mkdir(parents=True)
            clone.mkdir()
            (current / "scripts/check_file_size.py").write_text("# fixture checker\n")
            (current / ".file-size-exceptions").write_text("# no exceptions\n")
            (root / "outside.py").write_text("# outside the fixture repositories\n")
            (clone / ".file-size-exceptions").write_text("../outside.py invalid path\n")
            hooks.git("init", "-q", "-b", "main", cwd=clone)
            hooks.git("add", ".", cwd=clone)

            with patch.object(hooks, "ROOT", current):
                hooks.HookObjectBoundaryTests.add_file_size_support(clone)

            self.assertEqual(
                (root / "outside.py").read_text(),
                "# outside the fixture repositories\n",
            )
            self.assertNotIn("outside.py", hooks.git("ls-files", cwd=clone).stdout)

    def test_retired_exception_syncs_current_source_into_fixture_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, clone = root / "current", root / "clone"
            (current / "scripts").mkdir(parents=True)
            clone.mkdir()
            (current / "scripts/check_file_size.py").write_text("# fixture checker\n")
            (current / ".file-size-exceptions").write_text("# no exception needed\n")
            (current / "source.py").write_text("pass\n")
            (clone / ".file-size-exceptions").write_text("source.py old fixture\n")
            (clone / "source.py").write_text("pass\n" * 801)
            hooks.git("init", "-q", "-b", "main", cwd=clone)
            hooks.git("add", ".", cwd=clone)

            with patch.object(hooks, "ROOT", current):
                hooks.HookObjectBoundaryTests.add_file_size_support(clone)

            self.assertTrue(
                (clone / "source.py").read_text() == "pass\n",
                "fixture kept oversized HEAD bytes after retiring their exception",
            )
            self.assertEqual(hooks.git("show", ":source.py", cwd=clone).stdout, "pass\n")


    def test_native_hook_uses_fixture_tools_even_when_host_tools_are_installed(self):
        harness = hooks.HookObjectBoundaryTests(methodName="runTest")
        self.addCleanup(harness.doCleanups)
        clone = harness.clone()
        probe = clone / "app/FixtureProbe.swift"
        probe.write_text("struct FixtureProbe {}\n", encoding="utf-8")
        hooks.git("add", "app/FixtureProbe.swift", cwd=clone)
        host_bin = clone.parent / "host-native-bin"
        host_bin.mkdir()
        host_calls = clone.parent / "host-native-calls"
        for name in ("swiftlint", "periphery"):
            tool = host_bin / name
            tool.write_text(
                '#!/bin/sh\nprintf "%s\n" "${0##*/}" >> "$HOST_NATIVE_CALLS"\nexit 97\n',
                encoding="utf-8",
            )
            tool.chmod(0o755)
        result = harness.run_hook(clone, "pre-commit", env_extra={
            "PATH": f"{host_bin}:{os.environ['PATH']}",
            "HOST_NATIVE_CALLS": str(host_calls),
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(host_calls.exists(), "fixture invoked a host native tool")
        self.assertEqual(
            (clone.parent / "native-bin/calls").read_text().splitlines(),
            ["swiftlint", "periphery"],
        )


if __name__ == "__main__":
    unittest.main()
