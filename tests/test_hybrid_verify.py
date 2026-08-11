"""Unit tests for ``scripts/hybrid_verify.py`` — the two-pass hybrid Layer-C
verify (cheap DeepSeek(-go) review pass, Claude certifying pass only if that
pass is clean).

hybrid_verify calls scripts/verify_pack.py's ``main()`` IN-PROCESS, up to
twice per invocation. Every test here MOCKS either ``verify_pack.main``
directly (to assert the exact argv each pass is built with, and that the
second call is skipped whenever it must be) or the underlying critic
transports (``factcheck_pack.run_claude`` / ``critic_providers.run_opencode``
and ``critic_providers.shutil.which``) for a couple of true end-to-end runs
through verify_pack's real certifying logic — exactly the split
tests/test_recert_sweep.py uses. NO real LLM or network call happens, and no
live/paid pass is ever run.

Run from the project root::

    python3 -m unittest tests.test_hybrid_verify -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "hybrid_verify.py"

_spec = importlib.util.spec_from_file_location("hybrid_verify", SCRIPT_PATH)
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

# hybrid_verify imports verify_pack (which imports factcheck_pack and
# critic_providers) by path; reach the SAME module objects here so patches
# land where verify_pack.run_layer_c actually looks them up (mirrors
# test_recert_sweep.py's `vp = rs.verify_pack; fc = vp.factcheck_pack`).
vp = hv.verify_pack
fc = vp.factcheck_pack
cp = vp.critic_providers


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


def _coverage_blueprint(questions: list[dict]) -> list[dict]:
    topics = sorted({q.get("topic") for q in questions if q.get("topic")})
    return [{"topic": t, "min": 1} for t in topics]


def claude_envelope(findings: list[dict], checked: int = 1) -> str:
    """Canned ``claude --output-format json`` envelope, exactly what
    ``run_claude`` returns as stdout (real call never happens)."""
    inner = json.dumps({"findings": findings, "checked": checked})
    return json.dumps({"type": "result", "result": inner,
                       "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})


def ds_reply(findings: list[dict], checked: int = 1) -> "cp.CriticReply":
    """Canned opencode CriticReply — model is always None, mirroring the real
    run_opencode contract (opencode's event stream never attests a model)."""
    text = json.dumps({"findings": findings, "checked": checked})
    return cp.CriticReply(text=text, model=None, provider="opencode")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.pack = self.tmp_path / "ch01.json"
        payload = {
            "pack_id": "ch01",
            "questions": [dict(CLEAN_Q)],
        }
        payload["coverage_blueprint"] = _coverage_blueprint(payload["questions"])
        self.pack.write_text(json.dumps(payload))

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, extra_argv: list[str]):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = hv.main([str(self.pack)] + extra_argv)
        return rc, out.getvalue(), err.getvalue()


class ArgParserTests(unittest.TestCase):
    def test_help_lists_all_flags(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                hv.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        helptext = out.getvalue()
        for flag in ("--ds-model", "--variant", "--claude-model",
                    "--batch-size", "--timeout", "--jobs", "--strict"):
            self.assertIn(flag, helptext)

    def test_defaults(self):
        args = hv.build_arg_parser().parse_args(["some/pack.json"])
        self.assertEqual(args.ds_model, hv.DEFAULT_DS_MODEL)
        self.assertEqual(args.ds_model, "opencode-go/deepseek-v4-flash")
        self.assertEqual(args.variant, "max")
        self.assertEqual(args.claude_model, "opus")
        self.assertEqual(args.claude_model, hv.DEFAULT_CLAUDE_MODEL)
        self.assertEqual(args.batch_size, 12)
        self.assertEqual(args.timeout, 180)
        self.assertEqual(args.jobs, fc.DEFAULT_JOBS)
        self.assertFalse(args.strict)

    def test_no_pack_is_argparse_usage_error(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                hv.main([])
        self.assertNotEqual(cm.exception.code, 0)


def _capture(rc_sequence: list[int]):
    """Build a fake verify_pack.main that returns rc_sequence[call_index] and
    records every argv it was called with, in order."""
    calls: list[list[str]] = []

    def fake_main(argv):
        calls.append(argv)
        return rc_sequence[len(calls) - 1]

    return calls, fake_main


class RunHybridArgvTests(_Base):
    """Mock verify_pack.main directly so each pass's argv can be inspected
    without going through any real (or even mocked-at-the-transport) critic
    call — mirrors test_recert_sweep.py's CertifyOneTests."""

    def test_ds_pass_argv_shape(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="opencode-go/deepseek-v4-flash",
                          variant="max", claude_model="claude-sonnet-5",
                          batch_size=12, timeout=180, jobs=6, strict=False)
        ds_argv = calls[0]
        self.assertEqual(ds_argv[0], str(self.pack))
        self.assertIn("--provider", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--provider") + 1], "opencode")
        self.assertIn("--model", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--model") + 1],
                         "opencode-go/deepseek-v4-flash")
        self.assertIn("--variant", ds_argv)
        self.assertEqual(ds_argv[ds_argv.index("--variant") + 1], "max")
        self.assertIn("--batch-size", ds_argv)
        self.assertIn("--timeout", ds_argv)
        self.assertIn("--jobs", ds_argv)
        self.assertNotIn("--strict", ds_argv)

    def test_claude_pass_argv_shape_and_no_provider_or_variant(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            hv.run_hybrid(self.pack, ds_model="opencode-go/deepseek-v4-flash",
                          variant="max", claude_model="opus",
                          batch_size=5, timeout=42, jobs=3, strict=True)
        claude_argv = calls[1]
        self.assertEqual(claude_argv[0], str(self.pack))
        self.assertNotIn("--provider", claude_argv)
        self.assertNotIn("--variant", claude_argv)
        self.assertIn("--model", claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--model") + 1], "opus")
        self.assertIn("--batch-size", claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--batch-size") + 1], "5")
        self.assertIn("--timeout", claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--timeout") + 1], "42")
        self.assertIn("--jobs", claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--jobs") + 1], "3")
        self.assertIn("--strict", claude_argv)


class ShortCircuitTests(_Base):
    """The whole point: Claude quota (the second verify_pack.main call) is
    only spent when the DS pass is clean."""

    def test_ds_blocking_stops_before_claude_runs(self):
        calls, fake_main = _capture([2])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", claude_model="c",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)

    def test_ds_error_stops_before_claude_runs(self):
        calls, fake_main = _capture([1])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", claude_model="c",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)

    def test_ds_unexpected_zero_stops_defensively_before_claude_runs(self):
        """0 is structurally impossible for a lone --provider opencode pass
        (verify_pack's own certifying rule), but this wrapper must not
        silently spend Claude quota if that invariant is ever violated."""
        calls, fake_main = _capture([0])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", claude_model="c",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_ds_clean_then_claude_ready_runs_both_passes(self):
        calls, fake_main = _capture([3, 0])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", claude_model="c",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)

    def test_ds_clean_then_claude_not_ready_reports_disagreement(self):
        """The interesting case: DS says clean, Claude's certifying pass
        still finds a blocking issue. Final result must reflect Claude's
        verdict, and the report must explain why this happened rather than
        surface a confusing double result."""
        calls, fake_main = _capture([3, 2])
        with patch.object(vp, "main", side_effect=fake_main):
            rc, report = hv.run_hybrid(
                self.pack, ds_model="d", variant="max", claude_model="c",
                batch_size=12, timeout=180, jobs=6, strict=False)
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("DS pass came back clean but Claude's certifying pass "
                      "found a blocking issue", report)


class EndToEndTests(_Base):
    """Real verify_pack logic end to end (no mocked verify_pack.main): mocks
    only the critic transports, so this catches argv-wiring mistakes an
    argv-capture test would miss (e.g. a typo'd flag name verify_pack itself
    would reject)."""

    def test_ds_blocking_finding_stops_before_claude_and_pack_stays_uncertified(self):
        finding = {"qid": "q1", "severity": "wrong-answer", "issue": "wrong",
                   "correction": "fix", "confidence": "high"}

        def _claude_must_not_run(*a, **kw):
            raise AssertionError("Claude pass must not run when DS is blocking")

        with patch.object(cp, "run_opencode",
                          return_value=ds_reply([finding])), \
             patch.object(fc, "run_claude", side_effect=_claude_must_not_run), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, err = self.run_main([])

        self.assertEqual(rc, 2)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_ds_clean_then_claude_certifies(self):
        with patch.object(cp, "run_opencode", return_value=ds_reply([])), \
             patch.object(fc, "run_claude", return_value=claude_envelope([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, err = self.run_main([])

        self.assertEqual(rc, 0)
        data = json.loads(self.pack.read_text())
        self.assertIn("certification", data)
        self.assertEqual(data["certification"]["review_method"],
                         "external-layer-c-strict")

    def test_ds_go_model_is_reached_not_the_free_tier(self):
        """Confirms the DS pass reaches the go-tier model reference and NOT
        opencode's free-tier default, end to end through real verify_pack /
        critic_providers dispatch."""
        seen_models = []

        def _spy_run_opencode(prompt, model, timeout, variant=None):
            seen_models.append(model)
            return ds_reply([])

        with patch.object(cp, "run_opencode", side_effect=_spy_run_opencode), \
             patch.object(fc, "run_claude", return_value=claude_envelope([])), \
             patch.object(cp.shutil, "which",
                          side_effect=lambda name: f"/usr/bin/{name}"):
            rc, out, err = self.run_main([])

        self.assertEqual(rc, 0)
        self.assertEqual(seen_models, ["opencode-go/deepseek-v4-flash"])


if __name__ == "__main__":
    unittest.main()
