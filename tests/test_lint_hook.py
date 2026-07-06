"""Subprocess tests for ``scripts/lint_hook.py``.

Verifies the module's documented contract: any parse/lookup failure or
non-dict payload exits 0 and never blocks editing. Specifically covers the
D-17 fix: JSON null and array payloads (which previously reached .get() on a
non-dict and raised AttributeError → exit 1) must now exit 0 cleanly.

Run from the project root::

    python3 -m unittest tests.test_lint_hook -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LINT_HOOK = PROJECT_ROOT / "scripts" / "lint_hook.py"
PACKS_DIR = PROJECT_ROOT / "question-packs"


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


class LintHookL23AdvisoryTests(unittest.TestCase):
    """The L23 absent-`coverage_blueprint` nudge (severity "advisory") must NOT
    block the authoring hook — every pre-existing pack lacks a blueprint. A real
    blocking finding (a declared-but-uncovered blueprint topic → L23 critical)
    still exits 2. Uses a throwaway course folder under question-packs/ because
    the hook only fires for a *.json inside a real course subfolder."""

    CLEAN_Q = {
        "id": "q1", "type": "multiple_choice", "topic": "coverage",
        "difficulty": "easy", "prompt": "Which control repairs damage after an incident?",
        "options": ["Preventive", "Detective", "Corrective", "Compensating"],
        "answer": 2,
        "explanation": ("A corrective control repairs damage; preventive, detective, "
                        "and compensating controls do not repair after the fact."),
    }

    def setUp(self):
        # A non-hidden, non-underscore course folder so _is_question_pack matches.
        self.course_dir = PACKS_DIR / f"zz-hooktest-{os.getpid()}"
        self.course_dir.mkdir(parents=True, exist_ok=True)
        self.pack = self.course_dir / "pack.json"

    def tearDown(self):
        shutil.rmtree(self.course_dir, ignore_errors=True)

    def _run(self) -> int:
        payload = json.dumps({"tool_input": {"file_path": str(self.pack)}})
        r = subprocess.run([sys.executable, str(LINT_HOOK)], input=payload,
                           capture_output=True, text=True)
        return r.returncode

    def test_blueprintless_clean_pack_exits_zero(self):
        # No coverage_blueprint → only the L23 advisory fires → non-blocking → 0.
        self.pack.write_text(json.dumps({"pack_id": "hooktest", "questions": [self.CLEAN_Q]}))
        self.assertEqual(self._run(), 0)

    def test_blueprint_undercoverage_blocks(self):
        # A declared-but-uncovered blueprint topic is a real L23 critical → 2.
        self.pack.write_text(json.dumps({
            "pack_id": "hooktest",
            "coverage_blueprint": [{"topic": "unseen-topic", "min": 1}],
            "questions": [self.CLEAN_Q],
        }))
        self.assertEqual(self._run(), 2)


if __name__ == "__main__":
    unittest.main()
