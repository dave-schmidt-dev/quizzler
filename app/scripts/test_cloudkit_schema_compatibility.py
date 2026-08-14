#!/usr/bin/env python3
"""Offline additive-only CloudKit schema comparator tests."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloudkit_schema_compatibility import (  # noqa: E402
    SchemaCompatibilityError,
    compare_schemas,
    normalize_cktool_schema,
)


RAW_CKTOOL_SCHEMA = """DEFINE SCHEMA

    RECORD TYPE DevelopmentProbe (
        \"___createTime\" TIMESTAMP,
        \"___createdBy\" REFERENCE,
        \"___etag\" STRING,
        \"___modTime\" TIMESTAMP,
        \"___modifiedBy\" REFERENCE,
        \"___recordID\" REFERENCE QUERYABLE,
        status STRING QUERYABLE SEARCHABLE SORTABLE,
        GRANT WRITE TO \"_creator\",
        GRANT CREATE TO \"_icloud\",
        GRANT READ TO \"_world\"
    );

    RECORD TYPE Users (
        \"___createTime\" TIMESTAMP,
        \"___createdBy\" REFERENCE,
        \"___etag\" STRING,
        \"___modTime\" TIMESTAMP,
        \"___modifiedBy\" REFERENCE,
        \"___recordID\" REFERENCE,
        roles LIST<INT64>,
        GRANT WRITE TO \"_creator\",
        GRANT READ TO \"_world\"
    );
"""


def schema(container: str, environment: str) -> dict:
    return {
        "formatVersion": "1.0.0",
        "containerIdentifier": container,
        "environment": environment,
        "capturedAt": "2026-08-14T12:00:00Z",
        "recordTypes": {
            "Progress": {
                "fields": {"revision": {"type": "INT64", "required": True}},
                "indexes": ["revision-queryable"],
            }
        },
    }


class CloudKitSchemaCompatibilityTests(unittest.TestCase):
    def compare(self, development: dict, production: dict, disposition: str = "same-container") -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dev = root / "development.json"
            prod = root / "production.json"
            dev.write_text(json.dumps(development), encoding="utf-8")
            prod.write_text(json.dumps(production), encoding="utf-8")
            return compare_schemas(dev, prod, disposition=disposition)

    def test_optional_field_and_index_are_additive(self) -> None:
        production = schema("iCloud.example", "Production")
        development = schema("iCloud.example", "Development")
        development["recordTypes"]["Progress"]["fields"]["note"] = {"type": "STRING", "required": False}
        development["recordTypes"]["Progress"]["indexes"].append("note-queryable")
        report = self.compare(development, production)
        self.assertEqual(report["disposition"], "same-container")
        self.assertEqual(len(report["additions"]), 2)

    def test_removed_changed_and_new_required_fields_fail(self) -> None:
        production = schema("iCloud.example", "Production")
        for mutation in ("remove", "change", "required"):
            development = schema("iCloud.example", "Development")
            if mutation == "remove":
                development["recordTypes"]["Progress"]["fields"].pop("revision")
            elif mutation == "change":
                development["recordTypes"]["Progress"]["fields"]["revision"]["type"] = "STRING"
            else:
                development["recordTypes"]["Progress"]["fields"]["new"] = {"type": "STRING", "required": True}
            with self.subTest(mutation=mutation), self.assertRaisesRegex(SchemaCompatibilityError, "schema-incompatible"):
                self.compare(development, production)

    def test_different_container_requires_explicit_new_container_disposition(self) -> None:
        development = schema("iCloud.example.dev", "Development")
        production = schema("iCloud.example", "Production")
        with self.assertRaisesRegex(SchemaCompatibilityError, "schema-container-disposition-mismatch"):
            self.compare(development, production, "same-container")
        report = self.compare(development, production, "new-container")
        self.assertEqual(report["disposition"], "new-container")

    def test_normalizes_observed_cktool_record_type_fixture(self) -> None:
        raw = RAW_CKTOOL_SCHEMA.encode("utf-8")
        capture = normalize_cktool_schema(
            raw,
            container_identifier="iCloud.com.zerodelta.quizzler",
            environment="Development",
            captured_at="2026-08-14T12:00:00Z",
        )
        self.assertEqual(capture["sourceSha256"], hashlib.sha256(raw).hexdigest())
        record = capture["recordTypes"]["DevelopmentProbe"]
        self.assertEqual(record["fields"]["status"], {"type": "STRING", "required": False})
        self.assertEqual(record["fields"]["___recordID"], {"type": "REFERENCE", "required": True})
        self.assertEqual(
            record["indexes"],
            ["___recordID-queryable", "status-queryable", "status-searchable", "status-sortable"],
        )
        self.assertEqual(
            capture["recordTypes"]["Users"]["fields"]["roles"],
            {"type": "LIST<INT64>", "required": False},
        )

    def test_rejects_unsupported_schema_grammar(self) -> None:
        with self.assertRaisesRegex(SchemaCompatibilityError, "schema-ddl-unsupported"):
            normalize_cktool_schema(
                RAW_CKTOOL_SCHEMA.replace("status STRING", "status STRING REQUIRED"),
                container_identifier="iCloud.com.zerodelta.quizzler",
                environment="Development",
                captured_at="2026-08-14T12:00:00Z",
            )

    def test_normalization_rejects_environment_mismatch(self) -> None:
        with self.assertRaisesRegex(SchemaCompatibilityError, "schema-environment-invalid"):
            normalize_cktool_schema(
                RAW_CKTOOL_SCHEMA,
                container_identifier="iCloud.com.zerodelta.quizzler",
                environment="development",
                captured_at="2026-08-14T12:00:00Z",
            )

    def test_source_hash_binds_exact_raw_bytes(self) -> None:
        first = normalize_cktool_schema(
            RAW_CKTOOL_SCHEMA,
            container_identifier="iCloud.com.zerodelta.quizzler",
            environment="Development",
            captured_at="2026-08-14T12:00:00Z",
        )
        second = normalize_cktool_schema(
            RAW_CKTOOL_SCHEMA + "\n",
            container_identifier="iCloud.com.zerodelta.quizzler",
            environment="Development",
            captured_at="2026-08-14T12:00:00Z",
        )
        self.assertNotEqual(first["sourceSha256"], second["sourceSha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
