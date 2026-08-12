# Layer-C critic providers and the review panel

## The problem this fixes

Layer C was one call to one model. A single pass cannot distinguish **"reviewed
carefully, found nothing"** from **"did not really look"** — both produce an empty
findings list, and the certification records the same thing either way.

That is not hypothetical. `sy0-701-final-review.json` passed a single Layer-C
pass, minted a certification, and installed. It contained 115 critical
violations. The gate reported clean because one model, on one pass, said so.

Running the same model again does not help: correlated failure modes miss the
same questions twice. Running **different** models does. Cheap providers are what
make that affordable — an opencode free-tier pass costs **nothing at all**, so
"review it several independent times" stops being a budget decision.

## Discovery rule: union, never majority

A 2-of-3 vote is the obvious design and it is **wrong for a gate**.

The defect class being fixed is a *false negative*. A majority threshold
suppresses precisely the finding that only one model was sharp enough to catch,
which makes false negatives more likely, not less. So:

- every finding from every pass survives the merge;
- `agreement` (how many passes flagged the same `qid` + `severity`) is an
  **annotation** — it ranks attention and routes escalation;
- `agreement` never removes anything.

In a frozen certification campaign, one discovery finding is retained in the
ledger rather than discarded by a vote. Corroborated blockers are remediated as
a batch before exact changed-ID rechecks. The deterministic campaign stamp
consumes that evidence; the cheap reviewer never writes a stamp. The
regression test that locks the merge behavior in is
`tests/test_critic_providers.py::MergeFindingsTests::test_a_finding_from_one_pass_alone_survives_the_merge`.

## Providers

| name | transport | key | default model |
|---|---|---|---|
| `claude` | `claude` CLI subprocess | handled by the CLI | `claude-sonnet-5` |
| `codex` | `codex exec` read-only ephemeral subprocess | handled by the CLI | `gpt-5.6-terra` |
| `opencode` | `opencode` CLI subprocess | held by opencode itself | `deepseek-v4-flash-free` |
| `openai-compatible` | `QUIZZLER_OPENAI_BASE_URL` | `QUIZZLER_OPENAI_API_KEY` | none — pass `--model` |

Model ids are config, not constants: a vendor rename is a `--model` flag or a
one-line `ProviderSpec` edit.

**No provider here requires Quizzler to handle a secret unless you add one.**
`claude`, `codex`, and `opencode` each authenticate from their own credential stores,
which this repo never reads. `openai-compatible` exists for the day some
gateway does need a key — see [Secrets](#secrets).

Adding a provider that speaks the OpenAI chat-completions shape needs no new
code — append a `ProviderSpec` to `PROVIDERS` in `scripts/critic_providers.py`.

A local, no-key backend (Ollama, then `llama-server`) previously lived here.
Both were removed 2026-08-10: neither had a recorded authorization, and running
a local model server is a real architectural decision this repo does not make
unilaterally. If one is wanted again, it needs an explicit decision first, not
an agent re-adding a `ProviderSpec`.

## Who may certify

`scripts/certification_campaign.py` owns the frozen snapshot, evidence ledger,
and one remediation transition; it never certifies. The campaign's one full
discovery run is a census by the configured high-capability verifier over the
frozen snapshot. DeepSeek Flash Go (`opencode-go/deepseek-v4-flash`, variant
`max`) is advisory evidence: retain its findings and operational status, but do
not make it a stamp gate. The current verifier default is Codex GPT-5.6 Terra
at `high`; Claude remains an explicit alternative when available. Operational
errors may be retried only against the unchanged snapshot. If the pack itself
cannot be loaded, no critic is invoked.

`scripts/verify_pack.py` remains an internal in-process gate primitive used by
the hybrid orchestrator. Running it as a shell command, including `--panel`,
is retired and fails fast with guidance to `hybrid_verify.py`. The standalone
`factcheck_pack.py` and `critic_panel.py` tools remain non-certifying review
tools for authoring and diagnosis.

## Running it

The campaign starts with one full non-certifying hybrid discovery invocation,
which records DeepSeek advisory evidence and the complete high-verifier census.
Resolve its blocking findings in one remediation batch, then run exact
changed-ID targeted rechecks and ingest their evidence. The final command is a
deterministic stamp from the completed ledger; it does not invoke either
reviewer:

```bash
python3 scripts/certification_campaign.py init question-packs/<course>/<pack>.json --ledger /tmp/<pack>.campaign.json
python3 scripts/hybrid_verify.py question-packs/<course>/<pack>.json --no-certify --json --campaign-snapshot sha256:<frozen-snapshot>
python3 scripts/certification_campaign.py begin-remediation --ledger /tmp/<pack>.campaign.json --pack question-packs/<course>/<pack>.json --changed-ids qid1,qid2
python3 scripts/hybrid_verify.py question-packs/<course>/<pack>.json --no-certify --json --only qid1,qid2 --campaign-snapshot sha256:<remediation-snapshot>
python3 scripts/hybrid_verify.py question-packs/<course>/<pack>.json --certify-campaign /tmp/<pack>.campaign.json
```

`--certify-campaign` is restricted to a completed, snapshot-bound ledger. It
runs deterministic checks and makes no fresh LLM call. A new concern starts a
new campaign rather than changing the completed evidence set.

The sweep always uses the same hybrid pipeline. Its retired `--panel` option
fails fast with guidance to the canonical route.

Non-certifying review — fast, cheap, use it while editing:

```bash
python3 scripts/factcheck_pack.py <pack> --provider opencode
python3 scripts/critic_panel.py <pack> --panel opencode=deepseek-v4-flash-free,opencode=mimo-v2.5-free
```

`--panel` syntax is `provider[=model]`, comma-separated, **two or more entries**.
The separator is `=` rather than `:` because some gateway model ids contain
colons. Duplicate passes are rejected: repeating one model is correlated
repetition that would inflate `agreement` into fake consensus.

`--strict` is an optional diagnostic mode, not the final campaign gate. The high
verifier is selected through registered profiles; no particular vendor/model is
required by this workflow.

## Coverage across passes

A pass that dies does not un-review a pack that another pass covered in full. So:

- `questions_unchecked` is the **minimum** across passes, not the sum;
- the gate blocks on coverage only when **no** pass individually completed;
- the retired panel route is rejected; only the selected hybrid verifier profile
  can certify.

If *every* pass fails, that is an operational error (exit 1), not a verdict.

The bar is zero Layer-A live findings, zero blocking Layer-C findings, and full
coverage. These standalone provider passes are review tools only.

## What the certification records

The old panel route is retired and cannot write a certification. Hybrid accepts
only registered high-capability verifier profiles (currently
`codex-terra-high` by default, with `claude-opus-high` explicitly selectable).
Certification provenance records the selected provider/model/effort:

```json
"critic_provider": "codex",
"critic_model": "unknown",
"critic_model_requested": "gpt-5.6-terra",
"critic_reasoning_effort": "high"
```

This is a report-provenance shape from this repo. Stored `critic_model` is the
model observed in the provider's **own response**, never back-filled from the
request:

- `claude` reports the model id its own CLI envelope names.
- `opencode` reports `null`. Its JSON event stream carries no model field at
  all. Its SQLite store does hold a `modelID`, but that is the string passed in
  `-m` echoed back through a database, not the provider attesting to anything —
  recording it as observed would be self-attestation by a longer route.

A cert that records the requested model proves nothing about what graded the
questions. Unknown is recorded as unknown.

These provenance fields are not part of `questions_hash` (which hashes question
content), so richer provenance can never invalidate an existing certification.

## Backends

### `opencode` (nothing to configure)

```bash
python3 scripts/factcheck_pack.py <pack> --provider opencode                     # default model
python3 scripts/factcheck_pack.py <pack> --provider opencode --model mimo-v2.5-free
```

`opencode` authenticates from its own store (`~/.local/share/opencode/auth.json`)
and its free tier carries several genuinely distinct models — DeepSeek Flash V4,
MiMo, Nemotron Ultra, Ling Flash — which is real panel diversity at zero cost,
though see the open gap noted above: `opencode` cannot prove which of them
actually answered a given pass.

A bare `--model` is namespaced to opencode's own provider
(`deepseek-v4-flash-free` → `opencode/deepseek-v4-flash-free`); pass a value
containing `/` to reach another opencode provider (`opencode-go/deepseek-v4-flash`).

`--variant` selects opencode's reasoning effort (`low`/`high`/`max`) for models
that support one — `deepseek-v4-flash-free` does, at every effort level, for
$0. It applies only to opencode: pairing it with `--provider claude` (or any
non-opencode single provider) is rejected up front; in a `--panel` run it
applies to every opencode pass and is silently ignored for the others.

```bash
python3 scripts/factcheck_pack.py <pack> --provider opencode --variant max
```

Two implementation details, both found the hard way and both locked by tests:
**stdin must be closed** or `opencode run` hangs indefinitely rather than
failing, and the run uses the repo-local `.opencode/agent/pack-critic.md` agent,
which drops the stock `build` agent's tools and cuts ~16k tokens of system
prompt per batch to ~10k.

## Secrets

There is **no secret to handle** for `claude`, `codex`, or `opencode`. The section below
governs `openai-compatible`, kept for the day a gateway does need a key.

**Every API key comes from `bws-secret-exec`.** Never `bws-run`, `bws-get`, or
`bws secret get` — those print values to stdout, which puts them in scrollback,
logs, and agent transcripts.

The code holds up its end: keys are read from the environment only, never logged,
never in an exception message, never in a request body (header only), and
`_redact` scrubs key material out of any upstream error text a gateway might echo
back. Tests in `tests/test_critic_providers.py::SecretHygieneTests` enforce each
of those.

```bash
export QUIZZLER_OPENAI_BASE_URL=https://<gateway>/v1   # key via the broker, never inline
python3 scripts/factcheck_pack.py <pack> --provider openai-compatible --model <id>
```
