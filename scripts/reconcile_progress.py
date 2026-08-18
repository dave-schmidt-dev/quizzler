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


EXPORT_ENVELOPE_KEYS = {
    "schema_version",
    "kind",
    "migration_epoch",
    "source_kind",
    "source_snapshot_hash",
    "source_export_hash",
    "document",
    "counts",
    "scope",
}
SOURCE_KINDS = {"sqlite", "browser_export"}
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def validate_export_envelope(envelope: Any) -> dict[str, Any]:
    """Verify one export against the evidence it carries about itself.

    An export is only migration evidence if its recorded content hash and
    counts still describe its own document. Every rejection names the exact
    measured discrepancy so an operator can see what the export claims versus
    what it contains, rather than a generic "invalid export".
    """
    if not isinstance(envelope, dict):
        raise ReconciliationError("export envelope is not an object")
    missing = sorted(EXPORT_ENVELOPE_KEYS - set(envelope))
    unexpected = sorted(set(envelope) - EXPORT_ENVELOPE_KEYS)
    if missing or unexpected:
        raise ReconciliationError(
            f"export envelope keys are invalid (missing={missing}, unexpected={unexpected})"
        )
    if envelope["schema_version"] != 1 or envelope["kind"] != "source_export":
        raise ReconciliationError(
            "export envelope is not a version 1 source_export "
            f"(schema_version={envelope['schema_version']!r}, kind={envelope['kind']!r})"
        )
    if not isinstance(envelope["migration_epoch"], str) or not envelope["migration_epoch"].strip():
        raise ReconciliationError("export envelope has no migration epoch")
    if envelope["source_kind"] not in SOURCE_KINDS:
        raise ReconciliationError(f"unsupported export source kind {envelope['source_kind']!r}")
    if not _hex64(envelope["source_snapshot_hash"]):
        raise ReconciliationError("export source snapshot hash is not a sha256 digest")
    if not _hex64(envelope["source_export_hash"]):
        raise ReconciliationError("export content hash is not a sha256 digest")
    scope = envelope["scope"]
    if (
        not isinstance(scope, dict)
        or set(scope) != {"active_pack_ids"}
        or not isinstance(scope["active_pack_ids"], list)
        or not scope["active_pack_ids"]
        or not all(isinstance(item, str) and item for item in scope["active_pack_ids"])
    ):
        raise ReconciliationError("export scope must name at least one active pack ID")

    document = validate_document(envelope["document"])

    measured_hash = canonical_hash(document)
    if measured_hash != envelope["source_export_hash"]:
        raise ReconciliationError(
            "export content hash does not describe its document "
            f"(recorded={envelope['source_export_hash']}, measured={measured_hash})"
        )

    measured_counts = semantic_counts(document)
    recorded_counts = envelope["counts"]
    if not isinstance(recorded_counts, dict) or set(recorded_counts) != set(measured_counts):
        raise ReconciliationError(
            f"export counts keys are invalid (expected={sorted(measured_counts)}, "
            f"recorded={sorted(recorded_counts) if isinstance(recorded_counts, dict) else recorded_counts})"
        )
    discrepancies = [
        f"{key}: recorded={recorded_counts[key]!r} measured={measured_counts[key]!r}"
        for key in sorted(measured_counts)
        if recorded_counts[key] != measured_counts[key]
    ]
    if discrepancies:
        raise ReconciliationError("export counts do not match its document — " + "; ".join(discrepancies))
    return copy.deepcopy(envelope)


def _cross_check_inventory(
    inventory: dict[str, Any],
    envelopes: list[dict[str, Any]],
    counts: dict[str, int],
) -> None:
    """Require the attended inventory to describe the verified export set."""
    path = inventory.get("path")
    if path not in {"one_source", "multi_source"}:
        raise ReconciliationError(f"inventory path {path!r} does not import a source export")
    recorded = inventory.get("counts")
    if not isinstance(recorded, dict) or set(recorded) != {"sources", "records"}:
        raise ReconciliationError("inventory counts are invalid")
    if recorded["sources"] != len(envelopes):
        raise ReconciliationError(
            "inventory source count does not match the verified exports "
            f"(recorded={recorded['sources']}, measured={len(envelopes)})"
        )
    # Reconciled records, not the per-source sum: deduplication is what the
    # import actually writes, so that is the number the inventory must record.
    if recorded["records"] != counts["records"]:
        raise ReconciliationError(
            "inventory record count does not match the reconciled exports "
            f"(recorded={recorded['records']}, measured={counts['records']})"
        )
    scope = inventory.get("scope", {})
    expected_packs = sorted(scope.get("active_pack_ids", [])) if isinstance(scope, dict) else []
    for envelope in envelopes:
        observed = sorted(envelope["scope"]["active_pack_ids"])
        if observed != expected_packs:
            raise ReconciliationError(
                "export pack scope does not match the attended inventory "
                f"(inventory={expected_packs}, export={observed})"
            )


def reconcile_exports(
    exports: Iterable[dict[str, Any]],
    *,
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile verified export envelopes and require equal source boundaries.

    Every envelope is verified against its own recorded content hash and counts
    before any union occurs. Without that step an edited or truncated export
    would flow straight into an import plan.
    """
    envelopes = [validate_export_envelope(item) for item in exports]
    if not envelopes:
        raise ReconciliationError("at least one export is required")
    epochs = sorted({item["migration_epoch"] for item in envelopes})
    if len(epochs) != 1:
        raise ReconciliationError(f"exports span multiple migration epochs: {epochs}")
    hashes = [item["source_snapshot_hash"] for item in envelopes]
    duplicates = sorted({value for value in hashes if hashes.count(value) > 1})
    if duplicates:
        raise ReconciliationError(f"the same source was exported more than once: {duplicates}")
    document = union_documents([item["document"] for item in envelopes])
    counts = semantic_counts(document)
    if inventory is not None:
        _cross_check_inventory(inventory, envelopes, counts)
    return {
        "schema_version": 1,
        "kind": "reconciled",
        "migration_epoch": epochs[0],
        "source_snapshot_hashes": sorted(hashes),
        "verified_exports": [
            {
                "source_kind": item["source_kind"],
                "source_snapshot_hash": item["source_snapshot_hash"],
                "source_export_hash": item["source_export_hash"],
                "counts": copy.deepcopy(item["counts"]),
            }
            for item in sorted(envelopes, key=lambda item: item["source_snapshot_hash"])
        ],
        "document": document,
        "counts": counts,
        "semantic_hash": canonical_hash(document),
    }
