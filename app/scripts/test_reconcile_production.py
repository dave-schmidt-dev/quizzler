#!/usr/bin/env python3
"""Focused raw Production reconciliation contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconcile_production import (  # noqa: E402
    ProductionReconciliationError,
    verify_production_evidence,
)
from sync_release_tool import DEFAULT_DESTINATION  # noqa: E402
from test_release_readiness import Fixture, NOW_TEXT  # noqa: E402


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json(path: Path, value: dict) -> None:
    path.write_bytes(canonical(value) + b"\n")


def production_document(fixture: Fixture) -> dict:
    fields = {
        "automaticSyncObservationSha256": "a" * 64,
        "explicitFetchObservationSha256": "b" * 64,
        "issueObservationSha256": "c" * 64,
        "migrationObservationSha256": "d" * 64,
    }
    return {
        "formatVersion": "2.0.0",
        "candidateId": "1.2.3-17",
        "marketingVersion": "1.2.3",
        "buildNumber": "17",
        "gitRevision": "head-a",
        "sourceDigest": fixture.source_digest,
        "capturedAt": NOW_TEXT,
        "environment": "Production",
        "containerIdentifier": "iCloud.com.zerodelta.quizzler",
        "fields": fields,
        "canonicalStateSha256": hashlib.sha256(canonical(fields)).hexdigest(),
    }


class ProductionReconciliationTests(unittest.TestCase):
    def test_reconciles_raw_hashes_and_declared_candidate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            evidence = fixture.root / "evidence" / "production.json"
            write_json(evidence, production_document(fixture))
            statuses: list[str] = []
            report = verify_production_evidence(
                fixture.manifest,
                evidence,
                repository_root=fixture.root,
                runtime=DEFAULT_DESTINATION,
                on_status=statuses.append,
            )
            self.assertEqual(report["decision"], "reconciled")
            self.assertEqual(statuses[0], "production-reconciliation-started")
            self.assertEqual(statuses[-1], "production-reconciliation-complete")

    def test_rejects_identity_environment_hash_and_fake_pass_claims(self) -> None:
        mutations = (
            (lambda value: value.__setitem__("candidateId", "other"), "production-identity-mismatch"),
            (lambda value: value.__setitem__("environment", "Development"), "production-identity-mismatch"),
            (lambda value: value.__setitem__("canonicalStateSha256", "e" * 64), "production-canonical-hash-mismatch"),
            (lambda value: value["fields"].__setitem__("passed", True), "editable-pass-flag-forbidden"),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture(Path(temporary))
                evidence = fixture.root / "evidence" / "production.json"
                value = production_document(fixture)
                mutate(value)
                write_json(evidence, value)
                with self.assertRaisesRegex(ProductionReconciliationError, expected):
                    verify_production_evidence(
                        fixture.manifest, evidence, repository_root=fixture.root, runtime=DEFAULT_DESTINATION
                    )

    def test_cli_emits_status_for_reconciliation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            evidence = fixture.root / "evidence" / "production.json"
            write_json(evidence, production_document(fixture))
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("reconcile_production.py")),
                    "--candidate",
                    str(fixture.manifest),
                    "--evidence",
                    str(evidence),
                    "--repository",
                    str(fixture.root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("STATUS production-reconciliation-started", result.stderr)
            self.assertIn('"decision":"reconciled"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
