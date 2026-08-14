#!/usr/bin/env python3
"""Offline additive-only CloudKit schema comparator tests."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloudkit_schema_compatibility import (  # noqa: E402
    SchemaCompatibilityError,
    compare_schemas,
    main,
    normalize_cktool_schema,
    write_normalized_capture,
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
    def normalize(self, raw: bytes | str) -> dict:
        return normalize_cktool_schema(
            raw,
            container_identifier="iCloud.com.zerodelta.quizzler.dev",
            environment="Development",
            captured_at="2026-08-14T12:00:00Z",
        )

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
        capture = self.normalize(raw)
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
                container_identifier="iCloud.com.zerodelta.quizzler.dev",
                environment="Development",
                captured_at="2026-08-14T12:00:00Z",
            )

    def test_normalization_rejects_environment_mismatch(self) -> None:
        with self.assertRaisesRegex(SchemaCompatibilityError, "schema-environment-invalid"):
            normalize_cktool_schema(
                RAW_CKTOOL_SCHEMA,
                container_identifier="iCloud.com.zerodelta.quizzler.dev",
                environment="development",
                captured_at="2026-08-14T12:00:00Z",
            )

    def test_source_hash_binds_exact_raw_bytes(self) -> None:
        first = self.normalize(RAW_CKTOOL_SCHEMA)
        second = self.normalize(RAW_CKTOOL_SCHEMA + "\n")
        self.assertNotEqual(first["sourceSha256"], second["sourceSha256"])

    def test_quoted_grant_field_is_not_parsed_as_a_grant(self) -> None:
        capture = self.normalize(
            '''DEFINE SCHEMA
            RECORD TYPE QuotedFields (
                "GRANT" STRING,
                GRANT READ TO "_world"
            );
            '''
        )
        self.assertEqual(
            capture["recordTypes"]["QuotedFields"]["fields"]["GRANT"],
            {"type": "STRING", "required": False},
        )

    def test_malformed_schema_errors_are_stable(self) -> None:
        cases = {
            "duplicate-record": """DEFINE SCHEMA
                RECORD TYPE Progress (value STRING);
                RECORD TYPE Progress (value STRING);
            """,
            "duplicate-field": """DEFINE SCHEMA
                RECORD TYPE Progress (value STRING, value INT64);
            """,
            "system-field-type": """DEFINE SCHEMA
                RECORD TYPE Progress ("___recordID" STRING);
            """,
            "invalid-grant": """DEFINE SCHEMA
                RECORD TYPE Progress (GRANT DELETE TO "_world");
            """,
            "unterminated-comment": "/* missing close",
        }
        expected = {
            "duplicate-record": "schema-ddl-invalid:duplicate-record-type",
            "duplicate-field": "schema-ddl-invalid:duplicate-field",
            "system-field-type": "schema-ddl-invalid:system-field-type",
            "invalid-grant": "schema-ddl-unsupported:grant",
            "unterminated-comment": "schema-ddl-unsupported:unterminated-comment",
        }
        for name, raw in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(SchemaCompatibilityError, expected[name]):
                self.normalize(raw)

    def test_write_normalized_capture_reports_input_and_output_failures(self) -> None:
        with self.assertRaisesRegex(SchemaCompatibilityError, "schema-ddl-unreadable"):
            self.normalize(b"\xff")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SchemaCompatibilityError, "schema-capture-unreadable"):
                write_normalized_capture(
                    root / "missing.cktool",
                    root / "capture.json",
                    container_identifier="iCloud.com.zerodelta.quizzler.dev",
                    environment="Development",
                    captured_at="2026-08-14T12:00:00Z",
                )
            raw_path = root / "schema.cktool"
            raw_path.write_text(RAW_CKTOOL_SCHEMA, encoding="utf-8")
            output_path = root / "existing-directory"
            output_path.mkdir()
            with self.assertRaisesRegex(SchemaCompatibilityError, "schema-capture-write-failed"):
                write_normalized_capture(
                    raw_path,
                    output_path,
                    container_identifier="iCloud.com.zerodelta.quizzler.dev",
                    environment="Development",
                    captured_at="2026-08-14T12:00:00Z",
                )

    def test_normalize_cli_returns_json_or_blocked_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "schema.cktool"
            output_path = root / "capture.json"
            raw_path.write_text(RAW_CKTOOL_SCHEMA, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "normalize",
                        str(raw_path),
                        "--output",
                        str(output_path),
                        "--container-identifier",
                        "iCloud.com.zerodelta.quizzler.dev",
                        "--environment",
                        "Development",
                        "--captured-at",
                        "2026-08-14T12:00:00Z",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                json.loads(stdout.getvalue())["sourceSha256"],
                json.loads(output_path.read_text())["sourceSha256"],
            )

            missing_stdout = io.StringIO()
            missing_stderr = io.StringIO()
            with contextlib.redirect_stdout(missing_stdout), contextlib.redirect_stderr(missing_stderr):
                blocked_code = main(
                    [
                        "normalize",
                        str(root / "missing.cktool"),
                        "--output",
                        str(root / "blocked.json"),
                        "--container-identifier",
                        "iCloud.com.zerodelta.quizzler.dev",
                        "--environment",
                        "Development",
                        "--captured-at",
                        "2026-08-14T12:00:00Z",
                    ]
                )
            self.assertEqual(blocked_code, 2)
            self.assertEqual(missing_stdout.getvalue(), "")
            self.assertIn("BLOCKED schema-capture-unreadable", missing_stderr.getvalue())

    def test_normalize_cli_rejects_missing_required_arguments(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["normalize", "schema.cktool"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--output", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
