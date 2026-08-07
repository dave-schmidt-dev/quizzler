"""Unit tests for the multi-provider Layer-C critic.

Covers three seams:

* ``scripts/critic_providers.py`` — provider registry, transport, and the secret
  hygiene rules around API keys.
* ``scripts/factcheck_pack.run_critic`` — the dispatch point that decides which
  model reviews a pack.
* ``scripts/critic_panel.py`` — running several independent critics and merging
  their findings, plus the ``verify_pack`` integration that certifies the result.

NO real LLM, network, or subprocess call happens anywhere here: the Claude path
is mocked at ``factcheck_pack.run_claude`` (as every other critic suite does) and
the HTTP providers are mocked at ``urllib.request.urlopen``.

The single most important test in this file is
``MergeFindingsTests.test_a_finding_from_one_pass_alone_survives_the_merge``. The
whole panel is worthless — actively harmful, in fact — if the merge ever becomes
a majority vote, because the defect class it exists to catch is the finding only
one model was sharp enough to raise.

Run from the project root::

    python3 -m unittest tests.test_critic_providers -v
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_vp_spec = importlib.util.spec_from_file_location(
    "verify_pack", PROJECT_ROOT / "scripts" / "verify_pack.py")
vp = importlib.util.module_from_spec(_vp_spec)
_vp_spec.loader.exec_module(vp)
# Reach the SAME module objects verify_pack loaded by path, so patches land where
# the production code looks them up.
fc = vp.factcheck_pack
cp = vp.critic_providers
panel_mod = vp.critic_panel
pack_cert = vp.pack_cert

# A key-shaped string used only as test data. Not a credential — it never leaves
# this process and matches nothing. Its job is to prove redaction fires.
FAKE_KEY = "sk-testonly-0123456789abcdef"


class _FakeResponse:
    """Minimal stand-in for the context manager ``urlopen`` returns."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_response(content: str, model: str = "deepseek-v4-flash") -> dict:
    """An OpenAI-compatible chat-completions response body."""
    return {"model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}]}


def _critic_json(findings: list[dict], checked: int = 99) -> str:
    return json.dumps({"findings": findings, "checked": checked})


def _finding(qid: str, issue: str, severity: str = "wrong-answer",
             confidence: str = "high", correction: str = "") -> dict:
    return {"qid": qid, "severity": severity, "issue": issue,
            "confidence": confidence, "correction": correction}


# ── critic_providers: registry ────────────────────────────────────────────────


class ProviderRegistryTests(unittest.TestCase):
    def test_unknown_provider_raises_instead_of_falling_back(self):
        """A typo must NOT silently run the expensive default.

        Falling back to claude would produce a passing run whose certification
        named a provider that never executed — a provenance lie, and an
        unexplained bill.
        """
        with self.assertRaises(ValueError) as ctx:
            cp.get_spec("deepsek")
        self.assertIn("unknown critic provider", str(ctx.exception))
        self.assertIn("deepseek", str(ctx.exception))  # lists the real options

    def test_registry_exposes_the_documented_providers(self):
        names = cp.provider_names()
        for expected in ("claude", "deepseek", "ollama", "openai-compatible"):
            self.assertIn(expected, names)

    def test_base_url_env_override_beats_the_spec_default(self):
        spec = cp.get_spec("deepseek")
        with patch.dict("os.environ", {"QUIZZLER_DEEPSEEK_URL": "https://proxy.internal"}):
            self.assertEqual(cp.base_url(spec), "https://proxy.internal")
        with patch.dict("os.environ", {"QUIZZLER_DEEPSEEK_URL": ""}):
            self.assertEqual(cp.base_url(spec), cp.DEFAULT_DEEPSEEK_URL)

    def test_claude_is_not_dispatched_through_the_generic_runner(self):
        """critic_providers.run must refuse the claude kind.

        The Claude call deliberately lives in factcheck_pack so the existing
        ``patch.object(fc, "run_claude", ...)`` guards keep working. If this ever
        starts succeeding, those patches are silently disarmed and the suites can
        make real billed calls.
        """
        with self.assertRaises(RuntimeError) as ctx:
            cp.run("claude", "prompt", None, 5)
        self.assertIn("factcheck_pack.run_critic", str(ctx.exception))


# ── critic_providers: secret hygiene ──────────────────────────────────────────


class SecretHygieneTests(unittest.TestCase):
    """A leaked key is a rotation event, so these are correctness tests."""

    def test_redact_scrubs_a_live_key_out_of_error_text(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}):
            scrubbed = cp._redact(f"upstream said: bad token {FAKE_KEY} rejected")
        self.assertNotIn(FAKE_KEY, scrubbed)
        self.assertIn("«redacted»", scrubbed)

    def test_redact_does_not_wildcard_on_a_short_or_empty_key(self):
        """An empty env value must not turn every string into a redaction."""
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            self.assertEqual(cp._redact("nothing secret here"),
                             "nothing secret here")
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "abc"}):
            self.assertEqual(cp._redact("abcdef"), "abcdef")

    def test_safe_url_drops_query_string_and_userinfo(self):
        self.assertEqual(
            cp._safe_url("https://api.example.com/v1/chat?api_key=" + FAKE_KEY),
            "https://api.example.com/v1/chat")
        self.assertEqual(
            cp._safe_url(f"https://user:{FAKE_KEY}@api.example.com/v1/chat"),
            "https://api.example.com/v1/chat")

    def test_an_upstream_error_echoing_the_key_does_not_leak_it(self):
        """The realistic leak path: a gateway reflects the request in its 4xx body."""
        body = json.dumps({"error": f"invalid Authorization: Bearer {FAKE_KEY}"})
        http_error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "Unauthorized",
            {}, io.BytesIO(body.encode()))
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run("deepseek", "prompt", None, 5)
        message = str(ctx.exception)
        self.assertNotIn(FAKE_KEY, message)
        self.assertIn("HTTP 401", message)

    def test_the_key_travels_in_a_header_and_never_in_the_body(self):
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = req.data.decode("utf-8")
            return _FakeResponse(_chat_response(_critic_json([])))

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}), \
             patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            cp.run("deepseek", "grade these questions", None, 5)
        self.assertEqual(captured["auth"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, captured["body"])

    def test_missing_key_names_the_broker_and_never_the_legacy_paths(self):
        """The error must point at bws-secret-exec.

        ``bws-run`` and ``bws-get`` print secret values to a terminal and are
        barred from agent automation. An error message is documentation people
        actually read, so it must not send them down a path policy forbids.
        """
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}):
            reason = cp.preflight("deepseek")
        self.assertIsNotNone(reason)
        self.assertIn("DEEPSEEK_API_KEY", reason)
        self.assertIn("bws-secret-exec", reason)
        self.assertNotIn("bws-run", reason)
        self.assertNotIn("bws-get", reason)


# ── critic_providers: transport + observed model ──────────────────────────────


class ObservedModelTests(unittest.TestCase):
    """The certification records what ANSWERED, not what was asked for."""

    def test_openai_provider_reports_the_servers_model_not_the_request(self):
        served = "deepseek-v4-pro-2026-08"
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}), \
             patch("urllib.request.urlopen",
                   return_value=_FakeResponse(
                       _chat_response(_critic_json([]), model=served))):
            reply = cp.run("deepseek", "prompt", "deepseek-v4-flash", 5)
        self.assertEqual(reply.model, served)
        self.assertNotEqual(reply.model, "deepseek-v4-flash")
        self.assertEqual(reply.provider, "deepseek")

    def test_an_unreported_model_stays_none_rather_than_being_backfilled(self):
        """Unknown must be recorded as unknown.

        Substituting the requested id here would turn 'the provider told us
        nothing' into a confident provenance claim — self-attestation by a
        different route.
        """
        body = {"choices": [{"message": {"content": _critic_json([])}}]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}), \
             patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            reply = cp.run("deepseek", "prompt", "deepseek-v4-flash", 5)
        self.assertIsNone(reply.model)

    def test_ollama_provider_returns_text_and_observed_model(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeResponse(
                       {"model": "qwen3:8b", "response": _critic_json([])})):
            reply = cp.run("ollama", "prompt", "qwen3", 5)
        self.assertEqual(reply.model, "qwen3:8b")
        self.assertIn("findings", reply.text)

    def test_an_empty_completion_is_an_error_not_a_clean_pass(self):
        """A blank reply must never parse as 'checked everything, found nothing'."""
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}), \
             patch("urllib.request.urlopen",
                   return_value=_FakeResponse(_chat_response("   "))):
            with self.assertRaises(RuntimeError):
                cp.run("deepseek", "prompt", None, 5)

    def test_a_provider_without_a_default_model_says_so(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeResponse({"response": "{}"})):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run("ollama", "prompt", None, 5)
        self.assertIn("--model", str(ctx.exception))


class PreflightTests(unittest.TestCase):
    def test_unreachable_ollama_is_reported_before_any_batch_runs(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            reason = cp.preflight("ollama", "qwen3:8b")
        self.assertIsNotNone(reason)
        self.assertIn("ollama serve", reason)

    def test_ollama_with_no_models_pulled_says_which_command_to_run(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeResponse({"models": []})):
            reason = cp.preflight("ollama", "qwen3:8b")
        self.assertIn("ollama pull", reason)

    def test_ollama_matches_a_bare_model_name_against_its_tag(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeResponse({"models": [{"name": "qwen3:8b"}]})):
            self.assertIsNone(cp.preflight("ollama", "qwen3"))
            self.assertIsNone(cp.preflight("ollama", "qwen3:8b"))
            self.assertIsNotNone(cp.preflight("ollama", "llama4"))

    def test_deepseek_with_a_key_present_preflights_clean(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": FAKE_KEY}):
            self.assertIsNone(cp.preflight("deepseek"))


# ── factcheck_pack: the dispatch seam ─────────────────────────────────────────


class RunCriticSeamTests(unittest.TestCase):
    def test_claude_path_unwraps_the_envelope_and_reads_model_usage(self):
        stdout = json.dumps({
            "type": "result", "result": _critic_json([]),
            "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        with patch.object(fc, "run_claude", return_value=stdout):
            reply = fc.run_critic("prompt", "claude-sonnet-5", 5)
        self.assertEqual(reply.provider, "claude")
        self.assertEqual(reply.model, "claude-sonnet-5")
        self.assertIn("findings", reply.text)

    def test_a_non_claude_provider_never_touches_the_claude_cli(self):
        def _boom(*a, **k):
            raise AssertionError("run_claude must not be called for another provider")

        with patch.object(fc, "run_claude", side_effect=_boom), \
             patch.object(cp, "run",
                          return_value=cp.CriticReply(_critic_json([]),
                                                      "deepseek-v4-flash",
                                                      "deepseek")) as ran:
            reply = fc.run_critic("prompt", None, 5, provider="deepseek")
        ran.assert_called_once()
        self.assertEqual(reply.provider, "deepseek")

    def test_collect_findings_rejects_an_unknown_provider_up_front(self):
        """Fail once, not once per batch.

        Without the up-front check a typo produces N identical per-batch errors
        that look like N transient failures.
        """
        with self.assertRaises(ValueError):
            fc.collect_findings([{"id": "q1"}], None, 12, 5, provider="nope")

    def test_collect_findings_records_the_provider_it_used(self):
        with patch.object(cp, "run",
                          return_value=cp.CriticReply(_critic_json([], checked=1),
                                                      "qwen3:8b", "ollama")):
            result = fc.collect_findings([{"id": "q1"}], "qwen3:8b", 12, 5,
                                         provider="ollama")
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["errors"], [])


# ── critic_panel: parsing ─────────────────────────────────────────────────────


class ParsePanelTests(unittest.TestCase):
    def test_provider_equals_model_syntax_survives_colons_in_model_ids(self):
        """`=` separates, not `:` — Ollama model ids contain colons."""
        passes = panel_mod.parse_panel("deepseek=deepseek-v4-flash,ollama=qwen3:8b")
        self.assertEqual([p.provider for p in passes], ["deepseek", "ollama"])
        self.assertEqual(passes[1].model, "qwen3:8b")

    def test_a_bare_provider_uses_its_default_model(self):
        passes = panel_mod.parse_panel("claude,deepseek")
        self.assertEqual(passes[0].model, fc.DEFAULT_CLAUDE_MODEL)
        self.assertIsNone(passes[1].model)  # resolved by the provider spec

    def test_a_duplicate_pass_is_rejected(self):
        """Two identical passes are correlated repetition dressed as consensus.

        Worse than useless: the duplicate would raise `agreement` to 2 on every
        finding, making one model's opinion read as corroborated.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("deepseek=deepseek-v4-flash,deepseek=deepseek-v4-flash")
        self.assertIn("INDEPENDENT", str(ctx.exception))

    def test_a_panel_of_one_is_rejected(self):
        """`--panel deepseek` would certify as `external-layer-c-panel`.

        That name is read at the install gate as "several independent models
        looked". A single entry makes it mintable by exactly the single-critic
        pass whose false negative this module exists to stop — the label would
        promise corroboration that never happened.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("deepseek")
        self.assertIn("at least 2", str(ctx.exception))

    def test_unknown_provider_and_empty_spec_are_rejected(self):
        with self.assertRaises(ValueError):
            panel_mod.parse_panel("deepsek")
        with self.assertRaises(ValueError):
            panel_mod.parse_panel("  , ")


# ── critic_panel: the merge ───────────────────────────────────────────────────


class MergeFindingsTests(unittest.TestCase):
    def test_a_finding_from_one_pass_alone_survives_the_merge(self):
        """THE load-bearing test of this whole feature.

        The bug being fixed is a FALSE NEGATIVE — defects no critic reported. A
        majority vote would suppress exactly the finding that only one model was
        sharp enough to catch, making false negatives MORE likely. If this test
        ever fails, the panel has become a mute button and is worse than the
        single critic it replaced.
        """
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "the keyed answer is wrong")],
            "b": [],
            "c": [],
        })
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["qid"], "q1")
        self.assertEqual(merged[0]["agreement"], 1)
        self.assertEqual(merged[0]["sources"], ["a"])

    def test_agreement_counts_distinct_passes_on_the_same_qid_and_severity(self):
        """Agreement is grouped at (qid, severity), not per issue string.

        Two models essentially never phrase a defect identically, so a
        string-equality notion of agreement would report 1 for everything.
        """
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "answer B is actually correct")],
            "b": [_finding("q1", "the key points at the wrong option")],
        })
        self.assertEqual(len(merged), 2)          # union: both wordings kept
        self.assertTrue(all(f["agreement"] == 2 for f in merged))

    def test_near_verbatim_restatements_collapse_but_keep_both_sources(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "The keyed answer is wrong.")],
            "b": [_finding("q1", "the keyed answer is wrong")],
        })
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["sources"], ["a", "b"])
        self.assertEqual(merged[0]["agreement"], 2)

    def test_merging_keeps_the_worst_confidence_never_the_mildest(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "same issue", confidence="low")],
            "b": [_finding("q1", "same issue", confidence="high")],
        })
        self.assertEqual(merged[0]["confidence"], "high")

    def test_merging_prefers_the_more_specific_correction(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "same issue", correction="")],
            "b": [_finding("q1", "same issue",
                           correction="key should be option C, per NIST SP 800-63B")],
        })
        self.assertIn("800-63B", merged[0]["correction"])

    def test_different_severities_on_one_qid_stay_separate(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "wrong key", severity="wrong-answer")],
            "b": [_finding("q1", "sloppy wording", severity="nit")],
        })
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(f["agreement"] == 1 for f in merged))

    def test_merge_order_is_deterministic(self):
        per_pass = {
            "b": [_finding("q2", "beta"), _finding("q1", "alpha")],
            "a": [_finding("q3", "gamma")],
        }
        first = panel_mod.merge_findings(per_pass)
        second = panel_mod.merge_findings(dict(reversed(list(per_pass.items()))))
        self.assertEqual([f["qid"] for f in first], ["q1", "q2", "q3"])
        self.assertEqual(first, second)

    def test_solo_qids_flags_uncorroborated_questions_without_removing_them(self):
        merged = panel_mod.merge_findings({
            "a": [_finding("q1", "shared"), _finding("q2", "solo")],
            "b": [_finding("q1", "shared")],
        })
        solo = panel_mod.solo_qids(merged)
        self.assertEqual(solo, ["q2"])
        # The solo finding is still in the union — flagged, not filtered.
        self.assertIn("q2", [f["qid"] for f in merged])


# ── critic_panel: running passes ──────────────────────────────────────────────


class RunPanelTests(unittest.TestCase):
    QUESTIONS = [{"id": "q1", "prompt": "a"}, {"id": "q2", "prompt": "b"}]

    def _reply(self, findings, checked=2, model="m"):
        return cp.CriticReply(_critic_json(findings, checked), model, "deepseek")

    def test_a_failing_pass_does_not_abort_the_others(self):
        """A misconfigured cheap pass must not take the panel down.

        If it did, the rational response would be to stop adding passes — which
        is exactly the single-critic review this feature replaces.
        """
        calls = {"n": 0}

        def _run(provider, prompt, model, timeout):
            calls["n"] += 1
            if provider == "ollama":
                raise RuntimeError("connection refused")
            return self._reply([_finding("q1", "bad key")])

        passes = [panel_mod.PassSpec("ollama", "qwen3:8b"),
                  panel_mod.PassSpec("deepseek", "deepseek-v4-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["coverage_ok"])
        self.assertTrue(result["passes"][1]["coverage_ok"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(panel_mod.panel_coverage_ok(result))

    def test_a_preflight_failure_is_recorded_not_raised(self):
        passes = [panel_mod.PassSpec("deepseek", None)]
        with patch.object(cp, "preflight", return_value="DEEPSEEK_API_KEY is not set"):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["ok"])
        self.assertIn("DEEPSEEK_API_KEY", result["errors"][0])
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_unchecked_is_the_minimum_across_passes_not_the_sum(self):
        """One complete pass means the pack WAS reviewed in full.

        A second pass timing out cannot un-review it, so summing (or maxing)
        would invent uncovered questions that were in fact graded.
        """
        def _run(provider, prompt, model, timeout):
            if provider == "ollama":
                raise RuntimeError("timed out")
            return self._reply([], checked=2)

        passes = [panel_mod.PassSpec("ollama", "qwen3:8b"),
                  panel_mod.PassSpec("deepseek", "deepseek-v4-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertEqual(result["questions_unchecked"], 0)

    def test_panel_coverage_is_false_when_no_pass_completed(self):
        with patch.object(cp, "run", side_effect=RuntimeError("down")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("deepseek", "deepseek-v4-flash")], 12, 5)
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_progress_is_emitted_for_every_pass(self):
        """INV-1: a multi-pass network wait must never be a silent block."""
        events = []
        with patch.object(cp, "run", return_value=self._reply([])), \
             patch.object(cp, "preflight", return_value=None):
            panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("deepseek", "deepseek-v4-flash")], 12, 5,
                on_event=lambda kind, **info: events.append(kind))
        self.assertIn("pass_start", events)
        self.assertIn("batch", events)
        self.assertIn("pass_done", events)

    def test_summary_records_observed_models_for_the_certification(self):
        with patch.object(cp, "run",
                          return_value=self._reply([], model="deepseek-v4-flash-x")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("deepseek", "deepseek-v4-flash")], 12, 5)
        summary = panel_mod.panel_summary(result)
        self.assertEqual(summary["passes_attempted"], 1)
        self.assertEqual(summary["passes_completed"], 1)
        self.assertEqual(summary["passes"][0]["model_observed"],
                         "deepseek-v4-flash-x")
        self.assertEqual(summary["passes"][0]["model_requested"],
                         "deepseek-v4-flash")


# ── verify_pack integration ───────────────────────────────────────────────────


CLEAN_Q = {
    "id": "q1", "type": "multiple_choice", "topic": "math",
    "difficulty": "easy", "prompt": "What is 2+2?",
    "options": ["4", "5", "6", "7"], "answer": 0,
    "explanation": "Two plus two is four.",
}


class PanelCertificationTests(unittest.TestCase):
    """A panel run must certify HONESTLY: same bar, richer provenance."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pack = Path(self._tmp.name) / "pack.json"
        self.pack.write_text(json.dumps({
            "pack_id": "panel-test",
            "questions": [dict(CLEAN_Q)],
            "coverage_blueprint": [{"topic": "math", "min": 1}],
        }))

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv, provider_run):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cp, "run", side_effect=provider_run), \
             patch.object(cp, "preflight", return_value=None), \
             patch.object(fc, "run_claude",
                          side_effect=AssertionError("claude must not run")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _clean(self, model):
        def _run(provider, prompt, req_model, timeout):
            return cp.CriticReply(_critic_json([], checked=1), model, provider)
        return _run

    def test_a_clean_panel_run_certifies_under_the_panel_review_method(self):
        rc, out, _ = self._run(
            [str(self.pack), "--panel", "deepseek=deepseek-v4-flash,ollama=qwen3:8b"],
            self._clean("observed-model-1"))
        self.assertEqual(rc, 0)
        cert = json.loads(self.pack.read_text())["certification"]
        self.assertEqual(cert["review_method"], "external-layer-c-panel")
        self.assertIn(cert["review_method"], pack_cert.APPROVED_REVIEW_METHODS)

    def test_the_certification_is_accepted_by_the_install_gate(self):
        """A new review_method is worthless if certification_fresh rejects it."""
        rc, _, _ = self._run([str(self.pack), "--panel", "deepseek,ollama=qwen3:8b"],
                             self._clean("observed-model-1"))
        self.assertEqual(rc, 0)
        self.assertTrue(
            pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_the_certification_records_every_pass_that_ran(self):
        self._run([str(self.pack), "--panel", "deepseek,ollama=qwen3:8b"],
                  self._clean("observed-model-1"))
        cert = json.loads(self.pack.read_text())["certification"]
        panel = cert["critic_panel"]
        self.assertEqual(panel["passes_attempted"], 2)
        self.assertEqual(panel["passes_completed"], 2)
        self.assertEqual({p["provider"] for p in panel["passes"]},
                         {"deepseek", "ollama"})
        for p in panel["passes"]:
            self.assertEqual(p["model_observed"], "observed-model-1")

    def test_a_blocking_finding_from_a_single_pass_still_fails_the_gate(self):
        """Union semantics carried all the way to the verdict.

        One cheap model finding a wrong answer is enough to refuse
        certification. If a majority were required, this pack would ship.
        """
        def _run(provider, prompt, model, timeout):
            if provider == "deepseek":
                return cp.CriticReply(
                    _critic_json([_finding("q1", "the keyed answer is wrong")],
                                 checked=1), "m", provider)
            return cp.CriticReply(_critic_json([], checked=1), "m", provider)

        rc, out, _ = self._run(
            [str(self.pack), "--panel", "deepseek,ollama=qwen3:8b"], _run)
        self.assertEqual(rc, 2)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_a_degraded_panel_still_certifies_but_reports_the_failure(self):
        """One complete pass certifies; the dead pass is surfaced, not swallowed."""
        def _run(provider, prompt, model, timeout):
            if provider == "ollama":
                raise RuntimeError("connection refused")
            return cp.CriticReply(_critic_json([], checked=1), "m", provider)

        rc, out, _ = self._run(
            [str(self.pack), "--panel", "deepseek,ollama=qwen3:8b"], _run)
        self.assertEqual(rc, 0)
        self.assertIn("panel notes", out.lower())
        self.assertIn("connection refused", out)
        cert = json.loads(self.pack.read_text())["certification"]
        self.assertEqual(cert["critic_panel"]["passes_completed"], 1)
        self.assertEqual(cert["critic_panel"]["passes_attempted"], 2)

    def test_a_panel_where_every_pass_died_is_an_operational_error(self):
        def _run(provider, prompt, model, timeout):
            raise RuntimeError("everything is down")

        rc, _, err = self._run(
            [str(self.pack), "--panel", "deepseek,ollama=qwen3:8b"], _run)
        self.assertEqual(rc, 1)
        self.assertIn("every Layer-C panel pass failed", err)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_a_malformed_panel_spec_fails_before_any_provider_is_called(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cp, "run",
                          side_effect=AssertionError("no provider may run")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(self.pack), "--panel", "deepsek"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown critic provider", err.getvalue())


class WhoMayCertifyTests(unittest.TestCase):
    """Adding `--provider` must not make the certification cheaper to mint.

    Before the provider seam existed, `external-layer-c-strict` was reachable
    only through the project's designated external critic. `--provider` pointed
    Layer C at arbitrary endpoints; if that path kept minting the same
    review_method, the install gate could no longer tell a frontier-model
    certification from a 1B local one — or from an HTTP stub that answers
    `{"findings": []}` to everything. That is the self-attestation INV-7 was
    rewritten to refuse, and it would have been a NET WEAKENING of the gate
    shipped inside the change meant to strengthen it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pack = Path(self._tmp.name) / "pack.json"
        self.pack.write_text(json.dumps({
            "pack_id": "who-may-certify",
            "questions": [dict(CLEAN_Q)],
            "coverage_blueprint": [{"topic": "math", "min": 1}],
        }))

    def tearDown(self):
        self._tmp.cleanup()

    def _rubber_stamp(self, provider, prompt, model, timeout):
        """A critic that approves everything without looking — the threat model."""
        return cp.CriticReply(_critic_json([], checked=1), model, provider)

    def _run(self, argv, claude_stdout=None):
        out, err = io.StringIO(), io.StringIO()
        claude = (patch.object(fc, "run_claude", return_value=claude_stdout)
                  if claude_stdout is not None else
                  patch.object(fc, "run_claude",
                               side_effect=AssertionError("claude must not run")))
        with patch.object(cp, "run", side_effect=self._rubber_stamp), \
             patch.object(cp, "preflight", return_value=None), claude:
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _cert(self):
        return json.loads(self.pack.read_text()).get("certification")

    def test_a_single_non_default_provider_does_not_certify(self):
        rc, out, _ = self._run([str(self.pack), "--provider", "ollama",
                                "--model", "tiny:1b"])
        self.assertEqual(rc, 3)                 # reviewed, explicitly NOT certified
        self.assertIsNone(self._cert())
        self.assertFalse(
            pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_it_says_why_and_names_the_certifying_command(self):
        """An unexplained exit 3 is what sends someone hunting for a bypass."""
        _, out, _ = self._run([str(self.pack), "--provider", "ollama",
                               "--model", "tiny:1b"])
        self.assertIn("REVIEW PASSED", out)
        self.assertNotIn("PACK READY", out)
        self.assertIn("--panel", out)

    def test_a_non_default_provider_cannot_recertify_via_only_either(self):
        """`--only` has its own certification path; it must honour the same rule."""
        rc, _, _ = self._run([str(self.pack), "--provider", "ollama",
                              "--model", "tiny:1b", "--only", "q1"])
        self.assertEqual(rc, 3)
        self.assertIsNone(self._cert())

    def test_the_default_provider_still_certifies(self):
        """The control. The rule above must not break the ordinary path."""
        stdout = json.dumps({
            "type": "result", "result": _critic_json([], checked=1),
            "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}})
        rc, out, _ = self._run([str(self.pack)], claude_stdout=stdout)
        self.assertEqual(rc, 0)
        self.assertEqual(self._cert()["review_method"], "external-layer-c-strict")

    def test_a_two_pass_panel_of_cheap_providers_still_certifies(self):
        """Cheap providers are not distrusted — a SINGLE cheap pass is.

        The remedy the error message names has to actually work, or the rule
        just reads as "pay for Claude".
        """
        rc, _, _ = self._run(
            [str(self.pack), "--panel", "ollama=tiny:1b,deepseek"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._cert()["review_method"], "external-layer-c-panel")

    def test_a_one_entry_panel_is_refused_at_the_cli(self):
        """parse_panel enforces it; this pins the CLI wiring that calls it."""
        rc, _, err = self._run([str(self.pack), "--panel", "deepseek"])
        self.assertEqual(rc, 1)
        self.assertIn("at least 2", err)
        self.assertIsNone(self._cert())


if __name__ == "__main__":
    unittest.main()
