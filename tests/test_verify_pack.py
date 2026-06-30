"""Unit tests for ``scripts/verify_pack.py`` — the pack-readiness gate.

verify_pack runs Layer A (structure) + Layer C (factual LLM critic) as one hard
gate. Layer C's LLM subprocess is NON-deterministic and costs money, so every
test here MOCKS ``factcheck_pack.run_claude`` (and ``shutil.which`` so the gate
believes the ``claude`` CLI is present) to return a canned ``claude
--output-format json`` envelope. NO real LLM or network call happens.

Throw-away temp packs are written per-test (mirroring test_build_manifest.py) so
the real ``question-packs/`` tree is never touched.

Run from the project root::

    python3 -m unittest tests.test_verify_pack -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "verify_pack.py"

_spec = importlib.util.spec_from_file_location("verify_pack", SCRIPT_PATH)
vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vp)
# verify_pack imports factcheck_pack by path during its own load; reach the same
# module object so patches land where run_layer_c looks them up.
fc = vp.factcheck_pack


# A lint-clean MC question: numeric distractors carry no tokens (L10 has nothing
# to assess); explanation/topic/difficulty present satisfy L12.
CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def envelope(findings: list[dict]) -> str:
    """Build a canned ``claude --output-format json`` envelope whose `result` is
    the critic's JSON object, exactly what run_claude returns as stdout."""
    inner = json.dumps({"findings": findings, "checked": 99})
    return json.dumps({"type": "result", "result": inner,
                       "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_pack(self, **payload) -> Path:
        payload.setdefault("pack_id", "verify-test")
        payload.setdefault("questions", [dict(CLEAN_Q)])
        p = self.tmp_path / "pack.json"
        p.write_text(json.dumps(payload))
        return p

    def run_main(self, argv: list[str], findings: list[dict] | None = None):
        """Invoke verify_pack.main with run_claude + which mocked. `findings` is
        the canned Layer-C critic output (None → no findings)."""
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fc, "run_claude", return_value=envelope(findings or [])), \
             patch.object(vp.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()


class CleanPackTests(_Base):
    def test_clean_pack_no_findings_is_ready(self):
        pack = self.write_pack()
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        self.assertIn("Layer A (structure): clean", out)
        self.assertIn("Layer C (factual): clean", out)


class LayerATests(_Base):
    def test_layer_a_critical_blocks(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")  # L12 critical
        pack = self.write_pack(questions=[dirty])
        rc, out, _ = self.run_main([str(pack)], findings=[])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer-A", out)
        self.assertIn("L12", out)


class LayerCTests(_Base):
    FINDING = {"qid": "q1", "severity": "wrong-answer",
               "issue": "two plus two is five, not four",
               "correction": "the answer is four", "confidence": "high"}

    def test_layer_c_finding_blocks(self):
        pack = self.write_pack()
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out)
        self.assertIn("Layer C (factual): 1 live finding", out)
        self.assertIn("PACK NOT READY: 0 Layer-A + 1 Layer-C", out)

    def test_layer_c_finding_waived_is_ready(self):
        pack = self.write_pack(factcheck_waivers=[
            {"qid": "q1", "reason": "intentional trick distractor; verified by author"},
        ])
        rc, out, _ = self.run_main([str(pack)], findings=[self.FINDING])
        self.assertEqual(rc, 0)
        self.assertIn("PACK READY", out)
        self.assertIn("Layer C (factual): clean (1 waived)", out)


class NoFactcheckTests(_Base):
    def test_no_factcheck_skips_layer_c_and_prints_note(self):
        pack = self.write_pack()
        # No run_claude mock needed — Layer C must not run at all. If it did, the
        # real `claude` CLI absence would surface; we assert the skip note instead.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 0)
        # Structure-only must NOT claim full readiness — it never ran Layer C.
        self.assertIn("NOT certified ready", out.getvalue())
        self.assertNotIn("PACK READY", out.getvalue())
        self.assertIn("structure-only (Layer C skipped) — this is NOT the full readiness gate.",
                      out.getvalue())

    def test_no_factcheck_still_blocks_on_layer_a(self):
        dirty = dict(CLEAN_Q)
        dirty.pop("explanation")
        pack = self.write_pack(questions=[dirty])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = vp.main([str(pack), "--no-factcheck"])
        self.assertEqual(rc, 2)
        self.assertIn("PACK NOT READY", out.getvalue())


class OperationalErrorTests(_Base):
    def test_missing_pack_is_operational_error(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = vp.main([str(self.tmp_path / "does-not-exist.json")])
        self.assertEqual(rc, 1)
        self.assertIn("pack not found", err.getvalue())

    def test_missing_claude_cli_is_operational_error(self):
        pack = self.write_pack()
        err = io.StringIO()
        with patch.object(vp.shutil, "which", return_value=None):
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = vp.main([str(pack)])
        self.assertEqual(rc, 1)
        self.assertIn("claude", err.getvalue())


if __name__ == "__main__":
    unittest.main()
