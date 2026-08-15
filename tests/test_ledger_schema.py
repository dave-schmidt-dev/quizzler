"""Regression tests for the machine-readable recurrence ledger.

Run from the project root::

    python3 -m unittest tests.test_ledger_schema -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARVEST_ROOT = Path.home() / ".agent"
if str(HARVEST_ROOT) not in sys.path:
    sys.path.insert(0, str(HARVEST_ROOT))

from harvest.ledger_io import Ledger, load_ledger  # noqa: E402


LEDGER_PATH = PROJECT_ROOT / "ledger.yaml"
RUN_SLUG = "quizzler-review-remediation-2026-07-08"
ORIGINAL_RUN_DATE = "2026-07-08"
TOUCHED_INVARIANTS = {"INV-1", "INV-2", "INV-4", "INV-5", "INV-6"}
RESOLUTION_DATES = {
    "INV-1": "2026-08-15",
    "INV-2": "2026-07-08",
    "INV-4": "2026-07-08",
    "INV-5": "2026-07-08",
    "INV-6": "2026-07-08",
    "INV-7": "2026-08-15",
}
FUTURE_PACK_BASELINE_NOTE = (
    "future-pack baseline; archived findings are out of scope; post-baseline "
    "development/remediation entries audited against the active strict manifest, "
    "which passed for samples and CISSP"
)


class LedgerSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8"))

    def test_resolutions_are_keyed_by_invariant_id(self):
        resolutions = self.data["resolutions"]
        self.assertEqual(set(resolutions), TOUCHED_INVARIANTS | {"INV-7"})
        self.assertNotIn(RUN_SLUG, resolutions)
        self.assertEqual(resolutions["INV-7"]["note"], FUTURE_PACK_BASELINE_NOTE)

    def test_every_resolution_has_a_cutoff_date(self):
        for inv_id, resolution in self.data["resolutions"].items():
            with self.subTest(inv_id=inv_id):
                expected = RESOLUTION_DATES.get(inv_id)
                self.assertIsNotNone(
                    expected,
                    f"add an expected resolution date for {inv_id}",
                )
                self.assertEqual(resolution["resolved_at_date"], expected)

    def test_original_run_record_is_preserved_outside_resolutions(self):
        run = self.data["runs"][RUN_SLUG]
        self.assertEqual(str(run["date"]), ORIGINAL_RUN_DATE)
        self.assertEqual(set(run["invariants_touched"]), TOUCHED_INVARIANTS)
        self.assertEqual(run["findings_resolved"], [f"F{i}" for i in range(1, 10)])

    def test_load_ledger_accepts_rekeyed_schema(self):
        ledger = load_ledger(LEDGER_PATH)
        self.assertIsInstance(ledger, Ledger)
        self.assertEqual(ledger.resolved_after("INV-1"), RESOLUTION_DATES["INV-1"])
        self.assertEqual(ledger.resolved_after("INV-7"), RESOLUTION_DATES["INV-7"])


if __name__ == "__main__":
    unittest.main()
