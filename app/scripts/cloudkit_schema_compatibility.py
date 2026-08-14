#!/usr/bin/env python3
"""Compare captured CloudKit schemas offline using an additive-only policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class SchemaCompatibilityError(ValueError):
    """A stable schema-capture or compatibility rejection."""


ALLOWED_DISPOSITIONS = {"same-container", "new-container"}
ALLOWED_ENVIRONMENTS = {"Development", "Production"}


def _load(path: Path, expected_environment: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaCompatibilityError("schema-capture-unreadable") from exc
    required = {"formatVersion", "containerIdentifier", "environment", "capturedAt", "recordTypes"}
    if not isinstance(value, dict) or set(value) != required or value.get("formatVersion") != "1.0.0":
        raise SchemaCompatibilityError("schema-capture-invalid")
    if value.get("environment") != expected_environment or value["environment"] not in ALLOWED_ENVIRONMENTS:
        raise SchemaCompatibilityError("schema-environment-invalid")
    if not isinstance(value.get("containerIdentifier"), str) or not value["containerIdentifier"].startswith("iCloud."):
        raise SchemaCompatibilityError("schema-container-invalid")
    if not isinstance(value.get("capturedAt"), str) or not value["capturedAt"].endswith("Z"):
        raise SchemaCompatibilityError("schema-capture-time-invalid")
    if not isinstance(value.get("recordTypes"), dict):
        raise SchemaCompatibilityError("schema-record-types-invalid")
    for record_name, record in value["recordTypes"].items():
        if not isinstance(record_name, str) or not record_name or not isinstance(record, dict) or set(record) != {"fields", "indexes"}:
            raise SchemaCompatibilityError("schema-record-type-invalid")
        if not isinstance(record["fields"], dict) or not isinstance(record["indexes"], list):
            raise SchemaCompatibilityError("schema-record-type-invalid")
        if len(record["indexes"]) != len(set(record["indexes"])) or any(not isinstance(item, str) or not item for item in record["indexes"]):
            raise SchemaCompatibilityError("schema-index-invalid")
        for field_name, field in record["fields"].items():
            if not isinstance(field_name, str) or not field_name or not isinstance(field, dict) or set(field) != {"type", "required"}:
                raise SchemaCompatibilityError("schema-field-invalid")
            if not isinstance(field["type"], str) or not field["type"] or not isinstance(field["required"], bool):
                raise SchemaCompatibilityError("schema-field-invalid")
    value["sha256"] = hashlib.sha256(raw).hexdigest()
    return value


def compare_schemas(
    development_path: Path,
    production_path: Path,
    *,
    disposition: str,
) -> dict[str, Any]:
    """Return a raw comparison report or raise for incompatible captures."""

    if disposition not in ALLOWED_DISPOSITIONS:
        raise SchemaCompatibilityError("schema-disposition-required")
    development = _load(development_path, "Development")
    production = _load(production_path, "Production")
    same_container = development["containerIdentifier"] == production["containerIdentifier"]
    if same_container != (disposition == "same-container"):
        raise SchemaCompatibilityError("schema-container-disposition-mismatch")

    incompatibilities: list[str] = []
    additions: list[str] = []
    development_types = development["recordTypes"]
    for record_name, production_record in production["recordTypes"].items():
        development_record = development_types.get(record_name)
        if not isinstance(development_record, dict):
            incompatibilities.append(f"record-removed:{record_name}")
            continue
        for field_name, production_field in production_record["fields"].items():
            development_field = development_record["fields"].get(field_name)
            if development_field != production_field:
                incompatibilities.append(f"field-changed-or-removed:{record_name}.{field_name}")
        missing_indexes = sorted(set(production_record["indexes"]) - set(development_record["indexes"]))
        incompatibilities.extend(f"index-removed:{record_name}.{name}" for name in missing_indexes)

    for record_name, development_record in development_types.items():
        production_record = production["recordTypes"].get(record_name)
        if production_record is None:
            additions.append(f"record-added:{record_name}")
            continue
        for field_name, field in development_record["fields"].items():
            if field_name not in production_record["fields"]:
                if field["required"]:
                    incompatibilities.append(f"required-field-added:{record_name}.{field_name}")
                else:
                    additions.append(f"field-added:{record_name}.{field_name}")
        additions.extend(
            f"index-added:{record_name}.{name}"
            for name in sorted(set(development_record["indexes"]) - set(production_record["indexes"]))
        )
    report = {
        "formatVersion": "1.0.0",
        "disposition": disposition,
        "developmentSha256": development["sha256"],
        "productionSha256": production["sha256"],
        "additions": sorted(additions),
        "incompatibilities": sorted(incompatibilities),
    }
    if incompatibilities:
        raise SchemaCompatibilityError("schema-incompatible:" + ",".join(sorted(incompatibilities)))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("development", type=Path)
    parser.add_argument("production", type=Path)
    parser.add_argument("--disposition", required=True, choices=sorted(ALLOWED_DISPOSITIONS))
    args = parser.parse_args(argv)
    try:
        report = compare_schemas(args.development, args.production, disposition=args.disposition)
    except SchemaCompatibilityError as exc:
        print(f"BLOCKED {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
