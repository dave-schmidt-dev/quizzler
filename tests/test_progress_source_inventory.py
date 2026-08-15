"""Fail-closed contract tests for the local progress-source inventory."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".state" / "progress-source-inventory.example.json"
LOCAL = ROOT / ".state" / "progress-source-inventory.json"
PATHS = {"one_source", "multi_source", "new_start"}
REQUIRED = {"schema_version", "path", "approval", "counts", "scope"}
ATTESTATION_REQUIRED = {"kind", "reference"}
SESSION_REFERENCE = re.compile(r"^[0-9a-f]{64}$")


def validate_inventory(value):
    """Validate the deliberately small, fail-closed inventory contract."""
    if not isinstance(value, dict) or set(value) != REQUIRED:
        raise ValueError("inventory keys must match the schema exactly")
    if value["schema_version"] != 1:
        raise ValueError("unsupported schema version")
    path = value["path"]
    if not isinstance(path, str) or path not in PATHS:
        raise ValueError("path must be exactly one terminal enum value")
    approval = value["approval"]
    if not isinstance(approval, dict) or set(approval) != {"approved", "disposition", "attestation"}:
        raise ValueError("explicit approval, disposition, and attestation are required")
    if approval["approved"] is not True or not isinstance(approval["disposition"], str) or not approval["disposition"].strip():
        raise ValueError("approval must be explicit and affirmative")
    attestation = approval["attestation"]
    if not isinstance(attestation, dict) or set(attestation) != ATTESTATION_REQUIRED:
        raise ValueError("attended approval requires a local session attestation")
    if attestation["kind"] != "local_session_ref" or not isinstance(attestation["reference"], str) or not SESSION_REFERENCE.fullmatch(attestation["reference"]):
        raise ValueError("attestation must contain an opaque 64-hex local session reference")
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != {"sources", "records"}:
        raise ValueError("counts must contain only sources and records")
    if any(type(counts[key]) is not int or counts[key] < 0 for key in counts):
        raise ValueError("counts must be non-negative integers")
    if path == "new_start" and counts != {"sources": 0, "records": 0}:
        raise ValueError("new_start requires zero recovered sources and records")
    scope = value["scope"]
    if not isinstance(scope, dict) or set(scope) != {"active_pack_ids"}:
        raise ValueError("scope must contain only active_pack_ids")
    if not isinstance(scope["active_pack_ids"], list) or not all(isinstance(item, str) and item for item in scope["active_pack_ids"]):
        raise ValueError("active_pack_ids must be a list of non-empty strings")


def load(path):
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    validate_inventory(value)
    return value


class ProgressSourceInventoryTests(unittest.TestCase):
    def test_tracked_example_is_not_an_attended_inventory(self):
        with self.assertRaises(ValueError):
            load(EXAMPLE)

    def test_fixture_copy_is_rejected_but_attended_local_inventory_passes(self):
        fixture_copy = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            validate_inventory(fixture_copy)
        local = load(LOCAL)
        self.assertNotEqual(
            local["approval"]["attestation"],
            fixture_copy["approval"]["attestation"],
        )

    def test_local_inventory_is_new_start_for_unstarted_cissp(self):
        inventory = load(LOCAL)
        self.assertEqual(inventory["path"], "new_start")
        self.assertEqual(inventory["counts"], {"sources": 0, "records": 0})
        self.assertEqual(inventory["scope"]["active_pack_ids"], ["cissp"])

    def test_unknown_or_multiple_or_missing_path_is_rejected(self):
        valid = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        for bad in ("unknown", ["new_start", "one_source"], None):
            candidate = dict(valid)
            candidate["path"] = bad
            with self.subTest(path=bad), self.assertRaises(ValueError):
                validate_inventory(candidate)
        candidate = dict(valid)
        del candidate["path"]
        with self.assertRaises(ValueError):
            validate_inventory(candidate)

    def test_new_start_requires_approval_disposition_and_zero_counts(self):
        valid = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        for change in (
            {"approval": {"approved": False, "disposition": ""}},
            {"approval": {"approved": True}},
            {"approval": {"approved": True, "disposition": "ok", "attestation": {"kind": "local_session_ref", "reference": "not-a-reference"}}},
            {"counts": {"sources": 1, "records": 0}},
        ):
            candidate = json.loads(LOCAL.read_text(encoding="utf-8"))
            candidate.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_inventory(candidate)

    def test_local_inventory_is_gitignored(self):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(LOCAL.relative_to(ROOT))],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
