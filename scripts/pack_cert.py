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

# INV-7: the only review methods that produce an installable certification. A
# cert must name one of these explicitly — an absent `review_method` is not a
# pass. The former "codex-local-semantic-review" fallback is deliberately absent
# and its minting script is deleted: it wrote a self-attested certification from
# inside the same session that authored the pack, which let the SY0-701 pack ship
# with 54 source-dependent prompts and 61 exam-invalid questions while the
# install gate reported a clean pass. A pack reviewed only by its own author is
# not certified, regardless of which flags were passed.
#
# `external-layer-c-panel` is the MULTI-CRITIC method (scripts/critic_panel.py):
# two or more independent providers graded the same questions and the union of
# their findings cleared. It is listed alongside the single-critic methods, not
# above them, because the gate bar is identical — zero blocking findings, full
# coverage. What the panel buys is not a lower bar but a better-evidenced pass:
# one model reporting "nothing wrong" is indistinguishable from one model not
# looking, and N independent models are not. The cert's `critic_panel` block
# records which providers actually ran and which models they REPORTED using.
APPROVED_REVIEW_METHODS = frozenset({
    "external-layer-c-strict",
    "external-layer-c-standard",
    "external-layer-c-panel",
})

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


def question_content_hash(question: dict, pack_dict: dict) -> str:
    """Return a canonical SHA-256 digest of ONE question's content (INV-7 B.1).

    A per-question analogue of :func:`questions_hash`: projects the single
    ``question`` to the SAME ``RELEVANT_FIELDS`` set and folds in the pack's
    normalized ``source_directive`` the SAME way, so a per-qid stamp and the
    aggregate hash agree on what "content" means. This is the primitive behind
    the per-question certification stamp registry (``question_stamps``) that lets
    a single edited question be re-certified cheaply while still proving — qid by
    qid — that the rest of the pack is unchanged.

    Args:
        question: One question dict (as loaded from the pack's ``questions``).
        pack_dict: The parsed pack, read only for its ``source_directive``.

    Returns:
        Digest string in the form ``sha256:<hex>``.

    Raises:
        TypeError: If ``question`` is not a dict or a projected value is not
            JSON-serializable.
    """
    if not isinstance(question, dict):
        raise TypeError("question must be a dict")
    payload: dict = {"question": _project_question(question)}
    directive = _normalized_source_directive(pack_dict)
    if directive is not None:
        payload["source_directive"] = directive
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_question_stamps(pack_dict: dict) -> dict:
    """Return ``{qid: question_content_hash}`` for every id-bearing question.

    Questions without a usable ``id`` are skipped — they cannot be keyed into the
    registry. :func:`question_stamps_fresh` fails closed on such a question, so a
    pack containing one can never be certification-fresh via the per-qid path
    (real packs are lint-required to carry ids, so this never fires in practice).
    """
    stamps: dict = {}
    for q in pack_dict.get("questions", []) or []:
        if isinstance(q, dict) and q.get("id"):
            stamps[q["id"]] = question_content_hash(q, pack_dict)
    return stamps


def question_stamps_fresh(pack_dict: dict, stamps) -> bool:
    """Return True only when EVERY question has a matching fresh per-qid stamp.

    This is the PM-3 coverage primitive: an aggregate certification carrying a
    ``question_stamps`` registry is fresh only when the registry accounts for
    every current question with an up-to-date content hash. A ``--only`` subset
    re-cert that recomputes the whole-pack ``questions_hash`` therefore CANNOT
    forge a fresh aggregate while some qid was edited-but-unaudited: that qid's
    carried-over stamp will not match its current content, and this returns False.

    Fails closed: a non-dict registry, a malformed ``questions`` list, a non-dict
    question, or a question with no ``id`` all yield False (an unidentifiable or
    unmatched question is treated as UNAUDITED, never waved through).
    """
    if not isinstance(stamps, dict):
        return False
    questions = pack_dict.get("questions", [])
    if not isinstance(questions, list):
        return False
    for q in questions:
        if not isinstance(q, dict):
            return False
        qid = q.get("id")
        if not qid:
            return False  # can't prove coverage of an id-less question (fail closed)
        try:
            if stamps.get(qid) != question_content_hash(q, pack_dict):
                return False
        except (TypeError, ValueError):
            return False
    return True


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

    PM-3 per-qid coverage (INV-7 B.1): a cert that ALSO carries a
    ``question_stamps`` registry (the "new format") is fresh only when EVERY
    question additionally has a matching fresh per-qid stamp
    (:func:`question_stamps_fresh`). This is what stops a ``--only`` subset
    re-cert from forging a fresh aggregate while some qid is unaudited. A LEGACY
    cert (no ``question_stamps`` key) is validated by the aggregate hash alone —
    so a pre-existing whole-pack certification stays valid until the pack is next
    edited (backward compatible; the world is not invalidated on upgrade).

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
        if stored_hash != questions_hash(pack_dict):
            return False
    except (TypeError, ValueError):
        return False
    # The cert must NAME how the pack was reviewed, and the method must be one
    # this project accepts. An absent or unrecognized `review_method` is a fail,
    # not a legacy pass: "unstated" was how a self-attested local review became
    # indistinguishable from an external one at the install gate.
    if cert.get("review_method") not in APPROVED_REVIEW_METHODS:
        return False

    # PM-3 per-qid coverage is now MANDATORY. Previously an absent
    # ``question_stamps`` registry fell back to aggregate-hash-only validation
    # for backward compatibility — which meant a cert that simply omitted the
    # registry skipped per-question coverage entirely. A pack that has not been
    # graded question-by-question is not certified. A malformed/non-dict
    # registry, or any qid whose carried stamp no longer matches its content,
    # fails closed.
    stamps = cert.get("question_stamps")
    if not isinstance(stamps, dict) or not stamps:
        return False
    if not question_stamps_fresh(pack_dict, stamps):
        return False
    return True
