"""Shared fixtures and loaded script module for ``scripts/lint_packs.py`` tests."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "lint_packs.py"

_spec = importlib.util.spec_from_file_location("lint_packs", SCRIPT_PATH)
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)


# ── fixture builders ─────────────────────────────────────────────────────────

def mc(**over) -> dict:
    """A valid 4-option multiple_choice question; override any field."""
    base = {
        "id": "q1", "type": "multiple_choice", "topic": "t", "difficulty": "easy",
        "prompt": "Which control repairs damage after an incident?",
        "options": ["Preventive", "Detective", "Corrective", "Compensating"],
        "answer": 2, "explanation": "A corrective control repairs damage.",
    }
    base.update(over)
    return base


def matching(**over) -> dict:
    base = {
        "id": "m1", "type": "matching", "topic": "t", "difficulty": "easy",
        "prompt": "Match each item to its category.",
        "leftItems": ["Kelvin", "Pascal"],
        "rightItems": ["Temperature", "Pressure"],
        "correctPairs": [1, 0],  # non-identity so the L1 identity warning stays out
        "explanation": "Kelvin measures temperature; Pascal measures pressure.",
    }
    base.update(over)
    return base


def tf(**over) -> dict:
    base = {
        "id": "t1", "type": "true_false", "topic": "t", "difficulty": "easy",
        "prompt": "The sky is blue.", "answer": True,
        "explanation": "Rayleigh scattering.",
    }
    base.update(over)
    return base


def ms(**over) -> dict:
    """A valid 4-option multiple_select with 2 correct answers; override any field.

    Deliberately clean for L22: 2-of-4 correct (not a lone distractor), balanced
    option lengths, no distinctive prompt term echoed only in the correct set, no
    count word in the prompt, no meta/position options.
    """
    base = {
        "id": "s1", "type": "multiple_select", "topic": "t", "difficulty": "medium",
        "prompt": "Which of the listed protocols operate at the transport layer?",
        "options": ["The TCP protocol", "The UDP protocol", "The ARP protocol", "The ICMP protocol"],
        "answers": [0, 1],
        "explanation": "TCP and UDP are transport-layer; ARP and ICMP are not.",
    }
    base.update(over)
    return base


def rules(findings, rule=None, severity=None):
    out = findings
    if rule is not None:
        out = [f for f in out if f["rule"] == rule]
    if severity is not None:
        out = [f for f in out if f["severity"] == severity]
    return out


def coverage_blueprint_for(questions: list[dict]) -> list[dict]:
    """Default blueprint covering each question topic/area pair."""
    pairs = sorted({
        (q.get("topic"), q.get("exam_area"))
        for q in questions
        if q.get("topic")
    })
    return [
        {"topic": topic, **({"area": area} if area else {}), "min": 1}
        for topic, area in pairs
    ]


def clean_pack_dict(*, pack_id: str = "x", questions: list[dict], **extra) -> dict:
    """Build a lint-clean pack payload with a matching coverage_blueprint.

    Carries the native metadata L29 requires, because a pack the iOS client
    cannot decode is not clean in any sense a caller of this helper means.
    """
    pack = {"pack_id": pack_id, "questions": questions, **extra}
    pack.setdefault("coverage_blueprint", coverage_blueprint_for(questions))
    pack.setdefault("subject", "Sample Course")
    pack.setdefault("title", "Round 1")
    pack.setdefault("version", 1)
    return pack


def _fenced_json_after(doc: Path, marker: str) -> str:
    """Return the first ```json fenced block that follows `marker` in `doc`."""
    text = doc.read_text()
    start = text.index(marker) + len(marker)
    fence = text.index("```json", start) + len("```json")
    return text[fence:text.index("```", fence)]
