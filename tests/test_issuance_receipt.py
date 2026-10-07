"""Issuance-receipt tests for campaign finalization (M5, finding 8).

After a successful replace, and still holding the M0c lock, the finalizer
appends a receipt to ``ledger["issuance_receipts"]`` binding the ledger to
the exact certification block it wrote for the certified frontier. The
receipt is a consistency binding, not an authentication tag: ledgers and
pack blocks stay unauthenticated JSON, so it blocks pipeline mistakes and
single-file forgery, not an attacker who can hand-write both files. No
reviewer or network call ever happens; the success path exercises the real
deterministic Layer A on a lint-clean fixture.

Run from the project root::

    python3 -m unittest tests.test_issuance_receipt -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"

_spec = importlib.util.spec_from_file_location("hybrid_verify", SCRIPT_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# The same module objects hybrid_verify uses, so patches land where
# certify_campaign actually looks its collaborators up.
cc = hv.certification_campaign
cs = sys.modules["campaign_snapshot"]

CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}

# See L29: a fixture pack the native decoder would refuse fails Layer A, which
# would mask the receipt behavior these tests isolate.
NATIVE_METADATA = {"subject": "Math", "title": "Receipt fixture", "version": 1}


def _pack_payload() -> dict:
    """Return a lint-clean single-question pack payload."""
    payload = {
        **NATIVE_METADATA,
        "pack_id": "ch01",
        "questions": [dict(CLEAN_Q)],
    }
    payload["coverage_blueprint"] = [{"topic": "math", "min": 1}]
    return payload


class _Base(unittest.TestCase):
    """A lint-clean pack plus the frozen ledger whose evidence covers it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.pack = self.tmp_path / "ch01.json"
        self.pack.write_text(json.dumps(_pack_payload()))

    def tearDown(self):
        self._tmp.cleanup()

    def _ledger(self) -> Path:
        """Return a saved ledger whose discovery evidence covers the pack.

        The ledger is frozen against the pack exactly as setUp wrote it, so
        eligibility passes and the finalizer reaches the stamp and receipt.
        """
        snapshot = cc.build_snapshot(self.pack)
        ledger = cc.new_ledger(snapshot)

        def pass_report():
            return {
                "ready": False, "outcome": "review_ok", "partial": False,
                "layer_a": {"live": []},
                "layer_c": {"live": [], "errors": [], "coverage_gaps": [],
                            "questions_unchecked": 0,
                            "total": len(snapshot["question_ids"])},
            }

        cc.record_hybrid_discovery(ledger, {
            "schema_version": hv.JSON_SCHEMA_VERSION, "certifying": False,
            "verifier_profile": snapshot["critic_contract"]["profile"],
            "snapshot_fingerprint": snapshot["fingerprint"],
            "advisory": {"exit_code": 3, "report": pass_report()},
            "verifier": {"exit_code": 3, "report": pass_report()}, "exit_code": 3,
        })
        path = self.tmp_path / "campaign.json"
        cc.save_ledger(path, ledger)
        return path


class IssuanceReceiptTests(_Base):
    """M5 (finding 8): the ledger records exactly what the finalizer issued."""

    def test_receipt_matches_the_written_block(self):
        """The receipt digests the certification block as written."""
        ledger = self._ledger()
        rc, _out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 0)
        # load_ledger revalidates the receipt shape on the way in.
        saved = cc.load_ledger(ledger)
        receipts = saved["issuance_receipts"]
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        block = json.loads(self.pack.read_text())["certification"]
        header = {key: value for key, value in block.items()
                  if key != "question_stamps"}
        self.assertEqual(receipt["certification_header_digest"],
                         cs._digest(header))
        self.assertEqual(receipt["certification_digest"],
                         cs._digest(block))
        self.assertEqual(receipt["question_stamps"],
                         block["question_stamps"])
        self.assertEqual(receipt["campaign_snapshot_fingerprint"],
                         block["provenance"]["campaign_snapshot_fingerprint"])
        issued_at = datetime.fromisoformat(receipt["issued_at"])
        self.assertEqual(issued_at.utcoffset(), timedelta(0))

    def test_failed_receipt_save_fails_closed(self):
        """A receipt that cannot be recorded fails the run after the stamp."""
        ledger = self._ledger()
        with patch.object(cc, "save_ledger", side_effect=OSError("disk full")):
            rc, out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 2)
        self.assertEqual(
            out,
            "stamp written; receipt not recorded; this campaign cannot "
            "be inherited")
        # The stamp is already on the pack and cannot be unwritten...
        self.assertIn("certification", json.loads(self.pack.read_text()))
        # ...but the ledger on disk never recorded the issuance.
        self.assertNotIn("issuance_receipts", json.loads(ledger.read_text()))

    def test_tampered_receipt_shape_fails_ledger_validation(self):
        """A hand-edited receipt is refused by the ledger validator."""
        ledger = self._ledger()
        rc, _out = hv.certify_campaign(self.pack, ledger)
        self.assertEqual(rc, 0)
        good = json.loads(ledger.read_text())
        # Positive control: the receipt as written validates.
        cc.load_ledger(ledger)

        def missing_field(value):
            del value["issuance_receipts"][0]["certification_digest"]

        def unknown_field(value):
            value["issuance_receipts"][0]["signature"] = "forged"

        def stamps_not_a_mapping(value):
            value["issuance_receipts"][0]["question_stamps"] = ["q1"]

        def digest_malformed(value):
            value["issuance_receipts"][0][
                "certification_header_digest"] = "md5:oops"

        def issued_at_not_utc(value):
            value["issuance_receipts"][0]["issued_at"] = "2026-10-06T00:00:00"

        def receipt_not_an_object(value):
            value["issuance_receipts"][0] = "forged"

        def receipts_not_a_list(value):
            value["issuance_receipts"] = {"q1": "sha256:" + "0" * 64}

        tampers = {
            "missing field": missing_field,
            "unknown field": unknown_field,
            "stamps not a mapping": stamps_not_a_mapping,
            "digest malformed": digest_malformed,
            "issued_at not utc": issued_at_not_utc,
            "receipt not an object": receipt_not_an_object,
            "receipts not a list": receipts_not_a_list,
        }
        for label, tamper in tampers.items():
            with self.subTest(tamper=label):
                value = json.loads(json.dumps(good))
                tamper(value)
                path = self.tmp_path / "tampered.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(cc.CampaignError):
                    cc.load_ledger(path)
        # The list stays optional: a receipt-less ledger still validates.
        cc.load_ledger(self._ledger())


if __name__ == "__main__":
    unittest.main()
