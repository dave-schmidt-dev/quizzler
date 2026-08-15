"""Pure, read-only progress reconciliation helpers.

The migration tools deliberately keep source inspection separate from source
mutation.  This module normalizes documents, computes evidence hashes, and
builds the explicit empty baseline used by the approved ``new_start`` path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable


class ReconciliationError(ValueError):
    """The source set cannot be reconciled without a human decision."""


EMPTY_DOCUMENT: dict[str, Any] = {
    "schema_version": 1,
    "sessions": [],
    "mastery": {},
    "srs": {},
}


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    return value


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON used for all migration evidence."""
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_document(document: Any) -> dict[str, Any]:
    """Validate and return a defensive copy of a normalized progress document."""
    if not isinstance(document, dict) or set(document) != {"schema_version", "sessions", "mastery", "srs"}:
        raise ReconciliationError("progress document shape is incompatible")
    if document["schema_version"] != 1 or not isinstance(document["sessions"], list):
        raise ReconciliationError("unsupported progress document")
    if not isinstance(document["mastery"], dict) or not isinstance(document["srs"], dict):
        raise ReconciliationError("mastery and srs must be objects")
    for session in document["sessions"]:
        if not isinstance(session, dict):
            raise ReconciliationError("session is not an object")
        answers = session.get("answers", [])
        if not isinstance(answers, list):
            raise ReconciliationError("session answers must be an array")
        for answer in answers:
            if not isinstance(answer, dict):
                raise ReconciliationError("answer is not an object")
            if not all(answer.get(key) for key in ("course_id", "pack_id", "question_id")):
                # Legacy rows without a pack identity cannot be imported safely.
                raise ReconciliationError("answer lacks course_id, pack_id, or question_id")
    return copy.deepcopy(document)


def semantic_counts(document: dict[str, Any]) -> dict[str, int]:
    document = validate_document(document)
    mastery_questions = sum(
        len(pack.get("seen", {}))
        for course in document["mastery"].values()
        if isinstance(course, dict)
        for pack in course.values()
        if isinstance(pack, dict)
    )
    srs_questions = sum(
        len(value.get("questions", {}))
        for value in document["srs"].values()
        if isinstance(value, dict)
    )
    return {
        "sessions": len(document["sessions"]),
        "mastery_questions": mastery_questions,
        "srs_questions": srs_questions,
        "records": len(document["sessions"]) + mastery_questions + srs_questions,
    }


def build_new_start_baseline(
    inventory: dict[str, Any],
    migration_epoch: str,
) -> dict[str, Any]:
    """Build a hash-bound empty baseline without importing or claiming history."""
    if inventory.get("path") != "new_start":
        raise ReconciliationError("new-start baseline requires the new_start inventory path")
    counts = inventory.get("counts")
    if counts != {"sources": 0, "records": 0}:
        raise ReconciliationError("new_start requires zero recovered sources and records")
    approval = inventory.get("approval", {})
    attestation = approval.get("attestation", {}) if isinstance(approval, dict) else {}
    if (
        approval.get("approved") is not True
        or attestation.get("kind") != "local_session_ref"
        or not isinstance(attestation.get("reference"), str)
        or re.fullmatch(r"[0-9a-f]{64}", attestation["reference"]) is None
    ):
        raise ReconciliationError("new_start requires affirmative approval")
    if not isinstance(migration_epoch, str) or not migration_epoch.strip():
        raise ReconciliationError("migration epoch is required")
    document = copy.deepcopy(EMPTY_DOCUMENT)
    source_snapshot_hash = canonical_hash(inventory)
    hash_payload = {
        "schema_version": 1,
        "kind": "new_start_baseline",
        "migration_epoch": migration_epoch,
        "source_snapshot_hash": source_snapshot_hash,
        "active_pack_ids": sorted(inventory["scope"]["active_pack_ids"]),
        "document": document,
        "document_revision": 0,
        "operation_id": "baseline",
        "import_claim": False,
    }
    document_hash = canonical_hash(hash_payload)
    return {
        "schema_version": 1,
        "kind": "new_start",
        "migration_epoch": migration_epoch,
        "source_path": "new_start",
        "source_snapshot_hash": source_snapshot_hash,
        "source_export_hash": None,
        "counts": {"sources": 0, "records": 0},
        "scope": copy.deepcopy(inventory["scope"]),
        "document": document,
        "cloudkit_baseline": {
            "document_revision": 0,
            "operation_id": "baseline",
            "semantic_hash": document_hash,
            "import_claim": False,
        },
        "baseline_hash_payload": hash_payload,
    }


def _merge_mapping(left: dict[str, Any], right: dict[str, Any], path: str) -> dict[str, Any]:
    merged = copy.deepcopy(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        elif canonical_hash(merged[key]) != canonical_hash(value):
            raise ReconciliationError(f"irreconcilable conflict at {path}.{key}")
    return merged


def union_documents(documents: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Deterministically union source documents, rejecting ambiguous records."""
    docs = [validate_document(document) for document in documents]
    if not docs:
        return copy.deepcopy(EMPTY_DOCUMENT)
    sessions: dict[str, dict[str, Any]] = {}
    for document in docs:
        for index, session in enumerate(document["sessions"]):
            identity = str(session.get("operation_id") or session.get("session_id") or f"legacy-{canonical_hash(session)}")
            if identity in sessions and canonical_hash(sessions[identity]) != canonical_hash(session):
                raise ReconciliationError(f"irreconcilable session conflict at {identity}")
            sessions[identity] = session
    mastery: dict[str, Any] = {}
    srs: dict[str, Any] = {}
    for document in docs:
        mastery = _merge_mapping(mastery, document["mastery"], "mastery")
        srs = _merge_mapping(srs, document["srs"], "srs")
    result = {
        "schema_version": 1,
        "sessions": [sessions[key] for key in sorted(sessions)],
        "mastery": mastery,
        "srs": srs,
    }
    return validate_document(result)


def reconcile_exports(exports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile exported envelopes and require equal source boundaries."""
    envelopes = list(exports)
    if not envelopes:
        raise ReconciliationError("at least one export is required")
    hashes = {item.get("source_snapshot_hash") for item in envelopes}
    if None in hashes:
        raise ReconciliationError("export is missing source snapshot hash")
    documents = [item.get("document") for item in envelopes]
    if any(document is None for document in documents):
        raise ReconciliationError("export is missing normalized document")
    document = union_documents(documents)
    return {
        "schema_version": 1,
        "kind": "reconciled",
        "source_snapshot_hashes": sorted(hashes),
        "document": document,
        "counts": semantic_counts(document),
        "semantic_hash": canonical_hash(document),
    }
