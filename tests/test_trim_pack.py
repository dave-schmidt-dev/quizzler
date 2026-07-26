"""Unit tests for ``scripts/trim_pack.py`` — the mechanical pack trimmer.

Everything runs against inline fixture dicts or a throw-away ``tempfile``
course directory; the real ``question-packs/`` tree is never touched (no
`--all`, no real course path is ever passed to ``main``).

Mirrors the style of ``tests/test_lint_packs.py`` / ``tests/test_build_manifest.py``:
the script is imported by path (``scripts/`` isn't a package) and pure-logic
functions are exercised directly, with a smaller set of file-IO tests driving
``main()`` end-to-end against a temp directory.

Run from the project root::

    python3 -m unittest tests.test_trim_pack -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "trim_pack.py"

_spec = importlib.util.spec_from_file_location("trim_pack", SCRIPT_PATH)
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)


# ── fixture builders ─────────────────────────────────────────────────────────

def q(id_, type_, topic, **over) -> dict:
    base = {"id": id_, "type": type_, "topic": topic, "difficulty": "easy",
            "prompt": f"prompt for {id_}", "explanation": f"explanation for {id_}",
            "diagram": None}
    base.update(over)
    return base


def mc(id_, topic, answer=0, **over) -> dict:
    return q(id_, "multiple_choice", topic,
              options=["a", "b", "c", "d"], answer=answer, **over)


def matching(id_, topic, **over) -> dict:
    return q(id_, "matching", topic,
              leftItems=["x", "y"], rightItems=["1", "2"], correctPairs=[0, 1],
              **over)


def true_false(id_, topic, answer=True, **over) -> dict:
    return q(id_, "true_false", topic, answer=answer, **over)


def pack_with(questions, blueprint=None, **over) -> dict:
    base = {
        "pack_id": "test-pack", "subject": "Test", "title": "Test Pack",
        "version": 1, "generated_at": "2026-01-01T00:00:00-04:00",
        "generation_mode": "manual", "notes": "fixture",
        "coverage_blueprint": blueprint if blueprint is not None else [],
        "questions": questions,
    }
    base.update(over)
    return base


# ── blueprint_min_map ────────────────────────────────────────────────────────

class BlueprintMinMapTests(unittest.TestCase):
    def test_bare_string_defaults_to_min_1(self):
        self.assertEqual(tp.blueprint_min_map(["alpha"]), {"alpha": 1})

    def test_dict_entry_honors_min(self):
        self.assertEqual(
            tp.blueprint_min_map([{"topic": "alpha", "min": 3}]), {"alpha": 3})

    def test_missing_or_invalid_min_defaults_to_1(self):
        self.assertEqual(
            tp.blueprint_min_map([{"topic": "alpha"}, {"topic": "beta", "min": 0},
                                   {"topic": "gamma", "min": "two"}]),
            {"alpha": 1, "beta": 1, "gamma": 1})

    def test_empty_blueprint(self):
        self.assertEqual(tp.blueprint_min_map(None), {})
        self.assertEqual(tp.blueprint_min_map([]), {})


# ── trim_pack_data: 1-per-topic selection + dropped-qid listing ────────────

class OnePerTopicTests(unittest.TestCase):
    def test_keeps_one_per_topic_and_drops_the_rest(self):
        pack = pack_with(
            [mc("q1", "alpha", answer=0), mc("q2", "alpha", answer=1),
             mc("q3", "alpha", answer=2), mc("q4", "beta", answer=0)],
            blueprint=[{"topic": "alpha", "min": 1}, {"topic": "beta", "min": 1}])
        result = tp.trim_pack_data(pack)

        trimmed_ids = [x["id"] for x in result["pack"]["questions"]]
        self.assertEqual(len(trimmed_ids), 2)
        self.assertIn("q4", trimmed_ids)
        # exactly one alpha survivor
        alpha_survivors = [i for i in trimmed_ids if i in ("q1", "q2", "q3")]
        self.assertEqual(len(alpha_survivors), 1)

    def test_dropped_qid_listing_matches_non_survivors(self):
        pack = pack_with(
            [mc("q1", "alpha"), mc("q2", "alpha"), mc("q3", "alpha")],
            blueprint=[{"topic": "alpha", "min": 1}])
        result = tp.trim_pack_data(pack)

        trimmed_ids = {x["id"] for x in result["pack"]["questions"]}
        self.assertEqual(len(trimmed_ids), 1)
        self.assertEqual(set(result["dropped_qids"]), {"q1", "q2", "q3"} - trimmed_ids)
        self.assertEqual(len(result["dropped_qids"]), 2)
        self.assertEqual(result["stats"]["original_count"], 3)
        self.assertEqual(result["stats"]["trimmed_count"], 1)
        self.assertEqual(result["stats"]["dropped_count"], 2)

    def test_topic_with_single_candidate_is_untouched_and_unflagged(self):
        pack = pack_with([mc("q1", "alpha")],
                          blueprint=[{"topic": "alpha", "min": 1}])
        result = tp.trim_pack_data(pack)

        self.assertEqual([x["id"] for x in result["pack"]["questions"]], ["q1"])
        self.assertEqual(result["dropped_qids"], [])
        self.assertEqual(result["manual_review"], [])

    def test_blueprint_min_greater_than_one_keeps_min_count(self):
        pack = pack_with(
            [mc("q1", "alpha"), mc("q2", "alpha"), mc("q3", "alpha")],
            blueprint=[{"topic": "alpha", "min": 2}])
        result = tp.trim_pack_data(pack)

        self.assertEqual(result["stats"]["trimmed_count"], 2)
        self.assertEqual(result["stats"]["dropped_count"], 1)
        self.assertEqual(len(result["manual_review"]), 1)
        self.assertEqual(result["manual_review"][0]["topic"], "alpha")
        self.assertEqual(len(result["manual_review"][0]["kept"]), 2)
        self.assertEqual(len(result["manual_review"][0]["dropped"]), 1)

    def test_topic_not_in_blueprint_still_defaults_to_min_1(self):
        pack = pack_with([mc("q1", "orphan"), mc("q2", "orphan")], blueprint=[])
        result = tp.trim_pack_data(pack)

        self.assertEqual(result["stats"]["trimmed_count"], 1)
        self.assertEqual(result["stats"]["dropped_count"], 1)

    def test_non_dict_questions_are_skipped_without_crashing(self):
        pack = pack_with([mc("q1", "alpha"), "not-a-question", None],
                          blueprint=[{"topic": "alpha", "min": 1}])
        result = tp.trim_pack_data(pack)
        self.assertEqual([x["id"] for x in result["pack"]["questions"]], ["q1"])

    def test_questions_missing_topic_are_all_kept_untrimmed(self):
        # A falsy `topic` (None or "") can't be safely grouped against the
        # blueprint at all, so there is nothing sound to trim it down to —
        # keep everything rather than silently guessing a keep-count of 1.
        # (lint rule L12 requiring a topic is what should catch this pack
        # before it ever reaches the trimmer.)
        pack = pack_with(
            [mc("q1", "alpha"), mc("no_topic_1", None), mc("no_topic_2", None),
             mc("blank_topic", "")],
            blueprint=[{"topic": "alpha", "min": 1}])
        result = tp.trim_pack_data(pack)

        trimmed_ids = {x["id"] for x in result["pack"]["questions"]}
        self.assertEqual(
            trimmed_ids, {"q1", "no_topic_1", "no_topic_2", "blank_topic"})
        self.assertEqual(result["dropped_qids"], [])
        self.assertEqual(result["manual_review"], [])


# ── manual-review flag emission (INV-8) ──────────────────────────────────────

class ManualReviewFlagTests(unittest.TestCase):
    def test_flag_emitted_only_for_topics_with_drops(self):
        pack = pack_with(
            [mc("q1", "alpha"), mc("q2", "alpha"),  # alpha: drop 1
             mc("q3", "beta")],                       # beta: no drop
            blueprint=[{"topic": "alpha", "min": 1}, {"topic": "beta", "min": 1}])
        result = tp.trim_pack_data(pack)

        flagged_topics = {e["topic"] for e in result["manual_review"]}
        self.assertEqual(flagged_topics, {"alpha"})

    def test_flag_records_kept_and_dropped_ids(self):
        pack = pack_with(
            [mc("q1", "alpha"), mc("q2", "alpha"), mc("q3", "alpha")],
            blueprint=[{"topic": "alpha", "min": 1}])
        result = tp.trim_pack_data(pack)

        self.assertEqual(len(result["manual_review"]), 1)
        entry = result["manual_review"][0]
        self.assertEqual(entry["topic"], "alpha")
        self.assertEqual(len(entry["kept"]), 1)
        self.assertEqual(set(entry["kept"]) | set(entry["dropped"]),
                          {"q1", "q2", "q3"})
        self.assertEqual(entry["kept"][0], result["pack"]["questions"][0]["id"])

    def test_no_drops_anywhere_means_empty_manual_review(self):
        pack = pack_with([mc("q1", "alpha"), mc("q2", "beta")],
                          blueprint=[{"topic": "alpha"}, {"topic": "beta"}])
        result = tp.trim_pack_data(pack)
        self.assertEqual(result["manual_review"], [])
        self.assertEqual(result["stats"]["topics_with_manual_review"], 0)


# ── mechanical selection: no fake quality heuristic ──────────────────────────

class NoQualityHeuristicTests(unittest.TestCase):
    def test_selection_never_reads_prompt_or_explanation_text(self):
        """Selection must be reproducible from type/answer/order alone — the
        prompt/explanation text must be free to vary without changing which
        survivor is picked (i.e., no hidden 'which one sounds better' judgment).
        """
        pack_a = pack_with(
            [mc("q1", "alpha", answer=0, prompt="AAAA", explanation="one"),
             mc("q2", "alpha", answer=1, prompt="ZZZZ", explanation="two")],
            blueprint=[{"topic": "alpha", "min": 1}])
        pack_b = pack_with(
            [mc("q1", "alpha", answer=0, prompt="totally different text",
                explanation="a much longer and more detailed explanation here"),
             mc("q2", "alpha", answer=1, prompt="short", explanation="x")],
            blueprint=[{"topic": "alpha", "min": 1}])

        survivor_a = tp.trim_pack_data(pack_a)["pack"]["questions"][0]["id"]
        survivor_b = tp.trim_pack_data(pack_b)["pack"]["questions"][0]["id"]
        self.assertEqual(survivor_a, survivor_b)


# ── keyed-answer-index spreading (L16) ───────────────────────────────────────

class AnswerIndexSpreadingTests(unittest.TestCase):
    def test_spreads_indices_instead_of_always_picking_first(self):
        # Three topics, each with a same-type pair offering answer index 0 vs 1.
        # A naive "always keep first-in-file" trimmer would pick index 0 all
        # three times. The balancing tie-break must choose index 1 at least
        # once to spread the running distribution.
        pack = pack_with(
            [mc("a0", "A", answer=0), mc("a1", "A", answer=1),
             mc("b0", "B", answer=0), mc("b1", "B", answer=1),
             mc("c0", "C", answer=0), mc("c1", "C", answer=1)],
            blueprint=[{"topic": "A"}, {"topic": "B"}, {"topic": "C"}])
        result = tp.trim_pack_data(pack)

        survivors = {x["id"]: x["answer"] for x in result["pack"]["questions"]}
        self.assertEqual(len(survivors), 3)
        indices = sorted(survivors.values())
        # Not all three survivors share the same answer index.
        self.assertTrue(len(set(indices)) > 1,
                         f"expected spread indices, got {indices}")
        # Deterministic exact trace: topic A ties on first pick -> keeps the
        # earlier item (a0, index 0); topic B then prefers the currently
        # under-represented index 1 (b1); topic C ties again and falls back
        # to original order (c0, index 0).
        self.assertEqual(survivors, {"a0": 0, "b1": 1, "c0": 0})

    def test_true_false_boolean_answer_is_never_folded_into_index_tally(self):
        # A true_false `answer` of True/False must not be treated as an
        # index-0/1 slot (bool is a subclass of int in Python) — this would
        # otherwise corrupt the L16 spreading tally with unrelated data.
        pack = pack_with(
            [true_false("t1", "alpha", answer=True),
             mc("m1", "beta", answer=0), mc("m2", "beta", answer=1)],
            blueprint=[{"topic": "alpha"}, {"topic": "beta"}])
        result = tp.trim_pack_data(pack)
        beta_survivor = next(
            x for x in result["pack"]["questions"] if x["topic"] == "beta")
        # With no prior tally pollution from the boolean answer, the beta tie
        # falls back to original order (m1, index 0).
        self.assertEqual(beta_survivor["id"], "m1")


# ── question-type variety preservation ───────────────────────────────────────

class TypeVarietyTests(unittest.TestCase):
    def test_prefers_underrepresented_type_over_pure_file_order(self):
        # Each topic offers an mc candidate (earlier in file) and a matching
        # candidate (later in file). A naive "always first" trimmer would
        # pick multiple_choice both times, losing type variety.
        pack = pack_with(
            [mc("d_mc", "D", answer=0), matching("d_match", "D"),
             mc("e_mc", "E", answer=0), matching("e_match", "E")],
            blueprint=[{"topic": "D"}, {"topic": "E"}])
        result = tp.trim_pack_data(pack)

        survivors = {x["topic"]: x["type"] for x in result["pack"]["questions"]}
        self.assertEqual(survivors, {"D": "multiple_choice", "E": "matching"})
        types_kept = sorted(survivors.values())
        self.assertEqual(len(set(types_kept)), 2, "expected type variety, "
                         f"got {types_kept}")


# ── certification staleness ──────────────────────────────────────────────────

class CertificationStrippingTests(unittest.TestCase):
    def test_certification_removed_after_trim(self):
        pack = pack_with([mc("q1", "alpha"), mc("q2", "alpha")],
                          blueprint=[{"topic": "alpha", "min": 1}],
                          certification={"certified": True})
        result = tp.trim_pack_data(pack)
        self.assertNotIn("certification", result["pack"])

    def test_original_pack_dict_is_not_mutated(self):
        pack = pack_with([mc("q1", "alpha"), mc("q2", "alpha")],
                          blueprint=[{"topic": "alpha", "min": 1}],
                          certification={"certified": True})
        original_question_count = len(pack["questions"])
        tp.trim_pack_data(pack)
        self.assertEqual(len(pack["questions"]), original_question_count)
        self.assertIn("certification", pack)


# ── CLI / file-IO end-to-end ──────────────────────────────────────────────────

def _run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = tp.main(argv)
    return rc, out.getvalue(), err.getvalue()


class CliFileIoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.course_dir = self.tmp_path / "some-course"
        self.course_dir.mkdir()
        self.pack_path = self.course_dir / "pack.json"
        self.original_pack = pack_with(
            [mc("q1", "alpha", answer=0), mc("q2", "alpha", answer=1),
             mc("q3", "alpha", answer=2), mc("q4", "beta", answer=0)],
            blueprint=[{"topic": "alpha", "min": 1}, {"topic": "beta", "min": 1}])
        self.pack_path.write_text(json.dumps(self.original_pack))

    def tearDown(self):
        self._tmp.cleanup()

    def test_help_shows_args(self):
        with self.assertRaises(SystemExit) as cm:
            _run_main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_help_output_mentions_pack_argument(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                tp.main(["--help"])
        self.assertIn("pack", out.getvalue())

    def test_backs_up_original_and_trims_in_place(self):
        rc, out, err = _run_main([str(self.pack_path)])
        self.assertEqual(rc, 0, err)

        backup_path = self.course_dir / "_full" / "pack.json"
        self.assertTrue(backup_path.is_file())
        backed_up = json.loads(backup_path.read_text())
        self.assertEqual(backed_up, self.original_pack)

        trimmed = json.loads(self.pack_path.read_text())
        self.assertEqual(len(trimmed["questions"]), 2)  # 1 alpha + 1 beta

        report_path = self.course_dir / "_full" / "pack.trim_report.json"
        self.assertTrue(report_path.is_file())
        report = json.loads(report_path.read_text())
        self.assertEqual(len(report["dropped_qids"]), 2)
        self.assertEqual(len(report["manual_review"]), 1)
        self.assertEqual(report["manual_review"][0]["topic"], "alpha")

    def test_stdout_shows_dropped_ids_and_manual_review_flags(self):
        rc, out, err = _run_main([str(self.pack_path)])
        self.assertEqual(rc, 0, err)
        self.assertIn("Dropped question ids:", out)
        self.assertIn("q1", out)
        self.assertIn("MANUAL REVIEW NEEDED", out)
        self.assertIn("alpha", out)

    def test_second_run_without_force_refuses(self):
        rc1, _, _ = _run_main([str(self.pack_path)])
        self.assertEqual(rc1, 0)
        trimmed_after_first = self.pack_path.read_text()

        rc2, _out2, err2 = _run_main([str(self.pack_path)])
        self.assertEqual(rc2, 1)
        self.assertIn("backup already exists", err2)
        # file untouched by the refused second run
        self.assertEqual(self.pack_path.read_text(), trimmed_after_first)

    def test_force_reruns_without_clobbering_existing_backup(self):
        _run_main([str(self.pack_path)])
        backup_path = self.course_dir / "_full" / "pack.json"
        backup_before = backup_path.read_text()

        rc, _out, err = _run_main([str(self.pack_path), "--force"])
        self.assertEqual(rc, 0, err)
        # Backup must still hold the ORIGINAL 4-question pack, not a
        # once-trimmed copy — the true original is never overwritten.
        backup_after = backup_path.read_text()
        self.assertEqual(backup_before, backup_after)
        self.assertEqual(len(json.loads(backup_after)["questions"]), 4)

    def test_dry_run_writes_no_files(self):
        rc, out, err = _run_main([str(self.pack_path), "--dry-run"])
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.course_dir / "_full").exists())
        self.assertEqual(
            json.loads(self.pack_path.read_text()), self.original_pack)
        self.assertIn("dry run", out.lower())

    def test_missing_pack_file_is_error(self):
        rc, _out, err = _run_main([str(self.tmp_path / "nope.json")])
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)

    def test_empty_questions_is_error(self):
        empty_pack_path = self.course_dir / "empty.json"
        empty_pack_path.write_text(json.dumps(pack_with([])))
        rc, _out, err = _run_main([str(empty_pack_path)])
        self.assertEqual(rc, 1)
        self.assertIn("no questions", err)

    def test_custom_out_path_leaves_input_pack_untouched(self):
        out_path = self.course_dir / "trimmed.json"
        rc, _out, err = _run_main([str(self.pack_path), "--out", str(out_path)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            json.loads(self.pack_path.read_text()), self.original_pack)
        self.assertTrue(out_path.is_file())
        self.assertEqual(len(json.loads(out_path.read_text())["questions"]), 2)

    def test_json_flag_emits_machine_readable_report(self):
        rc, out, err = _run_main([str(self.pack_path), "--json"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertIn("dropped_qids", payload)
        self.assertIn("manual_review", payload)
        self.assertIn("stats", payload)


if __name__ == "__main__":
    unittest.main()
