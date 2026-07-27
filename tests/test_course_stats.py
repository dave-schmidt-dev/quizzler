"""Unit tests for ``scripts/lint_packs.course_stats`` — course-level aggregate stats.

Tests the T/F balance and type-mix advisory findings produced by ``course_stats``,
plus the contract that all findings carry ``severity: "advisory"`` so
``severity_to_exit`` returns 0.

Run from the project root::

    python3 -m unittest tests.test_course_stats -v
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "lint_packs.py"

_spec = importlib.util.spec_from_file_location("lint_packs", SCRIPT_PATH)
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)


def _write_pack(course_dir: Path, name: str, questions: list[dict]) -> Path:
    """Write a pack JSON file into *course_dir*, returning its Path."""
    p = course_dir / name
    p.write_text(json.dumps({"pack_id": name, "questions": questions}))
    return p


class CourseStatsTfBalanceTests(unittest.TestCase):
    def test_skewed_tf_fires_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = []
            for i in range(10):
                tfs.append({
                    "id": f"tf{i}", "type": "true_false",
                    "prompt": f"Statement {i}", "answer": i < 8,
                })
            _write_pack(course, "pack.json", tfs)
            findings = lp.course_stats(course)
        l17b = [f for f in findings if f["rule"] == "L17b"]
        self.assertEqual(len(l17b), 1)
        self.assertEqual(l17b[0]["severity"], "advisory")
        self.assertIsNone(l17b[0]["qid"])
        self.assertIn("80% True", l17b[0]["detail"])

    def test_balanced_tf_no_finding(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = []
            for i in range(12):
                tfs.append({
                    "id": f"tf{i}", "type": "true_false",
                    "prompt": f"Statement {i}", "answer": i % 2 == 0,
                })
            _write_pack(course, "pack.json", tfs)
            findings = lp.course_stats(course)
        l17b = [f for f in findings if f["rule"] == "L17b"]
        self.assertEqual(l17b, [])

    def test_below_min_tf_no_finding(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = []
            for i in range(9):
                tfs.append({
                    "id": f"tf{i}", "type": "true_false",
                    "prompt": f"Statement {i}", "answer": True,
                })
            _write_pack(course, "pack.json", tfs)
            findings = lp.course_stats(course)
        l17b = [f for f in findings if f["rule"] == "L17b"]
        self.assertEqual(l17b, [])

    def test_tf_across_multiple_packs(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            _write_pack(course, "pack1.json", [
                {"id": "a1", "type": "true_false", "prompt": "A", "answer": True},
                {"id": "a2", "type": "true_false", "prompt": "B", "answer": True},
                {"id": "a3", "type": "true_false", "prompt": "C", "answer": True},
                {"id": "a4", "type": "true_false", "prompt": "D", "answer": True},
                {"id": "a5", "type": "true_false", "prompt": "E", "answer": True},
            ])
            _write_pack(course, "pack2.json", [
                {"id": "b1", "type": "true_false", "prompt": "F", "answer": False},
                {"id": "b2", "type": "true_false", "prompt": "G", "answer": True},
                {"id": "b3", "type": "true_false", "prompt": "H", "answer": True},
                {"id": "b4", "type": "true_false", "prompt": "I", "answer": True},
                {"id": "b5", "type": "true_false", "prompt": "J", "answer": True},
            ])
            findings = lp.course_stats(course)
        l17b = [f for f in findings if f["rule"] == "L17b"]
        self.assertEqual(len(l17b), 1)
        self.assertIn("90% True", l17b[0]["detail"])

    def test_60_percent_true_no_finding(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = []
            for i in range(10):
                tfs.append({
                    "id": f"tf{i}", "type": "true_false",
                    "prompt": f"Statement {i}", "answer": i < 6,
                })
            _write_pack(course, "pack.json", tfs)
            findings = lp.course_stats(course)
        l17b = [f for f in findings if f["rule"] == "L17b"]
        self.assertEqual(l17b, [])


class CourseStatsTypeMixTests(unittest.TestCase):
    def test_all_mc_reports_distribution(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            qs = []
            for i in range(10):
                qs.append({
                    "id": f"q{i}", "type": "multiple_choice",
                    "prompt": f"Q {i}", "options": ["A", "B", "C", "D"], "answer": 0,
                })
            _write_pack(course, "pack.json", qs)
            findings = lp.course_stats(course)
        mix = [f for f in findings if f["rule"] == "L_TYPE_MIX"]
        self.assertEqual(len(mix), 1)
        self.assertEqual(mix[0]["severity"], "advisory")
        self.assertIsNone(mix[0]["qid"])
        self.assertIn("100%", mix[0]["detail"])
        self.assertIn("multiple_choice", mix[0]["detail"])

    def test_mixed_types_reports_all(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            qs = []
            for i in range(8):
                qs.append({
                    "id": f"mc{i}", "type": "multiple_choice",
                    "prompt": f"MC {i}", "options": ["A", "B", "C", "D"], "answer": 0,
                })
            for i in range(4):
                qs.append({
                    "id": f"tf{i}", "type": "true_false",
                    "prompt": f"TF {i}", "answer": True,
                })
            for i in range(3):
                qs.append({
                    "id": f"mt{i}", "type": "matching",
                    "prompt": f"Match {i}",
                    "leftItems": ["X", "Y"], "rightItems": ["Z", "W"],
                    "correctPairs": [0, 1],
                })
            _write_pack(course, "pack.json", qs)
            findings = lp.course_stats(course)
        mix = [f for f in findings if f["rule"] == "L_TYPE_MIX"]
        self.assertEqual(len(mix), 1)
        detail = mix[0]["detail"]
        self.assertIn("multiple_choice: 8 (53%)", detail)
        self.assertIn("true_false: 4 (27%)", detail)
        self.assertIn("matching: 3 (20%)", detail)

    def test_empty_course_no_crash(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            _write_pack(course, "pack.json", [])
            findings = lp.course_stats(course)
        mix = [f for f in findings if f["rule"] == "L_TYPE_MIX"]
        self.assertEqual(mix, [])

    def test_no_pack_files_no_crash(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            findings = lp.course_stats(course)
        self.assertEqual(findings, [])

    def test_malformed_pack_skipped_gracefully(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            (course / "bad.json").write_text("not json")
            qs = [{
                "id": "q0", "type": "multiple_choice",
                "prompt": "Q", "options": ["A", "B", "C", "D"], "answer": 0,
            }]
            _write_pack(course, "good.json", qs)
            findings = lp.course_stats(course)
        l7 = [f for f in findings if f["rule"] == "L7"]
        self.assertTrue(l7, "malformed pack must produce L7 advisory")
        self.assertEqual(l7[0]["severity"], "advisory")
        mix = [f for f in findings if f["rule"] == "L_TYPE_MIX"]
        self.assertEqual(len(mix), 1)

    def test_course_json_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            (course / "_course.json").write_text(json.dumps({"id": "test"}))
            qs = [{
                "id": "q0", "type": "multiple_choice",
                "prompt": "Q", "options": ["A", "B", "C", "D"], "answer": 0,
            }]
            _write_pack(course, "pack.json", qs)
            findings = lp.course_stats(course)
        mix = [f for f in findings if f["rule"] == "L_TYPE_MIX"]
        self.assertEqual(len(mix), 1)
        self.assertIn("1 total", mix[0]["detail"])


class CourseStatsSeverityTests(unittest.TestCase):
    def test_all_findings_are_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = [{
                "id": f"tf{i}", "type": "true_false",
                "prompt": f"S{i}", "answer": True,
            } for i in range(10)]
            qs = tfs + [{
                "id": "mc0", "type": "multiple_choice",
                "prompt": "Q", "options": ["A", "B", "C", "D"], "answer": 0,
            }]
            _write_pack(course, "pack.json", qs)
            findings = lp.course_stats(course)
        self.assertTrue(len(findings) > 0)
        for f in findings:
            self.assertEqual(f["severity"], "advisory")

    def test_severity_to_exit_returns_zero_for_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            course = Path(d)
            tfs = [{
                "id": f"tf{i}", "type": "true_false",
                "prompt": f"S{i}", "answer": True,
            } for i in range(10)]
            _write_pack(course, "pack.json", tfs)
            findings = lp.course_stats(course)
        self.assertTrue(len(findings) > 0)
        self.assertEqual(lp.severity_to_exit(findings), 0)


if __name__ == "__main__":
    unittest.main()
