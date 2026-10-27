"""Shared fixtures for the ``hybrid_verify`` unit-test modules.

Loads the script (and the verify_pack module objects it uses) once so every test
module patches the same module objects. Contains no test methods.
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
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"

_spec = importlib.util.spec_from_file_location("hybrid_verify", SCRIPT_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# hybrid_verify imports verify_pack (which imports factcheck_pack and
# critic_providers) by path; reach the SAME module objects here so patches
# land where verify_pack.run_layer_c actually looks them up.
vp = hv.verify_pack
fc = vp.factcheck_pack
cp = vp.critic_providers


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def _coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def codex_reply(findings: list[dict], checked: int = 1) -> cp.CriticReply:
    """Canned Codex final message; Codex does not attest the served model."""
    return cp.CriticReply(
        text=json.dumps({"findings": findings, "checked": checked}),
        model=None, provider="codex")


def advisory_reply(findings: list[dict], checked: int = 1) -> cp.CriticReply:
    """Canned opencode CriticReply — model is always None, mirroring the real
    run_opencode contract (opencode's event stream never attests a model)."""
    text = json.dumps({"findings": findings, "checked": checked})
    return cp.CriticReply(text=text, model=None, provider="opencode")


NATIVE_METADATA = {"subject": "Math", "title": "Hybrid verify fixture", "version": 1}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.pack = self.tmp_path / "ch01.json"
        payload = {
            **NATIVE_METADATA,
            "pack_id": "ch01",
            "questions": [dict(CLEAN_Q)],
        }
        payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        self.pack.write_text(json.dumps(payload))

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, extra_argv: list[str]):
        extra_argv = list(extra_argv)
        if "--json" in extra_argv and "--only" not in extra_argv \
                and "--campaign-snapshot" not in extra_argv:
            extra_argv += [
                "--campaign-snapshot",
                hv.certification_campaign.build_snapshot(self.pack)["fingerprint"],
            ]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(
            hv,
            "resolve_advisory_route",
            return_value=hv.AdvisoryRoute(
                selector="opencode-go/mimo-v2.5", variant=None,
            ),
        ), redirect_stdout(out), redirect_stderr(err):
            rc = hv.main([str(self.pack)] + extra_argv)
        return rc, out.getvalue(), err.getvalue()


def _capture(rc_sequence: list[int]):
    """Build a fake verify_pack.main that returns rc_sequence[call_index] and
    records every argv it was called with, in order."""
    calls: list[list[str]] = []

    def fake_main(argv, **kwargs):
        calls.append(argv)
        return rc_sequence[len(calls) - 1]

    return calls, fake_main

