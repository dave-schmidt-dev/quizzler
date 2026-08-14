#!/usr/bin/env python3
"""Offline additive-only CloudKit schema comparator tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloudkit_schema_compatibility import (  # noqa: E402
    SchemaCompatibilityError,
    compare_schemas,
)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
