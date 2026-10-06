#!/usr/bin/env python3
"""Frozen campaign snapshot construction and validation (INV-7).

A campaign snapshot is the portable fingerprint that every piece of discovery
and remediation evidence binds to.  It is deliberately broader than a
certification stamp's question hash: the pack's identity and question context,
its waivers, its course grounding, and the exact reviewer contract all
invalidate a campaign.  Extracted from ``certification_campaign.py``, which
re-exports these helpers because ``hybrid_verify`` calls
``certification_campaign.build_snapshot``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import course_grounding
import pack_cert
import verifier_profiles

# Snapshot version 2 binds the pack's question context (normalized ``subject``
# and ``source_directive``) and pack identity (course id and ``pack_id``) into
# the frozen fingerprint.  Version 1 let a remediation round re-scope a campaign
# by swapping the subject or source directive alongside a real question fix, and
# let the same file name in a different course -- or with a different pack_id --
# reuse another campaign's evidence.  A ledger frozen against a version 1
# snapshot therefore no longer matches its pack and must restart; existing
# certification stamps are unaffected because the install gate only checks
# fingerprint format.
SNAPSHOT_VERSION = 2

# The non-question certification inputs a remediation round must inherit
# unchanged from the campaign's base snapshot.  Question ids are structural
# rather than a frozen input and are checked separately.  Future quarantine and
# cross-campaign evidence inheritance must respect this same frozen set.
FROZEN_FIELDS = (
    "pack_name",
    "pack_identity",
    "question_context",
    "waivers",
    "grounding",
    "critic_contract",
)


class CampaignError(ValueError):
    """Raised for unsafe campaign inputs or an invalid ledger."""


def _canonical(value: Any) -> str:
    """Return stable JSON or raise a clear fail-closed campaign error."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"campaign value is not JSON-serializable: {exc}") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_pack(pack_path: Path) -> dict:
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read pack: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignError("pack root must be a JSON object")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise CampaignError("pack must contain a non-empty questions list")
    for question in questions:
        if not isinstance(question, dict):
            raise CampaignError("each question must be a JSON object")
    return data


def _question_ids(pack: dict) -> list[str]:
    ids: list[str] = []
    for question in pack["questions"]:
        qid = question.get("id")
        if not isinstance(qid, str) or not qid.strip():
            raise CampaignError("every question requires a non-blank string id")
        if qid in ids:
            raise CampaignError(f"duplicate question id: {qid}")
        ids.append(qid)
    return ids


def _grounding_evidence(pack_path: Path) -> dict:
    """Fingerprint grounding config and source text without retaining either.

    The raw configured path and source excerpt deliberately stay out of the
    ledger.  A changed config still changes its digest; a changed source changes
    its text digest.  A missing optional grounding block is represented
    explicitly, so it cannot be confused with a malformed course file.
    """
    course_path = pack_path.parent / "_course.json"
    raw_grounding: Any = None
    if course_path.exists():
        try:
            course = json.loads(course_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CampaignError(f"cannot read course grounding metadata: {exc}") from exc
        if not isinstance(course, dict):
            raise CampaignError("course metadata root must be a JSON object")
        raw_grounding = course.get("grounding")
        if raw_grounding is not None and not isinstance(raw_grounding, dict):
            raise CampaignError("course grounding must be an object when present")

    try:
        source_text = course_grounding.load_source_text(pack_path)
    except OSError as exc:
        raise CampaignError(f"cannot resolve course source text: {exc}") from exc
    if source_text is not None and not isinstance(source_text, str):
        raise CampaignError("course source text resolver returned a non-string")
    return {
        "configured": raw_grounding is not None,
        "config_digest": _digest(raw_grounding),
        "source_text_digest": _text_digest(source_text) if source_text else None,
    }


def _critic_contract(profile_name: str | None) -> dict:
    name = profile_name or verifier_profiles.DEFAULT_PROFILE
    try:
        profile = verifier_profiles.get_profile(name)
    except (KeyError, ValueError) as exc:
        raise CampaignError(f"unknown verifier profile: {name}") from exc
    return {
        "version": pack_cert.CRITIC_CONTRACT_VERSION,
        "profile": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
    }


def build_snapshot(pack_path: Path, *, verifier_profile: str | None = None) -> dict:
    """Build a portable frozen-input fingerprint for one campaign.

    This is deliberately broader than a certification stamp's question hash:
    pack identity, question context, waivers, course grounding, and the exact
    reviewer contract all invalidate a campaign.  The snapshot contains only
    hashes, question ids, and the pack's course id and pack_id -- never course
    source text or absolute filesystem paths.
    """
    pack = _load_pack(pack_path)
    question_ids = _question_ids(pack)
    # Keep enough detail to prove exactly which question records changed during
    # a frozen remediation batch, without retaining their contents in the
    # evidence ledger.  This is intentionally separate from questions_hash:
    # a question-level diff is not a substitute for the final full-pack gate.
    question_hashes = {
        question_id: _digest(question)
        for question_id, question in zip(question_ids, pack["questions"], strict=True)
    }
    try:
        question_hash = pack_cert.questions_hash(pack)
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"cannot fingerprint questions: {exc}") from exc
    waivers = {
        "lint_waivers_digest": _digest(pack.get("lint_waivers", [])),
        "factcheck_waivers_digest": _digest(pack.get("factcheck_waivers", [])),
    }
    # The per-question hashes above grade whole question records, so a subject
    # or source_directive swap leaves them untouched.  pack_cert's normalizers
    # are the canonical normalization (they match factcheck_pack's loaders), so
    # the frozen question context stays in lockstep with the certification hash.
    question_context = _digest({
        "subject": pack_cert._normalized_subject(pack),
        "source_directive": pack_cert._normalized_source_directive(pack),
    })
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "pack_name": pack_path.name,
        "pack_identity": {
            "course": pack_path.parent.name,
            "pack_id": pack.get("pack_id"),
        },
        "questions_hash": question_hash,
        "question_ids": question_ids,
        "question_hashes": question_hashes,
        "question_context": question_context,
        "waivers": waivers,
        "grounding": _grounding_evidence(pack_path),
        "critic_contract": _critic_contract(verifier_profile),
    }
    return {**payload, "fingerprint": _digest(payload)}


def _validate_snapshot(snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        raise CampaignError("snapshot must be an object")
    expected = {k: snapshot.get(k) for k in snapshot if k != "fingerprint"}
    fingerprint = snapshot.get("fingerprint")
    if not isinstance(fingerprint, str) or fingerprint != _digest(expected):
        raise CampaignError("snapshot fingerprint is missing or does not match its contents")
    ids = snapshot.get("question_ids")
    if (not isinstance(ids, list) or not ids or any(not isinstance(q, str) or not q for q in ids)
            or len(set(ids)) != len(ids)):
        raise CampaignError("snapshot question_ids must be non-empty unique strings")
    question_hashes = snapshot.get("question_hashes")
    if (not isinstance(question_hashes, dict) or set(question_hashes) != set(ids)
            or any(not isinstance(value, str) or not value.startswith("sha256:")
                   for value in question_hashes.values())):
        raise CampaignError("snapshot question_hashes must map every question id to a digest")
    contract = snapshot.get("critic_contract")
    if (not isinstance(contract, dict) or not isinstance(contract.get("profile"), str)
            or not contract["profile"].strip()):
        raise CampaignError("snapshot critic contract must name a verifier profile")
