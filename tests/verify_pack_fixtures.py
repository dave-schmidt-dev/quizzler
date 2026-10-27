"""Shared fixtures for the ``verify_pack`` unit-test modules.

Loads the script once by path so every test module patches the same ``vp`` and
``fc`` module objects. Contains no test methods.
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


def default_coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


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
        # L29: the app refuses a pack without this metadata, so the default
        # fixture is a pack that could actually ship. Pass `field=None` to omit
        # one on purpose.
        payload.setdefault("pack_id", "verify-test")
        payload.setdefault("subject", "Verify Fixture")
        payload.setdefault("title", "Verify Fixture Pack")
        payload.setdefault("version", 1)
        payload.setdefault("questions", [dict(CLEAN_Q)])
        payload = {key: value for key, value in payload.items() if value is not None}
        if "coverage_blueprint" not in payload:
            payload["coverage_blueprint"] = default_coverage_blueprint(payload["questions"])
        p = self.tmp_path / "pack.json"
        p.write_text(json.dumps(payload))
        return p

    def write_pack_without_blueprint(self, **payload) -> Path:
        pack = self.write_pack(**payload)
        data = json.loads(pack.read_text())
        data.pop("coverage_blueprint", None)
        pack.write_text(json.dumps(data))
        return pack

    def run_main(self, argv: list[str], findings: list[dict] | None = None):
        """Invoke verify_pack.main with run_claude + which mocked. `findings` is
        the canned Layer-C critic output (None → no findings)."""
        out, err = io.StringIO(), io.StringIO()
        authorized_argv = list(argv)
        if "--model" not in authorized_argv and "--no-factcheck" not in authorized_argv:
            authorized_argv[1:1] = ["--model", "opus"]
        with patch.object(fc, "run_claude", return_value=envelope(findings or [])), \
             patch.object(vp.critic_providers.shutil, "which", return_value="/usr/bin/claude"):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(authorized_argv)
        return rc, out.getvalue(), err.getvalue()


# A second lint-clean question, distinct from CLEAN_Q so a two-question pack stays
# Layer-A clean (no duplicate-stem/answer tells) for the subset tests below.
CLEAN_Q2 = {
    "id": "q2", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 3 times 3?",
    "options": ["9", "6", "12", "3"], "answer": 0,
    "explanation": "Three times three is nine.",
}


CLEAN_LAYER_A = {"live": [], "waived": [], "hygiene": []}


def _clean_layer_c(**overrides) -> dict:
    base = {
        "live": [], "waived": [], "hygiene": [],
        "errors": [], "coverage_gaps": [],
        "questions_unchecked": 0,
        "model": "claude-sonnet-5",
        "total": 1,
        "source_directive_active": False,
    }
    base.update(overrides)
    return base


def _stale_certification(*, model: str = "claude-sonnet-5", examined: int = 1) -> dict:
    """Pre-existing cert block (stale hash) for write-guard preservation tests."""
    return {
        "certified": True,
        "hash_schema_version": vp.pack_cert.HASH_SCHEMA_VERSION,
        "critic_contract_version": vp.pack_cert.CRITIC_CONTRACT_VERSION,
        "verified_at": "2020-01-01T00:00:00+00:00",
        "questions_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "critic_model": model,
        "blocking_count": 0,
        "questions_examined": examined,
    }

