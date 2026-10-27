"""Unit tests for the multi-provider Layer-C critic.

Covers the provider registry, transport, secret hygiene, and the two real
provider adapters. NO real LLM, network, or subprocess call happens anywhere
here: the HTTP providers are mocked at ``urllib.request.urlopen`` and the CLIs
are mocked at ``subprocess.run``.

Run from the project root::

    python3 -m unittest tests.test_critic_providers -v
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tests.critic_provider_fixtures import (
    _FakeResponse,
    _OAI_ENV,
    _chat_response,
    _critic_json,
    FAKE_KEY,
    cp,
)


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

    def setUp(self):
        binary = patch.object(cp.shutil, "which", return_value="/mock/opencode")
        binary.start()
        self.addCleanup(binary.stop)

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
            cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)

    def test_snapshot_override_is_child_only_and_preserves_invocation(self):
        seen = {}
        parent_config = "{\"existing\": true}"
        original_config = os.environ.get("OPENCODE_CONFIG_CONTENT")

        def _fake(argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)
            self.assertEqual(os.environ["OPENCODE_CONFIG_CONTENT"], parent_config)
            return self._proc(self._events(_critic_json([])))

        with patch.object(cp.shutil, "which", return_value="/usr/bin/opencode"), \
             patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": parent_config}), \
             patch.object(subprocess, "run", side_effect=_fake):
            cp._run_opencode_direct("prompt", "ds-flash", 30, variant="max")

        self.assertEqual(seen["env"]["OPENCODE_CONFIG_CONTENT"],
                         '{"snapshot": false}')
        if original_config is None:
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", os.environ)
        else:
            self.assertEqual(os.environ["OPENCODE_CONFIG_CONTENT"], original_config)
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)
        self.assertEqual(seen["argv"][:5],
                         ["/usr/bin/opencode", "run", "--pure", "--format", "json"])
        self.assertEqual(seen["argv"][seen["argv"].index("-m") + 1],
                         "opencode/ds-flash")
        self.assertEqual(seen["argv"][seen["argv"].index("--variant") + 1], "max")
        self.assertEqual(seen["argv"][-1], "prompt")

    def test_the_reply_is_the_concatenation_of_the_text_events(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(self._events('{"findings"', "):[]}"))):
            reply = cp._run_opencode_direct("prompt", "ds-flash", 30)
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
            reply = cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertIsNone(reply.model)

    def test_a_bare_model_id_is_namespaced_and_a_qualified_one_is_not(self):
        for given, expected in [("deepseek-v4-flash-free",
                                 "opencode/deepseek-v4-flash-free"),
                                ("opencode-go/deepseek-v4-flash",
                                 "opencode-go/deepseek-v4-flash")]:
            with patch.object(subprocess, "run",
                              return_value=self._proc(
                                  self._events(_critic_json([])))) as ran:
                cp._run_opencode_direct("prompt", given, 30)
            argv = ran.call_args[0][0]
            self.assertEqual(argv[argv.index("-m") + 1], expected)

    def test_a_nonzero_exit_raises_rather_than_reading_as_no_findings(self):
        """A dead pass must never be mistaken for a clean pass."""
        with patch.object(subprocess, "run",
                          return_value=self._proc("", "auth failed", rc=1)):
            with self.assertRaises(RuntimeError) as ctx:
                cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertIn("exited 1", str(ctx.exception))

    def test_a_timeout_raises_rather_than_reading_as_no_findings(self):
        with patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired([], 30)):
            with self.assertRaises(RuntimeError) as ctx:
                cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertIn("timed out", str(ctx.exception))

    def test_an_event_stream_with_no_text_is_an_error(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(self._events())):
            with self.assertRaises(RuntimeError):
                cp.run_opencode("prompt", "ds-flash", 30)

    def test_an_unparseable_log_line_does_not_fail_an_otherwise_good_pass(self):
        stream = "warning: something\n" + self._events(_critic_json([]))
        with patch.object(subprocess, "run", return_value=self._proc(stream)):
            reply = cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertIn("findings", reply.text)

    def test_a_variant_is_passed_through_as_its_own_flag(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(
                              self._events(_critic_json([])))) as ran:
            cp._run_opencode_direct("prompt", "ds-flash", 30, variant="max")
        argv = ran.call_args[0][0]
        self.assertEqual(argv[argv.index("--variant") + 1], "max")

    def test_no_variant_flag_when_none_is_given(self):
        with patch.object(subprocess, "run",
                          return_value=self._proc(
                              self._events(_critic_json([])))) as ran:
            cp._run_opencode_direct("prompt", "ds-flash", 30)
        self.assertNotIn("--variant", ran.call_args[0][0])

    def test_public_transport_uses_an_isolated_worker_session(self):
        envelope = json.dumps({"ok": True, "text": _critic_json([])})
        with patch.object(subprocess, "run",
                          return_value=self._proc(envelope)) as ran:
            reply = cp.run_opencode("prompt", "ds-flash", 30, variant="max")

        argv = ran.call_args[0][0]
        kwargs = ran.call_args.kwargs
        self.assertEqual(argv[-1], "--opencode-worker")
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(json.loads(kwargs["input"]), {
            "prompt": "prompt", "model": "ds-flash", "timeout": 30,
            "variant": "max"})
        self.assertEqual(reply.text, _critic_json([]))

    def test_public_transport_fails_closed_on_worker_signal(self):
        with patch.object(subprocess, "run", return_value=self._proc("", rc=-15)):
            with self.assertRaisesRegex(RuntimeError, "isolated OpenCode worker failed"):
                cp.run_opencode("prompt", "ds-flash", 30)


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


class CodexProviderTests(unittest.TestCase):
    """Codex verifier is isolated and never self-attests its requested model."""

    def setUp(self):
        binary = patch.object(cp.shutil, "which", return_value="/mock/codex")
        binary.start()
        self.addCleanup(binary.stop)

    def _proc(self, stdout="", stderr="", rc=0):
        return subprocess.CompletedProcess([], rc, stdout, stderr)

    def test_codex_exec_is_ephemeral_read_only_and_uses_requested_effort(self):
        def _fake(argv, **kwargs):
            output = Path(argv[argv.index("--output-last-message") + 1])
            output.write_text(_critic_json([]), encoding="utf-8")
            return self._proc()

        with patch.object(subprocess, "run", side_effect=_fake) as ran:
            reply = cp.run("codex", "prompt", "gpt-test", 30, variant="high")
        argv = ran.call_args.args[0]
        self.assertIn("--ephemeral", argv)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-test")
        self.assertIn("model_reasoning_effort=high", argv)
        self.assertEqual(ran.call_args.kwargs["input"], "prompt")
        self.assertIsNone(reply.model)
        self.assertEqual(reply.provider, "codex")

    def test_codex_output_file_is_removed_after_success(self):
        seen = {}

        def _fake(argv, **kwargs):
            path = Path(argv[argv.index("--output-last-message") + 1])
            seen["path"] = path
            path.write_text(_critic_json([]), encoding="utf-8")
            return self._proc()

        with patch.object(subprocess, "run", side_effect=_fake):
            cp.run("codex", "prompt", "gpt-test", 30)
        self.assertFalse(seen["path"].exists())

    def test_codex_preflight_requires_only_the_cli(self):
        with patch.object(cp.shutil, "which", return_value="/usr/bin/codex"):
            self.assertIsNone(cp.preflight("codex"))


if __name__ == "__main__":
    unittest.main()
