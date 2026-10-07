"""L29 accepts a well-formed ``partial_install`` marker and refuses a bad one.

The marker is written by ``scripts/pack_quarantine.py`` and read by the app,
so its shape is part of the native contract QuizzlerKit's
``PackManifest.validate()`` mirrors.

Run from the project root::

    python3 -m unittest tests.test_lint_partial_install -v
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lint_packs import check_l29_native_metadata_contract  # noqa: E402

DIGEST = "sha256:" + "a" * 64


def partial_pack() -> dict:
    return {
        "pack_id": "demo-core",
        "subject": "Demo",
        "title": "Core",
        "version": 1,
        "questions": [{"id": "q1"}, {"id": "q3"}],
        "partial_install": {
            "authored_count": 3,
            "installed_count": 2,
            "quarantined_ids": ["q2"],
            "record_digest": DIGEST,
        },
    }


def details(pack: dict) -> str:
    return " | ".join(f["detail"] for f in check_l29_native_metadata_contract(pack))


class PartialInstallContractTests(unittest.TestCase):
    def test_a_well_formed_marker_passes(self) -> None:
        self.assertEqual(check_l29_native_metadata_contract(partial_pack()), [])

    def test_a_pack_without_the_marker_is_unchanged(self) -> None:
        pack = partial_pack()
        del pack["partial_install"]
        self.assertEqual(check_l29_native_metadata_contract(pack), [])

    def test_each_malformed_marker_is_critical(self) -> None:
        cases = {
            "extra key": ("reason", "x"),
            "installed mismatch": ("installed_count", 3),
            "authored not above installed": ("authored_count", 2),
            "duplicate ids": ("quarantined_ids", ["q2", "q2"]),
            "still installed": ("quarantined_ids", ["q1"]),
            "count mismatch": ("quarantined_ids", ["q2", "q4"]),
            "bad digest": ("record_digest", "sha256:XYZ"),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name):
                pack = copy.deepcopy(partial_pack())
                pack["partial_install"][key] = value
                findings = check_l29_native_metadata_contract(pack)
                self.assertTrue(findings, name)
                self.assertTrue(all(f["severity"] == "critical" for f in findings))
                self.assertIn("partial_install" if name != "still installed"
                              else "still installed", details(pack))

    def test_a_non_object_marker_is_critical(self) -> None:
        pack = partial_pack()
        pack["partial_install"] = []
        self.assertIn("must be an object", details(pack))


if __name__ == "__main__":
    unittest.main()
