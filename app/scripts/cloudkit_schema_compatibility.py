#!/usr/bin/env python3
"""Compare captured CloudKit schemas offline using an additive-only policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


class SchemaCompatibilityError(ValueError):
    """A stable schema-capture or compatibility rejection."""


ALLOWED_DISPOSITIONS = {"same-container", "new-container"}
ALLOWED_ENVIRONMENTS = {"Development", "Production"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SCHEMA_TYPES = {
    "ASSET",
    "BYTES",
    "DOUBLE",
    "INT64",
    "LOCATION",
    "NUMBER",
    "REFERENCE",
    "STRING",
    "TIMESTAMP",
    "UUID",
}
_FIELD_INDEXES = {"QUERYABLE", "SEARCHABLE", "SORTABLE"}
_SYSTEM_FIELDS = {
    "___createTime": "TIMESTAMP",
    "___createdBy": "REFERENCE",
    "___etag": "STRING",
    "___modTime": "TIMESTAMP",
    "___modifiedBy": "REFERENCE",
    "___recordID": "REFERENCE",
}


class _SchemaToken:
    def __init__(self, value: str, *, quoted: bool = False) -> None:
        self.value = value
        self.quoted = quoted

    def __repr__(self) -> str:
        return f"_SchemaToken({self.value!r}, quoted={self.quoted})"


def _schema_tokens(raw: str) -> list[_SchemaToken]:
    """Tokenize the small, comment-bearing CloudKit schema language subset."""

    tokens: list[_SchemaToken] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character.isspace():
            index += 1
            continue
        if raw.startswith("//", index) or raw.startswith("--", index):
            newline = raw.find("\n", index + 2)
            index = len(raw) if newline < 0 else newline + 1
            continue
        if raw.startswith("/*", index):
            end = raw.find("*/", index + 2)
            if end < 0:
                raise SchemaCompatibilityError("schema-ddl-unsupported:unterminated-comment")
            index = end + 2
            continue
        if character == '"':
            end = index + 1
            value: list[str] = []
            while end < len(raw):
                if raw[end] == '"':
                    break
                if raw[end] == "\\":
                    raise SchemaCompatibilityError("schema-ddl-unsupported:escaped-identifier")
                value.append(raw[end])
                end += 1
            if end >= len(raw):
                raise SchemaCompatibilityError("schema-ddl-unsupported:unterminated-identifier")
            if not value:
                raise SchemaCompatibilityError("schema-ddl-invalid:empty-identifier")
            tokens.append(_SchemaToken("".join(value), quoted=True))
            index = end + 1
            continue
        if character in "(),;<>":
            tokens.append(_SchemaToken(character))
            index += 1
            continue
        end = index
        while end < len(raw) and (raw[end].isalnum() or raw[end] == "_"):
            end += 1
        if end == index:
            raise SchemaCompatibilityError(f"schema-ddl-unsupported:character-{character!r}")
        tokens.append(_SchemaToken(raw[index:end]))
        index = end
    return tokens


class _SchemaParser:
    def __init__(self, tokens: list[_SchemaToken]) -> None:
        self.tokens = tokens
        self.index = 0

    def _peek(self) -> _SchemaToken | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> _SchemaToken:
        token = self._peek()
        if token is None:
            raise SchemaCompatibilityError("schema-ddl-invalid:unexpected-end")
        if expected is not None and token.value.upper() != expected:
            raise SchemaCompatibilityError(f"schema-ddl-invalid:expected-{expected}")
        self.index += 1
        return token

    def _identifier(self) -> str:
        token = self._take()
        if not token.quoted and not _IDENTIFIER_RE.fullmatch(token.value):
            raise SchemaCompatibilityError("schema-ddl-invalid:identifier")
        return token.value

    def parse(self) -> dict[str, dict[str, Any]]:
        self._take("DEFINE")
        self._take("SCHEMA")
        record_types: dict[str, dict[str, Any]] = {}
        while self._peek() is not None:
            self._take("RECORD")
            self._take("TYPE")
            record_name = self._identifier()
            if record_name in record_types:
                raise SchemaCompatibilityError("schema-ddl-invalid:duplicate-record-type")
            self._take("(")
            fields, indexes = self._record_body()
            self._take(")")
            self._take(";")
            record_types[record_name] = {"fields": fields, "indexes": sorted(indexes)}
        if not record_types:
            raise SchemaCompatibilityError("schema-ddl-invalid:no-record-types")
        return record_types

    def _record_body(self) -> tuple[dict[str, dict[str, Any]], set[str]]:
        fields: dict[str, dict[str, Any]] = {}
        indexes: set[str] = set()
        if self._peek() and self._peek().value == ")":
            raise SchemaCompatibilityError("schema-ddl-invalid:empty-record-type")
        while True:
            token = self._peek()
            if token is None:
                raise SchemaCompatibilityError("schema-ddl-invalid:unterminated-record-type")
            if not token.quoted and token.value.upper() == "GRANT":
                self._grant()
            else:
                field_name = self._identifier()
                if field_name in fields:
                    raise SchemaCompatibilityError("schema-ddl-invalid:duplicate-field")
                field_type = self._field_type()
                field_indexes: list[str] = []
                while self._peek() is not None and self._peek().value.upper() in _FIELD_INDEXES:
                    option = self._take().value.upper()
                    field_indexes.append(f"{field_name}-{option.lower()}")
                if self._peek() is None or self._peek().value not in {",", ")"}:
                    raise SchemaCompatibilityError("schema-ddl-unsupported:field-option")
                expected_system_type = _SYSTEM_FIELDS.get(field_name)
                if expected_system_type is not None and field_type != expected_system_type:
                    raise SchemaCompatibilityError("schema-ddl-invalid:system-field-type")
                fields[field_name] = {
                    "type": field_type,
                    # CloudKit Schema Language has no custom required marker.
                    # Its implicit system fields are always present; custom
                    # fields are therefore represented as optional in the
                    # comparator's capture contract.
                    "required": field_name in _SYSTEM_FIELDS,
                }
                for field_index in field_indexes:
                    if field_index in indexes:
                        raise SchemaCompatibilityError("schema-ddl-invalid:duplicate-index")
                    indexes.add(field_index)
            if self._peek() and self._peek().value == ",":
                self._take(",")
                continue
            if self._peek() and self._peek().value == ")":
                return fields, indexes
            raise SchemaCompatibilityError("schema-ddl-invalid:expected-comma")

    def _field_type(self) -> str:
        type_name = self._take().value.upper()
        if type_name == "LIST":
            self._take("<")
            element = self._field_type()
            self._take(">")
            return f"LIST<{element}>"
        if type_name not in _SCHEMA_TYPES:
            raise SchemaCompatibilityError("schema-ddl-unsupported:data-type")
        if type_name == "NUMBER" and self._peek() and self._peek().value.upper() == "PREFERRED":
            self._take("PREFERRED")
            self._take("AS")
            preferred = self._take().value.upper()
            if preferred not in {"INT64", "DOUBLE"}:
                raise SchemaCompatibilityError("schema-ddl-unsupported:preferred-type")
            return f"NUMBER PREFERRED AS {preferred}"
        return type_name

    def _grant(self) -> None:
        self._take("GRANT")
        permission = self._take().value.upper()
        if permission not in {"READ", "CREATE", "WRITE"}:
            raise SchemaCompatibilityError("schema-ddl-unsupported:grant")
        while self._peek() and self._peek().value == ",":
            self._take(",")
            permission = self._take().value.upper()
            if permission not in {"READ", "CREATE", "WRITE"}:
                raise SchemaCompatibilityError("schema-ddl-unsupported:grant")
        self._take("TO")
        role = self._identifier()
        if role not in {"_creator", "_world", "_icloud"}:
            raise SchemaCompatibilityError("schema-ddl-unsupported:grant-role")


def normalize_cktool_schema(
    raw: bytes | str,
    *,
    container_identifier: str,
    environment: str,
    captured_at: str,
) -> dict[str, Any]:
    """Normalize raw ``cktool export-schema`` bytes into a capture document."""

    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    if not isinstance(raw_bytes, bytes):
        raise SchemaCompatibilityError("schema-capture-unreadable")
    if environment not in ALLOWED_ENVIRONMENTS:
        raise SchemaCompatibilityError("schema-environment-invalid")
    if not isinstance(container_identifier, str) or not container_identifier.startswith("iCloud."):
        raise SchemaCompatibilityError("schema-container-invalid")
    if not isinstance(captured_at, str) or not captured_at.endswith("Z"):
        raise SchemaCompatibilityError("schema-capture-time-invalid")
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaCompatibilityError("schema-ddl-unreadable") from exc
    record_types = _SchemaParser(_schema_tokens(text)).parse()
    return {
        "formatVersion": "1.0.0",
        "containerIdentifier": container_identifier,
        "environment": environment,
        "capturedAt": captured_at,
        "sourceSha256": hashlib.sha256(raw_bytes).hexdigest(),
        "recordTypes": record_types,
    }


# Stable descriptive alias for callers that do not mention cktool explicitly.
normalize_schema_capture = normalize_cktool_schema


def write_normalized_capture(
    raw_path: Path,
    output_path: Path,
    *,
    container_identifier: str,
    environment: str,
    captured_at: str,
) -> dict[str, Any]:
    """Read one local raw export and write its normalized JSON capture."""

    try:
        raw = raw_path.read_bytes()
    except OSError as exc:
        raise SchemaCompatibilityError("schema-capture-unreadable") from exc
    capture = normalize_cktool_schema(
        raw,
        container_identifier=container_identifier,
        environment=environment,
        captured_at=captured_at,
    )
    try:
        output_path.write_text(json.dumps(capture, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SchemaCompatibilityError("schema-capture-write-failed") from exc
    return capture


def _load(path: Path, expected_environment: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaCompatibilityError("schema-capture-unreadable") from exc
    required = {"formatVersion", "containerIdentifier", "environment", "capturedAt", "recordTypes"}
    allowed = required | {"sourceSha256"}
    if (
        not isinstance(value, dict)
        or not set(value).issubset(allowed)
        or not required.issubset(value)
        or value.get("formatVersion") != "1.0.0"
    ):
        raise SchemaCompatibilityError("schema-capture-invalid")
    if value.get("environment") != expected_environment or value["environment"] not in ALLOWED_ENVIRONMENTS:
        raise SchemaCompatibilityError("schema-environment-invalid")
    if not isinstance(value.get("containerIdentifier"), str) or not value["containerIdentifier"].startswith("iCloud."):
        raise SchemaCompatibilityError("schema-container-invalid")
    if not isinstance(value.get("capturedAt"), str) or not value["capturedAt"].endswith("Z"):
        raise SchemaCompatibilityError("schema-capture-time-invalid")
    if not isinstance(value.get("recordTypes"), dict):
        raise SchemaCompatibilityError("schema-record-types-invalid")
    if "sourceSha256" in value and (
        not isinstance(value["sourceSha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["sourceSha256"])
    ):
        raise SchemaCompatibilityError("schema-source-hash-invalid")
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
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "normalize":
        parser = argparse.ArgumentParser(description="Normalize a local cktool schema export offline.")
        parser.add_argument("raw", type=Path, help="Raw cktool export-schema output")
        parser.add_argument("--output", "-o", type=Path, required=True, help="Normalized JSON capture path")
        parser.add_argument(
            "--container-identifier",
            "--container-id",
            dest="container_identifier",
            required=True,
        )
        parser.add_argument("--environment", required=True, choices=sorted(ALLOWED_ENVIRONMENTS))
        parser.add_argument("--captured-at", required=True)
        args = parser.parse_args(argv[1:])
        try:
            capture = write_normalized_capture(
                args.raw,
                args.output,
                container_identifier=args.container_identifier,
                environment=args.environment,
                captured_at=args.captured_at,
            )
        except SchemaCompatibilityError as exc:
            print(f"BLOCKED {exc}", file=sys.stderr)
            return 2
        print(json.dumps(capture, sort_keys=True, separators=(",", ":")))
        return 0

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
