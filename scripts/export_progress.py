"""Read-only progress export for the migration boundary.

The exporter never writes a browser/SQLite source.  It requires the attended,
hash-bound inventory and performs a before/after fingerprint check so a source
write during export is a hard failure rather than an implicit delta.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    from reconcile_progress import ReconciliationError, canonical_hash, semantic_counts, validate_document
else:
    from .reconcile_progress import ReconciliationError, canonical_hash, semantic_counts, validate_document


class ExportError(ReconciliationError):
    """The requested export cannot be made safely."""


def validate_inventory(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "path", "approval", "counts", "scope"}:
        raise ExportError("inventory keys must match the attended schema")
    if value["schema_version"] != 1 or value["path"] not in {"one_source", "multi_source", "new_start"}:
        raise ExportError("inventory path or schema is invalid")
    approval = value["approval"]
    attestation = approval.get("attestation") if isinstance(approval, dict) else None
    if (
        not isinstance(approval, dict)
        or set(approval) != {"approved", "disposition", "attestation"}
        or approval.get("approved") is not True
        or not isinstance(approval.get("disposition"), str)
        or not approval["disposition"].strip()
        or not isinstance(attestation, dict)
        or set(attestation) != {"kind", "reference"}
        or attestation.get("kind") != "local_session_ref"
        or not isinstance(attestation.get("reference"), str)
        or len(attestation["reference"]) != 64
        or any(char not in "0123456789abcdef" for char in attestation["reference"])
    ):
        raise ExportError("inventory lacks attended approval attestation")
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != {"sources", "records"}:
        raise ExportError("inventory counts are invalid")
    if any(type(counts[key]) is not int or counts[key] < 0 for key in counts):
        raise ExportError("inventory counts must be non-negative integers")
    if value["path"] == "new_start" and counts != {"sources": 0, "records": 0}:
        raise ExportError("new_start requires zero recovered sources and records")
    scope = value["scope"]
    if not isinstance(scope, dict) or set(scope) != {"active_pack_ids"} or not scope["active_pack_ids"]:
        raise ExportError("active pack scope is required")
    if not all(isinstance(item, str) and item for item in scope["active_pack_ids"]):
        raise ExportError("active pack IDs must be non-empty strings")
    return copy.deepcopy(value)


def load_inventory(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_inventory(json.load(handle))


def source_fingerprint(path: str | Path) -> str:
    """Hash source bytes without opening a writable database handle."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _browser_document(value: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy browser aliases without discarding answer identities."""
    document = copy.deepcopy(value)
    document.setdefault("schema_version", 1)
    document.setdefault("sessions", [])
    document.setdefault("mastery", {})
    document.setdefault("srs", {})
    for session in document["sessions"]:
        if not isinstance(session, dict):
            continue
        for answer in session.get("answers", []):
            if "course_id" not in answer and "course" in answer:
                answer["course_id"] = answer.pop("course")
            if "pack_id" not in answer and "pack" in answer:
                answer["pack_id"] = answer.pop("pack")
            if "question_id" not in answer and "question" in answer:
                answer["question_id"] = answer.pop("question")
    return document


def _read_source(path: Path) -> tuple[dict[str, Any], int]:
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        uri = f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                "SELECT document_json FROM progress_state WHERE id = 1"
            ).fetchone()
        document = json.loads(row[0]) if row else {"schema_version": 1, "sessions": [], "mastery": {}, "srs": {}}
    else:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    document = validate_document(_browser_document(document))
    return document, semantic_counts(document)["records"]


def export_source(
    source_path: str | Path,
    inventory: dict[str, Any],
    *,
    migration_epoch: str,
) -> dict[str, Any]:
    """Export one approved source into a versioned, hash-bound envelope."""
    inventory = validate_inventory(inventory)
    if inventory["path"] == "new_start":
        raise ExportError("new_start forbids source inspection or migration")
    path = Path(source_path)
    if not path.is_file():
        raise ExportError("source path is not a regular file")
    before = source_fingerprint(path)
    document, records = _read_source(path)
    after = source_fingerprint(path)
    if before != after:
        raise ExportError("source changed during export; retry from a new boundary")
    if records == 0:
        raise ExportError("empty or near-empty source is not migration evidence")
    if inventory["path"] == "one_source":
        if inventory["counts"]["sources"] != 1 or inventory["counts"]["records"] != records:
            raise ExportError("measured source counts do not match the attended inventory")
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and records <= 1:
        raise ExportError("near-empty SQLite source cannot establish migration provenance")
    return {
        "schema_version": 1,
        "kind": "source_export",
        "migration_epoch": migration_epoch,
        "source_kind": "sqlite" if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} else "browser_export",
        "source_snapshot_hash": before,
        "source_export_hash": canonical_hash(document),
        "document": document,
        "counts": semantic_counts(document),
        "scope": copy.deepcopy(inventory["scope"]),
    }


def write_export(envelope: dict[str, Any], output: str | Path) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--source")
    parser.add_argument("--output", required=True)
    parser.add_argument("--migration-epoch", required=True)
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        if inventory["path"] == "new_start":
            raise ExportError("new_start produces an empty baseline; it does not export a source")
        if not args.source:
            raise ExportError("--source is required for one_source or multi_source")
        write_export(export_source(args.source, inventory, migration_epoch=args.migration_epoch), args.output)
    except (OSError, sqlite3.Error, json.JSONDecodeError, ExportError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
