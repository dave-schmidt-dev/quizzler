"""The ``pack_quarantine.py`` CLI round-trips a pack and refuses bad input.

Run from the project root::

    python3 -m unittest tests.test_pack_quarantine_cli -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pack_quarantine as pq  # noqa: E402


class PackQuarantineCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        course = Path(temp.name) / "course-a"
        course.mkdir()
        self.pack = course / "ch01.json"
        self.authored = {
            "pack_id": "demo-core", "subject": "Demo", "title": "Core", "version": 1,
            "questions": [{"id": f"q{i}", "prompt": f"P{i}"} for i in (1, 2, 3)],
        }
        self.pack.write_text(json.dumps(self.authored, indent=2), encoding="utf-8")

    def run_cli(self, *argv: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return pq.main(list(argv))

    def test_quarantine_then_restore_round_trips_the_pack(self) -> None:
        self.assertEqual(self.run_cli("quarantine", "--pack", str(self.pack),
                                      "--qid", "q2", "--reason", "ambiguous stem"), 0)
        partial = json.loads(self.pack.read_text())
        self.assertEqual(partial[pq.MARKER_KEY]["quarantined_ids"], ["q2"])
        self.assertTrue(pq.sidecar_path(self.pack).exists())

        self.assertEqual(self.run_cli("restore", "--pack", str(self.pack)), 0)
        self.assertEqual(json.loads(self.pack.read_text()), self.authored)
        self.assertFalse(pq.sidecar_path(self.pack).exists())

    def test_refusals_exit_nonzero_and_leave_the_pack_untouched(self) -> None:
        before = self.pack.read_bytes()
        self.assertEqual(self.run_cli("quarantine", "--pack", str(self.pack),
                                      "--qid", "missing", "--reason", "x"), 1)
        self.assertEqual(self.run_cli("restore", "--pack", str(self.pack)), 1)
        self.assertEqual(self.pack.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
