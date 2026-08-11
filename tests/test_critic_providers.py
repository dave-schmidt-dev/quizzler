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
import subprocess
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


def _chat_response(content: str, model: str = "served-model-1") -> dict:
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
            cp.get_spec("opencde")
        self.assertIn("unknown critic provider", str(ctx.exception))
        self.assertIn("opencode", str(ctx.exception))  # lists the real options

    def test_registry_exposes_the_documented_providers(self):
        names = cp.provider_names()
        for expected in ("claude", "opencode", "openai-compatible"):
            self.assertIn(expected, names)

    def test_base_url_env_override_is_used_when_set(self):
        spec = cp.get_spec("openai-compatible")
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}):
            self.assertEqual(cp.base_url(spec), "https://gw.example.com")
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_BASE_URL": ""}):
            self.assertIsNone(cp.base_url(spec))

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

    def test_variant_is_rejected_for_a_provider_that_does_not_support_one(self):
        """A caller bug, not a silent no-op — the value would otherwise be
        dropped and the caller would believe a higher effort was requested."""
        with self.assertRaises(ValueError) as ctx:
            cp.run("openai-compatible", "prompt", "gw-model", 5, variant="max")
        self.assertIn("--variant", str(ctx.exception))
        self.assertIn("opencode", str(ctx.exception))

    def test_variant_reaches_run_opencode_for_the_opencode_provider(self):
        with patch.object(cp, "run_opencode",
                          return_value=cp.CriticReply("{}", None, "opencode")) as ran:
            cp.run("opencode", "prompt", "ds-flash", 5, variant="max")
        ran.assert_called_once_with("prompt", "ds-flash", 5, variant="max")


# ── critic_providers: secret hygiene ──────────────────────────────────────────


class SecretHygieneTests(unittest.TestCase):
    """A leaked key is a rotation event, so these are correctness tests."""

    def test_redact_scrubs_a_live_key_out_of_error_text(self):
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": FAKE_KEY}):
            scrubbed = cp._redact(f"upstream said: bad token {FAKE_KEY} rejected")
        self.assertNotIn(FAKE_KEY, scrubbed)
        self.assertIn("«redacted»", scrubbed)

    def test_redact_does_not_wildcard_on_a_short_or_empty_key(self):
        """An empty env value must not turn every string into a redaction."""
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": ""}):
            self.assertEqual(cp._redact("nothing secret here"),
                             "nothing secret here")
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": "abc"}):
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
            "https://gw.example.com/chat/completions", 401, "Unauthorized",
            {}, io.BytesIO(body.encode()))
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": FAKE_KEY,
                                       "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run("openai-compatible", "prompt", "gw-model", 5)
        message = str(ctx.exception)
        self.assertNotIn(FAKE_KEY, message)
        self.assertIn("HTTP 401", message)

    def test_the_key_travels_in_a_header_and_never_in_the_body(self):
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = req.data.decode("utf-8")
            return _FakeResponse(_chat_response(_critic_json([])))

        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": FAKE_KEY,
                                       "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}), \
             patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            cp.run("openai-compatible", "grade these questions", "gw-model", 5)
        self.assertEqual(captured["auth"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, captured["body"])

    def test_missing_key_names_the_broker_and_never_the_legacy_paths(self):
        """The error must point at bws-secret-exec.

        ``bws-run`` and ``bws-get`` print secret values to a terminal and are
        barred from agent automation. An error message is documentation people
        actually read, so it must not send them down a path policy forbids.
        """
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": "",
                                       "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}):
            reason = cp.preflight("openai-compatible")
        self.assertIsNotNone(reason)
        self.assertIn("QUIZZLER_OPENAI_API_KEY", reason)
        self.assertIn("bws-secret-exec", reason)
        self.assertNotIn("bws-run", reason)
        self.assertNotIn("bws-get", reason)


# ── critic_providers: transport + observed model ──────────────────────────────


_OAI_ENV = {"QUIZZLER_OPENAI_API_KEY": "sk-testonly-oai",
            "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}


class ObservedModelTests(unittest.TestCase):
    """The certification records what ANSWERED, not what was asked for."""

    def test_openai_provider_reports_the_servers_model_not_the_request(self):
        # A gateway can route an alias to a different served model; the
        # response's own `model` field is what must be trusted.
        served = "actually-served-model-v2"
        with patch.dict("os.environ", _OAI_ENV), \
             patch("urllib.request.urlopen",
                   return_value=_FakeResponse(
                       _chat_response(_critic_json([]), model=served))):
            reply = cp.run("openai-compatible", "prompt", "gw-model", 5)
        self.assertEqual(reply.model, served)
        self.assertNotEqual(reply.model, "gw-model")
        self.assertEqual(reply.provider, "openai-compatible")

    def test_an_unreported_model_stays_none_rather_than_being_backfilled(self):
        """Unknown must be recorded as unknown.

        Substituting the requested id here would turn 'the provider told us
        nothing' into a confident provenance claim — self-attestation by a
        different route.
        """
        body = {"choices": [{"message": {"content": _critic_json([])}}]}
        with patch.dict("os.environ", _OAI_ENV), \
             patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            reply = cp.run("openai-compatible", "prompt", "gw-model", 5)
        self.assertIsNone(reply.model)

    def test_an_empty_completion_is_an_error_not_a_clean_pass(self):
        """A blank reply must never parse as 'checked everything, found nothing'."""
        with patch.dict("os.environ", _OAI_ENV), \
             patch("urllib.request.urlopen",
                   return_value=_FakeResponse(_chat_response("   "))):
            with self.assertRaises(RuntimeError):
                cp.run("openai-compatible", "prompt", "gw-model", 5)

    def test_a_provider_without_a_default_model_says_so(self):
        with patch.dict("os.environ", _OAI_ENV):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run("openai-compatible", "prompt", None, 5)
        self.assertIn("--model", str(ctx.exception))


class OpencodeProviderTests(unittest.TestCase):
    """`opencode run` is a subprocess, and every detail here was found the hard way."""

    def _proc(self, stdout="", stderr="", rc=0):
        return subprocess.CompletedProcess([], rc, stdout, stderr)

    def _events(self, *texts):
        """opencode's newline-delimited event stream."""
        lines = [json.dumps({"type": "step_start", "part": {"type": "step-start"}})]
        lines += [json.dumps({"type": "text", "part": {"type": "text", "text": t}})
                  for t in texts]
        lines.append(json.dumps({"type": "step_finish",
                                 "part": {"tokens": {"total": 10}, "cost": 0}}))
        return "\n".join(lines) + "\n"

    def test_stdin_is_closed_or_opencode_hangs_forever(self):
        """Not a tidiness choice: with an open stdin `opencode run` blocks.

        The first attempt at this provider returned 0 bytes after a 120s
        timeout for exactly this reason. A regression here does not fail
        loudly — it hangs the whole panel.
        """
        seen = {}

        def _fake(argv, **kwargs):
            seen.update(kwargs)
            seen["argv"] = argv
            return self._proc(self._events(_critic_json([])))

        with patch.object(subprocess, "run", side_effect=_fake):
            cp.run_opencode("prompt", "ds-flash", 30)
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)

    def test_the_reply_is_the_concatenation_of_the_text_events(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(self._events('{"findings"', "):[]}"))):
            reply = cp.run_opencode("prompt", "ds-flash", 30)
        self.assertEqual(reply.text, '{"findings"):[]}')
        self.assertEqual(reply.provider, "opencode")

    def test_the_observed_model_is_none_because_opencode_never_reports_one(self):
        """opencode's event stream carries no model field at all.

        Its SQLite store does record a `modelID`, but that is the string we
        passed in `-m` echoed back through a database — self-attestation by a
        longer route. Unknown is recorded as unknown.
        """
        with patch.object(subprocess, "run",
                          return_value=self._proc(self._events(_critic_json([])))):
            reply = cp.run_opencode("prompt", "ds-flash", 30)
        self.assertIsNone(reply.model)

    def test_a_bare_model_id_is_namespaced_and_a_qualified_one_is_not(self):
        for given, expected in [("deepseek-v4-flash-free",
                                 "opencode/deepseek-v4-flash-free"),
                                ("opencode-go/deepseek-v4-flash",
                                 "opencode-go/deepseek-v4-flash")]:
            with patch.object(subprocess, "run",
                              return_value=self._proc(
                                  self._events(_critic_json([])))) as ran:
                cp.run_opencode("prompt", given, 30)
            argv = ran.call_args[0][0]
            self.assertEqual(argv[argv.index("-m") + 1], expected)

    def test_a_nonzero_exit_raises_rather_than_reading_as_no_findings(self):
        """A dead pass must never be mistaken for a clean pass."""
        with patch.object(subprocess, "run",
                          return_value=self._proc("", "auth failed", rc=1)):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run_opencode("prompt", "ds-flash", 30)
        self.assertIn("exited 1", str(ctx.exception))

    def test_a_timeout_raises_rather_than_reading_as_no_findings(self):
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired([], 30)):
            with self.assertRaises(RuntimeError) as ctx:
                cp.run_opencode("prompt", "ds-flash", 30)
        self.assertIn("timed out", str(ctx.exception))

    def test_an_event_stream_with_no_text_is_an_error(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(self._events())):
            with self.assertRaises(RuntimeError):
                cp.run_opencode("prompt", "ds-flash", 30)

    def test_an_unparseable_log_line_does_not_fail_an_otherwise_good_pass(self):
        stream = "warning: something\n" + self._events(_critic_json([]))
        with patch.object(subprocess, "run", return_value=self._proc(stream)):
            reply = cp.run_opencode("prompt", "ds-flash", 30)
        self.assertIn("findings", reply.text)

    def test_a_variant_is_passed_through_as_its_own_flag(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(
                              self._events(_critic_json([])))) as ran:
            cp.run_opencode("prompt", "ds-flash", 30, variant="max")
        argv = ran.call_args[0][0]
        self.assertEqual(argv[argv.index("--variant") + 1], "max")

    def test_no_variant_flag_when_none_is_given(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(
                              self._events(_critic_json([])))) as ran:
            cp.run_opencode("prompt", "ds-flash", 30)
        self.assertNotIn("--variant", ran.call_args[0][0])


class PreflightTests(unittest.TestCase):
    def test_a_missing_base_url_is_reported_before_any_batch_runs(self):
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": "sk-testonly-oai",
                                       "QUIZZLER_OPENAI_BASE_URL": ""}):
            reason = cp.preflight("openai-compatible")
        self.assertIsNotNone(reason)
        self.assertIn("base URL", reason)

    def test_openai_compatible_with_a_key_present_preflights_clean(self):
        with patch.dict("os.environ", {"QUIZZLER_OPENAI_API_KEY": FAKE_KEY,
                                       "QUIZZLER_OPENAI_BASE_URL": "https://gw.example.com"}):
            self.assertIsNone(cp.preflight("openai-compatible"))


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

    def test_claude_rejects_a_variant_rather_than_ignoring_it(self):
        with patch.object(fc, "run_claude") as claude:
            with self.assertRaises(ValueError) as ctx:
                fc.run_critic("prompt", "claude-sonnet-5", 5, variant="max")
        claude.assert_not_called()
        self.assertIn("--variant", str(ctx.exception))

    def test_a_non_claude_provider_never_touches_the_claude_cli(self):
        def _boom(*a, **k):
            raise AssertionError("run_claude must not be called for another provider")

        with patch.object(fc, "run_claude", side_effect=_boom), \
             patch.object(cp, "run",
                          return_value=cp.CriticReply(_critic_json([]),
                                                      None,
                                                      "opencode")) as ran:
            reply = fc.run_critic("prompt", None, 5, provider="opencode")
        ran.assert_called_once()
        self.assertEqual(reply.provider, "opencode")

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
                                                      "served-model", "opencode")):
            result = fc.collect_findings([{"id": "q1"}], "ds-flash", 12, 5,
                                         provider="opencode")
        self.assertEqual(result["provider"], "opencode")
        self.assertEqual(result["model"], "served-model")
        self.assertEqual(result["errors"], [])


# ── critic_panel: parsing ─────────────────────────────────────────────────────


class ParsePanelTests(unittest.TestCase):
    def test_provider_equals_model_syntax_survives_colons_in_model_ids(self):
        """`=` separates, not `:` — some gateway model ids contain colons."""
        passes = panel_mod.parse_panel(
            "opencode=deepseek-v4-flash-free,openai-compatible=gw:v2")
        self.assertEqual([p.provider for p in passes],
                         ["opencode", "openai-compatible"])
        self.assertEqual(passes[1].model, "gw:v2")

    def test_a_bare_provider_uses_its_default_model(self):
        passes = panel_mod.parse_panel("claude,opencode")
        self.assertEqual(passes[0].model, fc.DEFAULT_CLAUDE_MODEL)
        self.assertIsNone(passes[1].model)  # resolved by the provider spec

    def test_a_duplicate_pass_is_rejected(self):
        """Two identical passes are correlated repetition dressed as consensus.

        Worse than useless: the duplicate would raise `agreement` to 2 on every
        finding, making one model's opinion read as corroborated.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("opencode=ds-flash,opencode=ds-flash")
        self.assertIn("INDEPENDENT", str(ctx.exception))

    def test_a_panel_of_one_is_rejected(self):
        """`--panel opencode` would certify as `external-layer-c-panel`.

        That name is read at the install gate as "several independent models
        looked". A single entry makes it mintable by exactly the single-critic
        pass whose false negative this module exists to stop — the label would
        promise corroboration that never happened.
        """
        with self.assertRaises(ValueError) as ctx:
            panel_mod.parse_panel("opencode")
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
        return cp.CriticReply(_critic_json(findings, checked), model, "opencode")

    def test_a_failing_pass_does_not_abort_the_others(self):
        """A misconfigured cheap pass must not take the panel down.

        If it did, the rational response would be to stop adding passes — which
        is exactly the single-critic review this feature replaces.
        """
        calls = {"n": 0}

        def _run(provider, prompt, model, timeout):
            calls["n"] += 1
            if provider == "openai-compatible":
                raise RuntimeError("connection refused")
            return self._reply([_finding("q1", "bad key")])

        passes = [panel_mod.PassSpec("openai-compatible", "gw-model"),
                  panel_mod.PassSpec("opencode", "ds-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["coverage_ok"])
        self.assertTrue(result["passes"][1]["coverage_ok"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(panel_mod.panel_coverage_ok(result))

    def test_a_preflight_failure_is_recorded_not_raised(self):
        passes = [panel_mod.PassSpec("opencode", None)]
        with patch.object(cp, "preflight", return_value="`opencode` CLI not on PATH"):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertFalse(result["passes"][0]["ok"])
        self.assertIn("opencode", result["errors"][0])
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_unchecked_is_the_minimum_across_passes_not_the_sum(self):
        """One complete pass means the pack WAS reviewed in full.

        A second pass timing out cannot un-review it, so summing (or maxing)
        would invent uncovered questions that were in fact graded.
        """
        def _run(provider, prompt, model, timeout):
            if provider == "openai-compatible":
                raise RuntimeError("timed out")
            return self._reply([], checked=2)

        passes = [panel_mod.PassSpec("openai-compatible", "gw-model"),
                  panel_mod.PassSpec("opencode", "ds-flash")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5)
        self.assertEqual(result["questions_unchecked"], 0)

    def test_panel_coverage_is_false_when_no_pass_completed(self):
        with patch.object(cp, "run", side_effect=RuntimeError("down")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5)
        self.assertFalse(panel_mod.panel_coverage_ok(result))

    def test_progress_is_emitted_for_every_pass(self):
        """INV-1: a multi-pass network wait must never be a silent block."""
        events = []
        with patch.object(cp, "run", return_value=self._reply([])), \
             patch.object(cp, "preflight", return_value=None):
            panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5,
                on_event=lambda kind, **info: events.append(kind))
        self.assertIn("pass_start", events)
        self.assertIn("batch", events)
        self.assertIn("pass_done", events)

    def test_a_panel_level_variant_reaches_opencode_and_skips_claude(self):
        """One --variant flag on a mixed panel must not blow up the claude pass.

        run_critic raises for claude+variant, so run_panel has to withhold the
        flag from any pass that cannot use it rather than forwarding it
        uniformly."""
        seen_variant = {}

        def _run(provider, prompt, model, timeout, variant=None):
            seen_variant[provider] = variant
            return self._reply([])

        claude_stdout = json.dumps({"type": "result",
                                    "result": _critic_json([]),
                                    "modelUsage": {"claude-sonnet-5": {}}})
        passes = [panel_mod.PassSpec("opencode", "ds-flash"),
                  panel_mod.PassSpec("claude", "claude-sonnet-5")]
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None), \
             patch.object(fc, "run_claude", return_value=claude_stdout):
            result = panel_mod.run_panel(self.QUESTIONS, passes, 12, 5,
                                         variant="max")
        self.assertEqual(seen_variant["opencode"], "max")
        self.assertTrue(result["passes"][1]["ok"])  # claude pass did not error

    def test_summary_records_observed_models_for_the_certification(self):
        with patch.object(cp, "run",
                          return_value=self._reply([], model="ds-flash-x")), \
             patch.object(cp, "preflight", return_value=None):
            result = panel_mod.run_panel(
                self.QUESTIONS,
                [panel_mod.PassSpec("opencode", "ds-flash")], 12, 5)
        summary = panel_mod.panel_summary(result)
        self.assertEqual(summary["passes_attempted"], 1)
        self.assertEqual(summary["passes_completed"], 1)
        self.assertEqual(summary["passes"][0]["model_observed"],
                         "ds-flash-x")
        self.assertEqual(summary["passes"][0]["model_requested"],
                         "ds-flash")

    def test_subject_reaches_the_actual_critic_prompt(self):
        """run_panel must forward `subject` all the way through
        collect_findings/_run_one_batch/build_prompt into what the provider
        actually receives — not just accept the parameter and drop it."""
        captured = {}

        def _run(provider, prompt, model, timeout):
            captured["prompt"] = prompt
            return self._reply([])

        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            panel_mod.run_panel(
                self.QUESTIONS, [panel_mod.PassSpec("opencode", "ds-flash")],
                12, 5, subject="CISSP")
        self.assertIn("CISSP", captured["prompt"])
        self.assertNotIn("Security+", captured["prompt"])


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
        """Every pass clean, each reporting a DISTINCT observed model.

        Distinct because that is what an independent panel actually looks like:
        two different providers answer with two different model ids. A fixture
        that reported one id for every pass would be describing correlated
        repetition, and `duplicate_observed_models` would (correctly) refuse to
        certify it — see `test_two_passes_served_by_one_model_do_not_certify`.
        """
        def _run(provider, prompt, req_model, timeout):
            return cp.CriticReply(_critic_json([], checked=1),
                                  f"{provider}-{model}", provider)
        return _run

    def _clean_same_model(self, model):
        """Every pass clean and served by THE SAME model — a fake panel."""
        def _run(provider, prompt, req_model, timeout):
            return cp.CriticReply(_critic_json([], checked=1), model, provider)
        return _run

    def test_a_clean_panel_run_certifies_under_the_panel_review_method(self):
        rc, out, _ = self._run(
            [str(self.pack), "--panel", "opencode=ds-flash,openai-compatible=gw-model"],
            self._clean("observed-model-1"))
        self.assertEqual(rc, 0)
        cert = json.loads(self.pack.read_text())["certification"]
        self.assertEqual(cert["review_method"], "external-layer-c-panel")
        self.assertIn(cert["review_method"], pack_cert.APPROVED_REVIEW_METHODS)

    def test_the_certification_is_accepted_by_the_install_gate(self):
        """A new review_method is worthless if certification_fresh rejects it."""
        rc, _, _ = self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"],
            self._clean("observed-model-1"))
        self.assertEqual(rc, 0)
        self.assertTrue(
            pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_the_certification_records_every_pass_that_ran(self):
        self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"],
            self._clean("observed-model-1"))
        cert = json.loads(self.pack.read_text())["certification"]
        panel = cert["critic_panel"]
        self.assertEqual(panel["passes_attempted"], 2)
        self.assertEqual(panel["passes_completed"], 2)
        self.assertEqual({p["provider"] for p in panel["passes"]},
                         {"opencode", "openai-compatible"})
        self.assertEqual({p["model_observed"] for p in panel["passes"]},
                         {"opencode-observed-model-1",
                          "openai-compatible-observed-model-1"})

    def test_two_passes_served_by_one_model_do_not_certify(self):
        """A roster is not independence.

        `--panel opencode=ds-flash,openai-compatible=gw-model` names two
        distinct passes and clears the duplicate-label check, but if both end
        up served by the same underlying model (e.g. a gateway routing two
        aliases to one backend), the roster LOOKS independent and is not.
        Nothing in the roster shows it — only the observed ids do, and they
        are only known after the run. Certifying here would mint
        `external-layer-c-panel` from correlated repetition, which is the
        one-entry-panel bug with extra steps.
        """
        rc, out, _ = self._run(
            [str(self.pack), "--panel", "opencode=ds-flash,openai-compatible=gw-model"],
            self._clean_same_model("actually-one-model-v3"))
        self.assertEqual(rc, 3)                       # REVIEW PASSED, not certified
        self.assertNotIn("certification", json.loads(self.pack.read_text()))
        self.assertIn("not independent", out)
        self.assertIn("actually-one-model-v3", out)

    def test_an_unreported_model_does_not_count_as_a_duplicate(self):
        """opencode reports no model. Two nulls are not proof of sameness.

        Treating unknown as duplicate would make every opencode-only panel
        uncertifiable on evidence nobody has. `parse_panel`'s distinct-request
        rule is the guarantee available for providers that stay silent.
        """
        def _run(provider, prompt, req_model, timeout):
            return cp.CriticReply(_critic_json([], checked=1), None, provider)
        rc, _, _ = self._run(
            [str(self.pack), "--panel", "opencode=ds-flash,opencode=mimo"], _run)
        self.assertEqual(rc, 0)
        cert = json.loads(self.pack.read_text())["certification"]
        self.assertEqual(cert["review_method"], "external-layer-c-panel")

    def test_a_blocking_finding_from_a_single_pass_still_fails_the_gate(self):
        """Union semantics carried all the way to the verdict.

        One cheap model finding a wrong answer is enough to refuse
        certification. If a majority were required, this pack would ship.
        """
        def _run(provider, prompt, model, timeout):
            if provider == "opencode":
                return cp.CriticReply(
                    _critic_json([_finding("q1", "the keyed answer is wrong")],
                                 checked=1), "m", provider)
            return cp.CriticReply(_critic_json([], checked=1), "m", provider)

        rc, out, _ = self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"], _run)
        self.assertEqual(rc, 2)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_a_degraded_panel_still_certifies_but_reports_the_failure(self):
        """One complete pass certifies; the dead pass is surfaced, not swallowed."""
        def _run(provider, prompt, model, timeout):
            if provider == "openai-compatible":
                raise RuntimeError("connection refused")
            return cp.CriticReply(_critic_json([], checked=1), "m", provider)

        rc, out, _ = self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"], _run)
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
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"], _run)
        self.assertEqual(rc, 1)
        self.assertIn("every Layer-C panel pass failed", err)
        self.assertNotIn("certification", json.loads(self.pack.read_text()))

    def test_strict_panel_drops_source_directive_but_keeps_subject(self):
        """2026-08-11: the panel path must honor the SAME --strict asymmetry
        as the single-critic path (drop source_directive, keep subject) —
        exercised end-to-end here through the real
        _layer_c_inputs -> critic_panel.run_panel -> collect_findings ->
        build_prompt chain, not a mocked boundary, so nothing in that chain
        can silently drop or swap the two."""
        self.pack.write_text(json.dumps({
            "pack_id": "panel-test",
            "questions": [dict(CLEAN_Q)],
            "coverage_blueprint": [{"topic": "math", "min": 1}],
            "source_directive": "Trust the author's framing.",
            "subject": "CISSP",
        }))
        captured = {}

        def _run(provider, prompt, model, timeout):
            captured[provider] = prompt
            # Distinct observed models per provider — a shared model id would
            # trip the unrelated duplicate_observed_models check (INV-7) and
            # make this a "not independent" panel instead of exercising the
            # --strict asymmetry this test is actually about.
            return cp.CriticReply(_critic_json([], checked=1), f"{provider}-m",
                                  provider)

        rc, _, _ = self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model"], _run)
        self.assertEqual(rc, 0)
        self.assertEqual(len(captured), 2, "both panel providers must have run")
        for prompt in captured.values():
            self.assertIn("Trust the author's framing.", prompt)
            self.assertIn("CISSP", prompt)

        captured.clear()
        rc, _, _ = self._run(
            [str(self.pack), "--panel", "opencode,openai-compatible=gw-model",
             "--strict"], _run)
        self.assertEqual(rc, 0)
        self.assertEqual(len(captured), 2, "both panel providers must have run")
        for prompt in captured.values():
            self.assertNotIn("Trust the author's framing.", prompt)
            self.assertIn("CISSP", prompt)

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
        rc, out, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                                "--model", "tiny:1b"])
        self.assertEqual(rc, 3)                 # reviewed, explicitly NOT certified
        self.assertIsNone(self._cert())
        self.assertFalse(
            pack_cert.certification_fresh(json.loads(self.pack.read_text())))

    def test_it_says_why_and_names_the_certifying_command(self):
        """An unexplained exit 3 is what sends someone hunting for a bypass."""
        _, out, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                               "--model", "tiny:1b"])
        self.assertIn("REVIEW PASSED", out)
        self.assertNotIn("PACK READY", out)
        self.assertIn("--panel", out)

    def test_a_non_default_provider_cannot_recertify_via_only_either(self):
        """`--only` has its own certification path; it must honour the same rule."""
        rc, _, _ = self._run([str(self.pack), "--provider", "openai-compatible",
                              "--model", "tiny:1b", "--only", "q1"])
        self.assertEqual(rc, 3)
        self.assertIsNone(self._cert())

    def test_variant_is_rejected_at_the_cli_for_the_default_provider(self):
        """--variant with no --panel and the default (claude) provider is a
        caller error caught before any critic runs, not a silent no-op."""
        rc, _, err = self._run([str(self.pack), "--variant", "max"])
        self.assertEqual(rc, 1)
        self.assertIn("--variant", err)
        self.assertIn("opencode", err)
        self.assertIsNone(self._cert())

    def test_variant_is_accepted_at_the_cli_for_the_opencode_provider(self):
        seen = {}

        def _run(provider, prompt, model, timeout, variant=None):
            seen["variant"] = variant
            return cp.CriticReply(_critic_json([], checked=1), model, provider)

        out, err = io.StringIO(), io.StringIO()
        with patch.object(cp, "run", side_effect=_run), \
             patch.object(cp, "preflight", return_value=None):
            with redirect_stdout(out), redirect_stderr(err):
                rc = vp.main([str(self.pack), "--provider", "opencode",
                             "--variant", "max"])
        self.assertEqual(rc, 3)  # reviews, does not certify — unrelated to variant
        self.assertEqual(seen["variant"], "max")

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
            [str(self.pack), "--panel", "openai-compatible=tiny-1b,opencode"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._cert()["review_method"], "external-layer-c-panel")

    def test_what_the_gate_accepts_equals_what_the_gate_can_write(self):
        """No accepted-but-unwritable review method.

        A method `certification_fresh` honours but no code path produces is a
        cert shape only a hand-edit could have made — the gate would trust it
        precisely because nothing legitimate creates it. This caught the dead
        `external-layer-c-standard` entry.
        """
        self.assertEqual(vp.CERTIFYING_REVIEW_METHODS,
                         pack_cert.APPROVED_REVIEW_METHODS)

    def test_a_one_entry_panel_is_refused_at_the_cli(self):
        """parse_panel enforces it; this pins the CLI wiring that calls it."""
        rc, _, err = self._run([str(self.pack), "--panel", "opencode"])
        self.assertEqual(rc, 1)
        self.assertIn("at least 2", err)
        self.assertIsNone(self._cert())


if __name__ == "__main__":
    unittest.main()
