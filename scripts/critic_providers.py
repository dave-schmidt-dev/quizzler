#!/usr/bin/env python3
"""Layer-C critic PROVIDERS — the seam between the critic and whatever model runs it.

Why this module exists
----------------------
Layer C used to be hardwired to exactly one call: ``claude -p --output-format
json``. That made a full-pack review a multi-hour, frontier-priced serial grind,
which in practice meant it got run ONCE — and one model's one opinion became the
entire factual gate. The SY0-701 post-mortem is what that costs: a single pass
missed 115 criticals and the pack certified clean.

The fix is structural, not a model swap. A gate that rests on one model's one
pass has no way to distinguish "looked hard and found nothing" from "didn't
really look". Several cheap, INDEPENDENT passes — different vendors, different
weights, different failure modes — miss in different places, and where they
disagree is signal a single pass cannot produce at any price. This module is the
seam that makes "several independent passes" expressible at all;
:mod:`critic_panel` is what runs them.

Provider contract
-----------------
A provider is ``call(prompt, model, timeout) -> CriticReply``. Two properties are
load-bearing for the certification:

* ``CriticReply.model`` is the model the PROVIDER SAID it used, read out of its
  own response body — never the string the caller asked for. A certification that
  records the *requested* model is self-attestation, and self-attestation is
  precisely what INV-7 exists to stop. A provider that cannot report its own
  model returns ``None`` here, and callers must record the unknown rather than
  substitute the request.
* Failures raise ``RuntimeError`` with a short, already-redacted message. Every
  caller in :mod:`factcheck_pack` treats ``RuntimeError`` as a batch error that
  counts against coverage, so a provider that dies quietly can never be mistaken
  for a provider that found nothing.

Secrets
-------
**No registered provider requires this repo to handle a secret.** ``local`` is a
keyless loopback server, ``opencode`` and ``claude`` each hold their own
credentials in their own stores, and ``ollama`` is local. The best secret
handling is not handling one.

``openai-compatible`` remains for the day some gateway does need a key, and the
rules for that day are enforced in code: keys are read from the environment only
and are NEVER logged, echoed, embedded in an exception, or written to a report.
The environment is expected to be populated by the ``bws-secret-exec`` broker
(see ``docs/CRITIC_PROVIDERS.md``); this module neither reads BWS nor touches a
key beyond placing it in one ``Authorization`` header. :func:`_redact` scrubs key
material out of any error text that a server might echo back, so an upstream
service cannot leak the key into a transcript by reflecting the request.

Adding a provider
-----------------
Append a :class:`ProviderSpec` to :data:`PROVIDERS`. If it speaks the OpenAI
chat-completions shape (most do), ``kind="openai"`` needs no new code — only a
base URL and the name of the env var holding its key.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# The repo root — passed to `opencode --dir` so the repo-local critic agent is
# found no matter what directory verify_pack.py was invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Local, no key, no cost — the provider that makes the panel testable on a laptop
# with nothing registered anywhere.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# llama.cpp's `llama-server` speaks OpenAI chat-completions at /v1 with NO key.
# It is the local backend that actually matches this machine: the models here are
# GGUF files (gemma-4-26B, gemma-4-12b, Nemotron-3-Nano-30B), not Ollama pulls.
# Crucially it reports the *loaded GGUF path* in the response's `model` field, so
# a local pass yields real provenance rather than an echo of the request.
DEFAULT_LLAMA_SERVER_URL = "http://127.0.0.1:8080/v1"

# `opencode` reaches DeepSeek and several other models on its free tier through
# its OWN credential store (~/.local/share/opencode/auth.json). Quizzler never
# sees a key for it, which is why there is no api_key_env below and no broker in
# the path: there is no secret here for this repo to handle.
DEFAULT_OPENCODE_MODEL = "deepseek-v4-flash-free"
OPENCODE_DEFAULT_NAMESPACE = "opencode"

# opencode's stock `build` agent carries a ~16k-token system prompt and a full
# tool suite into every batch. The repo-local agent at .opencode/agent/ strips
# the tools and cuts that to ~10k, which is latency this critic has no use for.
OPENCODE_AGENT = "pack-critic"

# Cheap models drift toward prose and fences without it; `extract_findings`
# tolerates both, but constrained decoding removes a whole class of retry.
# Safe to request here because PROMPT_HEADER already contains the literal word
# "JSON" ("Output ONLY a JSON object"), which OpenAI-compatible json_object mode
# requires of the prompt.
JSON_MODE_DEFAULT = True


@dataclass(frozen=True)
class CriticReply:
    """One provider's answer to one critic prompt.

    ``text`` is the model's reply with any provider envelope ALREADY removed, so
    :func:`factcheck_pack.extract_findings` can parse it directly regardless of
    which provider produced it. ``model`` is the observed model id reported by
    the provider itself (``None`` when the provider does not report one) — see
    the module docstring on why this must never be back-filled from the request.
    """

    text: str
    model: str | None
    provider: str


@dataclass(frozen=True)
class ProviderSpec:
    """Declarative description of one critic backend.

    ``kind`` selects the transport:
      * ``"claude-cli"``   — subprocess to the ``claude`` CLI. Dispatched by
        :func:`factcheck_pack.run_critic`, NOT by :func:`run` — see :func:`run`.
      * ``"opencode-cli"`` — subprocess to the ``opencode`` CLI (:func:`run_opencode`).
      * ``"ollama"``       — local Ollama HTTP API (``/api/generate``).
      * ``"openai"``       — any OpenAI-compatible ``/chat/completions`` endpoint.

    ``default_model`` of ``None`` means the provider has no sensible default and
    ``--model`` is required: guessing a model id for a local Ollama install (or
    an unknown OpenAI-compatible gateway) would produce a confusing 404 instead
    of an actionable message.

    ``api_key_env`` of ``None`` means the provider needs no key from this repo —
    either it is local and keyless (``local``) or it holds its own credentials
    outside Quizzler (``opencode``). That is a real distinction, not a missing
    field: a provider with no ``api_key_env`` never has a secret to mishandle.
    """

    name: str
    kind: str
    description: str
    default_model: str | None = None
    base_url_env: str | None = None
    default_base_url: str | None = None
    api_key_env: str | None = None
    json_mode: bool = JSON_MODE_DEFAULT


PROVIDERS: dict[str, ProviderSpec] = {
    "claude": ProviderSpec(
        name="claude",
        kind="claude-cli",
        description="Anthropic `claude` CLI (subscription/API auth handled by the CLI)",
        default_model=None,  # the CLI's own default is deliberate; don't override
        json_mode=False,     # --output-format json already frames the reply
    ),
    "ollama": ProviderSpec(
        name="ollama",
        kind="ollama",
        description="Local Ollama server — no key, no network, no per-token cost",
        default_model=None,  # entirely install-dependent; require --model
        base_url_env="QUIZZLER_OLLAMA_URL",
        default_base_url=DEFAULT_OLLAMA_URL,
    ),
    "local": ProviderSpec(
        name="local",
        kind="openai",
        description="Local llama.cpp `llama-server` (GGUF weights) — no key, no cost",
        # llama-server serves whichever GGUF was loaded and IGNORES the requested
        # id, so --model is required as the operator's statement of what they
        # loaded. The cert records it next to the gguf path the server reports;
        # a mismatch between the two is then visible rather than assumed away.
        default_model=None,
        base_url_env="QUIZZLER_LOCAL_URL",
        default_base_url=DEFAULT_LLAMA_SERVER_URL,
        api_key_env=None,          # keyless by design; see _require_key
    ),
    "opencode": ProviderSpec(
        name="opencode",
        kind="opencode-cli",
        description="`opencode` CLI — DeepSeek Flash V4 and other free-tier models",
        default_model=DEFAULT_OPENCODE_MODEL,
        api_key_env=None,          # opencode holds its own credentials
        json_mode=False,           # no request body to set response_format on
    ),
    "openai-compatible": ProviderSpec(
        name="openai-compatible",
        kind="openai",
        description="Any OpenAI-compatible endpoint; set QUIZZLER_OPENAI_BASE_URL",
        default_model=None,
        base_url_env="QUIZZLER_OPENAI_BASE_URL",
        default_base_url=None,
        api_key_env="QUIZZLER_OPENAI_API_KEY",
    ),
}

# How a caller is told to populate a key. Named here ONCE so every error message
# points at the same broker. It names `bws-secret-exec` and nothing else on
# purpose: an error message is documentation people act on immediately, and the
# legacy `bws-run` / `bws-get` paths print secret values to a terminal. Not even
# mentioning them to warn against them — a half-read error is how a forbidden
# command gets copy-pasted.
KEY_HELP = ("populate it with `bws-secret-exec <consumer> -- <command>` "
            "(see docs/CRITIC_PROVIDERS.md)")


def provider_names() -> list[str]:
    """Registered provider names, sorted — the choices for ``--provider``."""
    return sorted(PROVIDERS)


def get_spec(name: str) -> ProviderSpec:
    """Look up a provider spec by name.

    Raises:
        ValueError: If ``name`` is not registered. Deliberately not a silent
            fallback to ``claude``: a typo'd ``--provider`` that quietly ran the
            expensive default would misreport which model reviewed the pack.
    """
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"unknown critic provider {name!r}; known: {', '.join(provider_names())}"
        ) from None


def base_url(spec: ProviderSpec) -> str | None:
    """Effective base URL: the ``base_url_env`` override, else the spec default."""
    if spec.base_url_env:
        override = os.environ.get(spec.base_url_env, "").strip()
        if override:
            return override
    return spec.default_base_url


def _redact(text: str) -> str:
    """Scrub any registered provider key out of ``text``.

    Defense in depth. Nothing here intentionally puts a key in a message, but an
    upstream service can echo a request back in an error body, and a URL can be
    mistyped with a key in the query string. One accidental leak into a log or a
    transcript is a rotation event, so the cost of scrubbing unconditionally is
    trivially worth it. The ``len >= 8`` floor keeps a short or empty env value
    from turning into a wildcard that redacts ordinary text.
    """
    for spec in PROVIDERS.values():
        if not spec.api_key_env:
            continue
        value = os.environ.get(spec.api_key_env, "")
        if len(value) >= 8 and value in text:
            text = text.replace(value, "«redacted»")
    return text


def _safe_url(url: str) -> str:
    """A URL trimmed to scheme://host/path — query and userinfo dropped.

    Some gateways accept a key as a query parameter. Errors quote URLs, so strip
    everything that could carry credentials before the string reaches a message.
    """
    without_query = url.split("?", 1)[0]
    if "://" in without_query:
        scheme, rest = without_query.split("://", 1)
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[1]
        return f"{scheme}://{rest}"
    return without_query


def _post_json(url: str, payload: dict, timeout: int,
               headers: dict[str, str] | None = None) -> dict:
    """POST ``payload`` as JSON and return the decoded response object.

    stdlib ``urllib`` only — Quizzler has no third-party runtime dependencies and
    a QA tool is not the place to acquire the first one. ``urlopen`` blocks on a
    socket and so releases the GIL, which is what lets ``collect_findings`` run
    provider calls in a thread pool exactly as it does subprocess calls.

    Raises:
        RuntimeError: On any transport, status, or decode failure, with a short
            redacted message. Never leaks the request body (it contains the
            prompt) or any header (it may contain a key).
    """
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - a body we cannot read is not the error
            detail = ""
        raise RuntimeError(
            f"HTTP {e.code} from {_safe_url(url)}: {_redact(detail).strip()}"
        ) from None
    except urllib.error.URLError as e:
        # URLError wraps socket.timeout (TimeoutError) as well as DNS/refused.
        reason = e.reason
        if isinstance(reason, TimeoutError):
            raise RuntimeError(
                f"request to {_safe_url(url)} timed out after {timeout}s") from None
        raise RuntimeError(
            f"cannot reach {_safe_url(url)}: {_redact(str(reason))}") from None
    except TimeoutError:
        raise RuntimeError(
            f"request to {_safe_url(url)} timed out after {timeout}s") from None
    except OSError as e:
        raise RuntimeError(
            f"cannot reach {_safe_url(url)}: {_redact(str(e))}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"non-JSON response from {_safe_url(url)}: {_redact(raw[:200])!r}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected response shape from {_safe_url(url)}")
    return data


def _get_json(url: str, timeout: int) -> dict:
    """GET a JSON object. Same redaction rules as :func:`_post_json`.

    Used only by preflight probes, which must be cheap and must never raise
    through to a caller that just wanted to know whether a provider is usable.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        raise RuntimeError(_redact(str(e))) from None
    return data if isinstance(data, dict) else {}


def _resolve_model(spec: ProviderSpec, model: str | None) -> str:
    """The model id to request, or a clear error naming what to pass.

    Raises:
        RuntimeError: When neither ``--model`` nor a spec default is available.
    """
    resolved = (model or spec.default_model or "").strip()
    if not resolved:
        raise RuntimeError(
            f"provider {spec.name!r} has no default model; pass --model "
            f"(e.g. `--provider local --model gemma-4-12b`)")
    return resolved


def _require_key(spec: ProviderSpec) -> str:
    """Read the provider's API key from the environment.

    Raises:
        RuntimeError: If unset. The message names the env var and the broker, and
            by construction contains no key material.
    """
    if not spec.api_key_env:
        return ""
    key = os.environ.get(spec.api_key_env, "").strip()
    if not key:
        raise RuntimeError(f"{spec.api_key_env} is not set; {KEY_HELP}")
    return key


def _call_ollama(spec: ProviderSpec, prompt: str, model: str | None,
                 timeout: int) -> CriticReply:
    """One local Ollama generate call.

    ``temperature: 0`` because this is a grader, not a writer: two runs of the
    same pass over the same pack should differ because the MODELS differ, not
    because sampling did. Independence in the panel comes from using different
    weights, and sampling noise only muddies the agreement signal.
    """
    url = base_url(spec).rstrip("/") + "/api/generate"
    payload: dict = {
        "model": _resolve_model(spec, model),
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    if spec.json_mode:
        payload["format"] = "json"
    data = _post_json(url, payload, timeout)
    text = data.get("response")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"{spec.name} returned an empty response")
    # Ollama echoes the resolved model (including the tag it actually loaded).
    observed = data.get("model")
    return CriticReply(text=text,
                       model=str(observed) if observed else None,
                       provider=spec.name)


def _call_openai(spec: ProviderSpec, prompt: str, model: str | None,
                 timeout: int) -> CriticReply:
    """One OpenAI-compatible chat-completions call (llama-server and friends)."""
    root = base_url(spec)
    if not root:
        raise RuntimeError(
            f"provider {spec.name!r} has no base URL; set {spec.base_url_env}")
    key = _require_key(spec)
    url = root.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": _resolve_model(spec, model),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0,
    }
    if spec.json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _post_json(url, payload, timeout, headers=headers)
    choices = data.get("choices")
    text = ""
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = str(message.get("content") or "")
    if not text.strip():
        raise RuntimeError(f"{spec.name} returned an empty completion")
    # The response's own `model` field — what actually served the request, which
    # can differ from what was asked for when a gateway routes or aliases.
    observed = data.get("model")
    return CriticReply(text=text,
                       model=str(observed) if observed else None,
                       provider=spec.name)


def _opencode_model_ref(model: str) -> str:
    """Turn a ``--model`` value into opencode's ``provider/model`` reference.

    A bare id (``deepseek-v4-flash-free``) is namespaced to opencode's own free
    tier; anything already containing ``/`` (``opencode-go/deepseek-v4-flash``)
    is passed through untouched. This keeps the common case short AND keeps the
    panel label readable — ``opencode/deepseek-v4-flash-free`` rather than the
    doubled ``opencode/opencode/deepseek-v4-flash-free`` that a verbatim
    pass-through would produce.
    """
    return model if "/" in model else f"{OPENCODE_DEFAULT_NAMESPACE}/{model}"


def run_opencode(prompt: str, model: str | None, timeout: int) -> CriticReply:
    """One `opencode run` subprocess.

    A module-level function rather than an inline ``subprocess.run`` because the
    suites must be able to patch it: an unpatched test would spawn a real CLI,
    hit a real endpoint, and take ~10s per batch. Same reasoning that keeps
    :func:`factcheck_pack.run_claude` separately patchable.

    Two details are load-bearing and were found the hard way:

    * **stdin must be closed.** ``opencode run`` waits on stdin when it is a pipe
      or a tty and simply hangs — the first attempt at this returned 0 bytes
      after a 120s timeout. ``DEVNULL`` is the fix, not a tidiness choice.
    * **``model`` is reported as ``None``.** opencode's JSON event stream carries
      ``sessionID``/``messageID``/tokens/cost and no model field at all. Its
      SQLite store does record a ``modelID``, but that is opencode echoing the
      string we passed in ``-m``, laundered through a database — not the provider
      attesting to what served the request. Recording it as observed would be
      exactly the self-attestation the module docstring forbids, so the unknown
      is recorded as unknown.

    Raises:
        RuntimeError: Binary missing, non-zero exit, timeout, or an event stream
            with no text in it. Message is redacted and truncated.
    """
    binary = shutil.which("opencode")
    if not binary:
        raise RuntimeError("`opencode` CLI not on PATH")
    resolved = _opencode_model_ref(_resolve_model(get_spec("opencode"), model))
    # --pure drops external plugins; --dir anchors agent discovery at the repo so
    # the run does not depend on the caller's working directory.
    argv = [binary, "run", "--pure", "--format", "json",
            "--dir", str(REPO_ROOT), "-m", resolved]
    if (REPO_ROOT / ".opencode" / "agent" / f"{OPENCODE_AGENT}.md").is_file():
        argv += ["--agent", OPENCODE_AGENT]
    argv.append(prompt)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL,
                              check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"opencode ({resolved}) timed out after {timeout}s") from None
    except OSError as e:
        raise RuntimeError(f"could not run opencode: {_redact(str(e))}") from None
    if proc.returncode != 0:
        detail = _redact((proc.stderr or proc.stdout or "").strip())[:300]
        raise RuntimeError(
            f"opencode ({resolved}) exited {proc.returncode}: {detail}")
    # Newline-delimited events; the reply is the concatenation of every `text`
    # part. Unparseable lines are skipped rather than fatal — a future opencode
    # version adding a log line to stdout should not fail an otherwise good pass.
    chunks: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    text = "".join(chunks)
    if not text.strip():
        raise RuntimeError(f"opencode ({resolved}) returned no text output")
    return CriticReply(text=text, model=None, provider="opencode")


def run(provider: str, prompt: str, model: str | None, timeout: int) -> CriticReply:
    """Send ``prompt`` to ``provider`` and return its reply.

    ``kind="claude-cli"`` is intentionally NOT handled here. That call lives in
    :func:`factcheck_pack.run_claude` and is dispatched by
    :func:`factcheck_pack.run_critic`, for two reasons: keeping it there avoids a
    circular import (this module must not import the critic), and the existing
    suites patch ``factcheck_pack.run_claude`` directly — routing the Claude path
    through here would silently disarm those patches and let tests make real
    billed calls.

    Raises:
        ValueError: Unknown provider name.
        RuntimeError: Any call failure, already redacted.
    """
    spec = get_spec(provider)
    if spec.kind == "ollama":
        return _call_ollama(spec, prompt, model, timeout)
    if spec.kind == "openai":
        return _call_openai(spec, prompt, model, timeout)
    if spec.kind == "opencode-cli":
        return run_opencode(prompt, model, timeout)
    if spec.kind == "claude-cli":
        raise RuntimeError(
            "the claude provider is dispatched by factcheck_pack.run_critic, "
            "not critic_providers.run")
    raise RuntimeError(f"provider {spec.name!r} has unsupported kind {spec.kind!r}")


def preflight(provider: str, model: str | None = None,
              timeout: int = 5) -> str | None:
    """Return why ``provider`` cannot run right now, or ``None`` if it can.

    Called BEFORE a long batch loop so a missing key or a stopped Ollama server
    fails in a second with an actionable sentence, instead of after N batches of
    identical errors. Never raises for an unusable provider — that is the answer,
    not an exception — but an unknown NAME still raises, because that is a
    caller bug rather than an environment state.
    """
    spec = get_spec(provider)

    if spec.kind == "claude-cli":
        if not shutil.which("claude"):
            return "`claude` CLI not on PATH; cannot run the Layer-C critic"
        return None

    if spec.kind == "opencode-cli":
        if not shutil.which("opencode"):
            return "`opencode` CLI not on PATH; cannot run the Layer-C critic"
        return None

    if spec.api_key_env and not os.environ.get(spec.api_key_env, "").strip():
        return f"{spec.api_key_env} is not set; {KEY_HELP}"

    root = base_url(spec)
    if not root:
        return f"provider {spec.name!r} has no base URL; set {spec.base_url_env}"

    if spec.kind == "ollama":
        try:
            tags = _get_json(root.rstrip("/") + "/api/tags", timeout)
        except RuntimeError as e:
            return (f"ollama server not reachable at {_safe_url(root)} ({e}); "
                    "start it with `ollama serve`")
        installed = [m.get("name") for m in tags.get("models", [])
                     if isinstance(m, dict) and m.get("name")]
        if not installed:
            return (f"ollama at {_safe_url(root)} has no models pulled; "
                    "e.g. `ollama pull qwen3:8b`")
        wanted = (model or spec.default_model or "").strip()
        if wanted and wanted not in installed:
            # Tolerate the bare-name form: `qwen3` should match `qwen3:8b`.
            if not any(name.split(":", 1)[0] == wanted for name in installed):
                return (f"ollama model {wanted!r} is not pulled; have: "
                        f"{', '.join(sorted(installed))}")
        return None

    if spec.name == "local":
        # A local server is either up or it is not, and finding out costs one
        # request. Same rationale as the Ollama probe: N identical connection
        # errors spread across a long batch loop is the worst way to learn that
        # llama-server was never started.
        try:
            _get_json(root.rstrip("/") + "/models", timeout)
        except RuntimeError as e:
            return (f"llama-server not reachable at {_safe_url(root)} ({e}); "
                    "start it with `llama-server -m <model.gguf> --port 8080`")
        return None

    # kind == "openai": a key and a base URL are all we can verify without
    # spending a token. A wrong model id surfaces as a 404 on the first batch.
    return None
