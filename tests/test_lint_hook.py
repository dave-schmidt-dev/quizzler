"""Subprocess tests for ``scripts/lint_hook.py``.

Verifies the module's documented contract: any parse/lookup failure or
non-dict payload exits 0 and never blocks editing. Specifically covers the
D-17 fix: JSON null and array payloads (which previously reached .get() on a
non-dict and raised AttributeError → exit 1) must now exit 0 cleanly.

Run from the project root::

    python3 -m unittest tests.test_lint_hook -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LINT_HOOK = PROJECT_ROOT / "scripts" / "lint_hook.py"


class LintHookNonDictTests(unittest.TestCase):
    """D-17: non-dict payloads must exit 0, never block editing."""

    def _run(self, payload: str) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, str(LINT_HOOK)],
            input=payload,
            capture_output=True,
            text=True,
        )
        return r.returncode, r.stderr

    def test_null_payload_exits_zero(self):
        """JSON null on stdin must exit 0."""
        code, _ = self._run("null")
        self.assertEqual(code, 0)

    def test_array_payload_exits_zero(self):
        """JSON array on stdin must exit 0."""
        code, _ = self._run("[]")
        self.assertEqual(code, 0)

    def test_empty_dict_no_file_path_exits_zero(self):
        """A valid dict with no file_path must exit 0 (non-pack path)."""
        code, _ = self._run("{}")
        self.assertEqual(code, 0)

    def test_non_pack_file_path_exits_zero(self):
        """A dict pointing at a non-pack file must exit 0 silently."""
        payload = '{"tool_input": {"file_path": "/tmp/not-a-pack.txt"}}'
        code, _ = self._run(payload)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
