"""Unit tests for the multi-provider Layer-C critic.

Covers three seams:

* ``scripts/critic_providers.py`` — provider registry, transport, and the secret
  hygiene rules around API keys.
* ``scripts/factcheck_pack.run_critic`` — the dispatch point that decides which
  model reviews a pack.
* ``scripts/critic_panel.py`` — running several independent critics and merging
  their findings, plus the ``verify_pack`` integration that certifies the result.

NO real LLM, network, or subprocess call happens anywhere here: the Claude path
is mocked at ``factcheck_pack.run_claude`` (as every other critic suite does) and
the HTTP providers are mocked at ``urllib.request.urlopen``.

The single most important test in this file is
``MergeFindingsTests.test_a_finding_from_one_pass_alone_survives_the_merge``. The
whole panel is worthless — actively harmful, in fact — if the merge ever becomes
a majority vote, because the defect it exists to catch is the finding only
one model was sharp enough to raise.

Run from the project root::

    python3 -m unittest tests.test_critic_providers -v
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_vp_spec = importlib.util.spec_from_file_location(
    "verify_pack", PROJECT_ROOT / "scripts" / "verify_pack.py")
vp = importlib.util.module_from_spec(_vp_spec)
_vp_spec.loader.exec_module(vp)
# Reach the SAME module objects verify_pack loaded by path, so patches land where
# the production code looks them up.
fc = vp.factcheck_pack
cp = vp.critic_providers
pack_cert = vp.pack_cert
# verify_pack puts scripts/ on sys.path, so the panel module imports by name.
import critic_panel as panel_mod  # noqa: E402

# A key-shaped string used only as test data. Not a credential — it never leaves
# this process and matches nothing. Its job is to prove redaction fires.
FAKE_KEY = "sk-testonly-0123456789abcdef"


# Every fixture pack must satisfy L29: the linter mirrors what QuizzlerKit's
# decoder requires, and it is non-waivable, so a fixture without these fields
# fails Layer A before any critic runs.
NATIVE_METADATA = {"subject": "Math", "title": "Critic provider fixture", "version": 1}


class _FakeResponse:
    """Minimal stand-in for the context manager ``urlopen`` returns."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_response(content: str, model: str = "served-model-1") -> dict:
    """An OpenAI-compatible chat-completions response body."""
    return {"model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}]}


def _critic_json(findings: list[dict], checked: int = 99) -> str:
    return json.dumps({"findings": findings, "checked": checked})


def _finding(qid: str, issue: str, severity: str = "wrong-answer",
             confidence: str = "high", correction: str = "") -> dict:
    return {"qid": qid, "severity": severity, "issue": issue,
            "confidence": confidence, "correction": correction}

# ── shared OpenAI-compatible fixtures ─────────────────────────────────────────


_OAI_ENV = {"QUIZZLER_OPENAI_API_KEY": "sk-testonly-oai",
            "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}


# ── verify_pack integration fixtures ──────────────────────────────────────────


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}
