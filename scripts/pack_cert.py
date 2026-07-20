#!/usr/bin/env python3
"""Pack certification helpers — content hash and freshness check (INV-7 T1).

Computes a canonical questions fingerprint for pack certification metadata
and validates that an embedded ``certification`` block is still current.

No CLI, no I/O — callers pass an in-memory pack dict (as loaded from JSON).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# scripts/ isn't a package; import RELEVANT_FIELDS from the Layer-C critic module
# so the projection stays aligned with factcheck_pack's prompt payload.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from factcheck_pack import RELEVANT_FIELDS  # noqa: E402

HASH_SCHEMA_VERSION = "2026-07-20"
CRITIC_CONTRACT_VERSION = "2026-07-20"

CURRENT_GATE = (
    "Pack certification hard-invalidates when either axis drifts: "
    "hash_schema_version (canonical projection rules for questions_hash) or "
    "critic_contract_version (Layer-C critic contract at certify time). "
    "Both must match the current module constants."
)


def _project_question(question: dict) -> dict:
    """Return one question dict restricted to RELEVANT_FIELDS present on it."""
    return {k: question[k] for k in RELEVANT_FIELDS if k in question}


def _normalized_source_directive(pack_dict: dict) -> str | None:
    """Match factcheck_pack.load_source_directive: non-blank str only, else None."""
    d = pack_dict.get("source_directive")
    return d.strip() if isinstance(d, str) and d.strip() else None


def _canonical_payload(pack_dict: dict) -> dict:
    """Build the in-memory structure that questions_hash serializes.

    Raises:
        TypeError: If ``questions`` is present but not a list, or any entry is
            not a dict (fail closed — do not silently hash a subset).
    """
    if "questions" in pack_dict and not isinstance(pack_dict["questions"], list):
        raise TypeError("pack questions must be a list")
    raw_questions = pack_dict.get("questions", [])
    projected: list[dict] = []
    for q in raw_questions:
        if not isinstance(q, dict):
            raise TypeError("each question must be a dict")
        projected.append(_project_question(q))
    payload: dict = {"questions": projected}
    directive = _normalized_source_directive(pack_dict)
    if directive is not None:
        payload["source_directive"] = directive
    return payload


def questions_hash(pack_dict: dict) -> str:
    """Return a canonical SHA-256 digest of pack question content.

    Projects each question to RELEVANT_FIELDS (same set as factcheck_pack) and
    includes top-level ``source_directive`` when it is a non-blank string
    (same normalization as factcheck_pack.load_source_directive; PM-7).
    Non-relevant fields (tags, difficulty, diagram SVG, etc.) are excluded.
    The digest is computed over a sorted-key JSON projection in memory —
    never over file bytes.

    Args:
        pack_dict: Parsed pack JSON as a dict.

    Returns:
        Digest string in the form ``sha256:<hex>``.

    Raises:
        TypeError: If ``pack_dict`` is not a dict, ``questions`` is malformed,
            or a projected value is not JSON-serializable.
    """
    if not isinstance(pack_dict, dict):
        raise TypeError("pack_dict must be a dict")
    canonical = json.dumps(
        _canonical_payload(pack_dict),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def has_pack_wide_l23_waiver(pack_dict: dict) -> bool:
    """Return True if ``lint_waivers`` contains a pack-wide L23 entry (PM-5).

    A pack-wide entry is ``rule == "L23"`` with no ``qid`` (or empty qid). Such a
    waiver would silence coverage enforcement on an installed pack.
    """
    if not isinstance(pack_dict, dict):
        return False
    waivers = pack_dict.get("lint_waivers")
    if not isinstance(waivers, list):
        return False
    for entry in waivers:
        if not isinstance(entry, dict):
            continue
        if entry.get("rule") == "L23" and not entry.get("qid"):
            return True
    return False


def certification_fresh(pack_dict: dict) -> bool:
    """Return True if the pack carries a current, matching certification block.

    Requires ``certification.certified`` is true, both version axes match the
    current module constants, ``certification.questions_hash`` equals a fresh
    recompute via :func:`questions_hash`, and PM-6 critic summary fields:
    ``blocking_count == 0`` and ``questions_examined == len(questions)``.
    Malformed packs return False rather than raising.

    Args:
        pack_dict: Parsed pack JSON as a dict.

    Returns:
        True only when every certification field is present, well-typed, and
        current; False on any missing or malformed field.
    """
    if not isinstance(pack_dict, dict):
        return False
    cert = pack_dict.get("certification")
    if not isinstance(cert, dict):
        return False
    if cert.get("certified") is not True:
        return False
    if cert.get("hash_schema_version") != HASH_SCHEMA_VERSION:
        return False
    if cert.get("critic_contract_version") != CRITIC_CONTRACT_VERSION:
        return False
    stored_hash = cert.get("questions_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    if cert.get("blocking_count") != 0:
        return False
    questions = pack_dict.get("questions", [])
    if not isinstance(questions, list):
        return False
    if cert.get("questions_examined") != len(questions):
        return False
    try:
        return stored_hash == questions_hash(pack_dict)
    except (TypeError, ValueError):
        return False