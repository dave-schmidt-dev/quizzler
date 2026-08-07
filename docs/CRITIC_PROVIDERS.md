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
make that affordable — an opencode free-tier pass plus a local `llama-server`
pass cost **nothing at all**, so "review it several independent times" stops
being a budget decision.

## The one rule: union, never majority

A 2-of-3 vote is the obvious design and it is **wrong for a gate**.

The defect class being fixed is a *false negative*. A majority threshold
suppresses precisely the finding that only one model was sharp enough to catch,
which makes false negatives more likely, not less. So:

- every finding from every pass survives the merge;
- `agreement` (how many passes flagged the same `qid` + `severity`) is an
  **annotation** — it ranks attention and routes escalation;
- `agreement` never removes anything.

One cheap model finding a wrong answer is enough to refuse certification. The
regression test that locks this in is
`tests/test_critic_providers.py::MergeFindingsTests::test_a_finding_from_one_pass_alone_survives_the_merge`.

## Providers

| name | transport | key | default model |
|---|---|---|---|
| `claude` | `claude` CLI subprocess | handled by the CLI | `claude-sonnet-5` |
| `opencode` | `opencode` CLI subprocess | held by opencode itself | `deepseek-v4-flash-free` |
| `local` | `llama-server` at `http://127.0.0.1:8080/v1` | **none** | none — pass `--model` |
| `ollama` | local `http://127.0.0.1:11434` | none | none — pass `--model` |
| `openai-compatible` | `QUIZZLER_OPENAI_BASE_URL` | `QUIZZLER_OPENAI_API_KEY` | none — pass `--model` |

Base URLs are overridable per provider (`QUIZZLER_LOCAL_URL`,
`QUIZZLER_OLLAMA_URL`). Model ids are config, not constants: a vendor rename is a
`--model` flag or a one-line `ProviderSpec` edit.

**No provider here requires Quizzler to handle a secret.** `local` and `ollama`
are keyless loopback servers; `claude` and `opencode` each authenticate from
their own credential stores, which this repo never reads. `openai-compatible`
exists for the day some gateway does need a key — see [Secrets](#secrets).

Adding a provider that speaks the OpenAI chat-completions shape needs no new
code — append a `ProviderSpec` to `PROVIDERS` in `scripts/critic_providers.py`.

## Who may certify

Cheap providers make review affordable. They do **not** make certification
cheaper to mint — those are different questions, and conflating them would have
weakened the gate this feature exists to strengthen.

| Command | Runs the gate | Writes a certification |
|---|---|---|
| `verify_pack.py <pack>` (default `claude`) | yes | yes — `external-layer-c-strict` |
| `verify_pack.py <pack> --panel a,b[,c]` | yes | yes — `external-layer-c-panel` |
| `verify_pack.py <pack> --provider local --model …` | yes | **no** — exit 3, `REVIEW PASSED` |
| `verify_pack.py <pack> --panel opencode` | — | **no** — exit 1, refused |
| `--panel local=a,local=b` (one server) | yes | **no** — exit 3, not independent |

A single non-default provider reviews and reports; it leaves the pack unchanged.
Otherwise a 1B local model — or an HTTP stub that answers `{"findings": []}` to
every batch — could stamp the same certification the install gate trusts, which
is the self-attestation INV-7 exists to refuse.

A one-entry `--panel` is refused for the matching reason: it would mint
`external-layer-c-panel`, a name the gate reads as "several independent models
looked", from the single pass whose false negative started all of this.

Two cheap passes certify. `--panel opencode,local=gemma-4-12b` is a complete,
Claude-free, **credential-free** certifying run.

### A roster is not independence

Distinct `--panel` entries prove nothing about distinct *weights*. Point
`--panel local=gemma-4-12b,local=nemotron-nano` at one `llama-server` and both
passes are graded by whichever single GGUF that server has loaded — two labels,
one model, `external-layer-c-panel` minted from correlated repetition. Only the
models' own reported ids can settle it, and those are known only after the run.

So the gate compares `model_observed` across completed passes and refuses to
certify when one model served more than one pass. Providers that report **no**
model (`opencode`) are not counted as duplicates: two unknowns are not evidence
of sameness, and for those the distinct-request rule in `parse_panel` is the
guarantee available. To run two different local models, give each its own
`llama-server` port and point one pass at it with `QUIZZLER_LOCAL_URL`.

## Running it

Certifying runs:

```bash
python3 scripts/verify_pack.py question-packs/<course>/<pack>.json
python3 scripts/verify_pack.py <pack> --panel opencode,local=gemma-4-12b,claude
python3 scripts/recert_sweep.py question-packs/<course>/ --panel opencode,claude
```

Upgrading an **existing** course to panel certification needs `--force`:

```bash
python3 scripts/recert_sweep.py question-packs/<course>/ --panel opencode,claude --force
```

Certification freshness is a **content** check, not a method check, so every
already-certified pack is "fresh" against a panel run too. Without `--force` the
sweep would print `SKIP` for the whole course, grade nothing, exit 0, and read as
if the course were already panel-certified. It names the method it found on each
skip and prints a mismatch note at the end, so the no-op is visible rather than
inferred.

Non-certifying review — fast, cheap, use it while editing:

```bash
python3 scripts/verify_pack.py <pack> --provider local --model gemma-4-12b
python3 scripts/critic_panel.py <pack> --panel opencode,local=gemma-4-12b
```

`--panel` syntax is `provider[=model]`, comma-separated, **two or more entries**.
The separator is `=` rather than `:` because Ollama model ids contain colons
(`qwen3:8b`). Duplicate passes are rejected: repeating one model is correlated
repetition that would inflate `agreement` into fake consensus.

### Suggested shapes

- **Drafting loop** — `--provider local --model gemma-4-12b`. Free, local, fast,
  and catches the obvious defects while you are still editing. Does not certify,
  which is correct: you are still editing.
- **Pre-certification** — `--panel opencode,local=gemma-4-12b,claude`. Three
  independent opinions; the free passes do the volume, Claude anchors it.
- **Final belt-and-suspenders** — add `--strict`, which drops the pack's
  `source_directive` so no pass can be talked out of a finding by author-written
  text, and treats every live finding as blocking.

After a panel run, look at the **uncorroborated qids** line. Those are the
questions only one critic flagged: either a weak model's false positive or a
defect nobody else caught. Re-grade just those with a stronger provider:

```bash
python3 scripts/verify_pack.py <pack> --only q17,q42 --provider claude --model opus
```

## Coverage across passes

A pass that dies does not un-review a pack that another pass covered in full. So:

- `questions_unchecked` is the **minimum** across passes, not the sum;
- the gate blocks on coverage only when **no** pass individually completed;
- a degraded panel still certifies, and the dead pass is reported under
  `Layer C panel notes` — surfaced, never silently swallowed.

If *every* pass fails, that is an operational error (exit 1), not a verdict.

The bar itself is unchanged: zero Layer-A live findings, zero blocking Layer-C
findings, full coverage. A panel does not lower the bar; it produces better
evidence that the bar was actually cleared.

## What the certification records

A panel run certifies with `review_method: "external-layer-c-panel"` (an approved
method in `pack_cert.APPROVED_REVIEW_METHODS`) plus a `critic_panel` block:

```json
"critic_panel": {
  "passes": [
    {"label": "opencode", "provider": "opencode",
     "model_requested": null,
     "model_observed": null, "coverage_ok": true},
    {"label": "local/gemma-4-12b", "provider": "local",
     "model_requested": "gemma-4-12b",
     "model_observed": "/Users/dave/models/gemma-4-12b-it-qat-q4_0.gguf",
     "coverage_ok": true}
  ],
  "passes_completed": 2,
  "passes_attempted": 2,
  "solo_qids": ["q17"]
}
```

That is a real certification from this repo, and the two passes show both halves
of the rule. `model_observed` is read out of the provider's **own response**,
never back-filled from the request:

- `local` reports the **loaded GGUF path**, which is not the `gemma-4-12b` the
  request asked for. That is genuine provenance — the server naming the weights
  that actually answered.
- `opencode` reports `null`. Its JSON event stream carries no model field at
  all. Its SQLite store does hold a `modelID`, but that is the string passed in
  `-m` echoed back through a database, not the provider attesting to anything —
  recording it as observed would be self-attestation by a longer route.

A cert that records the requested model proves nothing about what graded the
questions. Unknown is recorded as unknown.

`critic_panel` is not part of `questions_hash` (which hashes question content),
so richer provenance can never invalidate an existing certification.

## Backends

Both certifying backends need **no credential from this repo and no setup
beyond starting a server**.

### `opencode` (nothing to configure)

```bash
python3 scripts/verify_pack.py <pack> --provider opencode                     # default model
python3 scripts/verify_pack.py <pack> --provider opencode --model mimo-v2.5-free
```

`opencode` authenticates from its own store (`~/.local/share/opencode/auth.json`)
and its free tier carries several genuinely distinct models — DeepSeek Flash V4,
MiMo, Nemotron Ultra, Ling Flash — which is real panel diversity at zero cost.
Quizzler shells out to the binary on `PATH`; no broker is involved because there
is no secret in the path to broker.

A bare `--model` is namespaced to opencode's own provider
(`deepseek-v4-flash-free` → `opencode/deepseek-v4-flash-free`); pass a value
containing `/` to reach another opencode provider (`opencode-go/deepseek-v4-flash`).

Two implementation details, both found the hard way and both locked by tests:
**stdin must be closed** or `opencode run` hangs indefinitely rather than
failing, and the run uses the repo-local `.opencode/agent/pack-critic.md` agent,
which drops the stock `build` agent's tools and cuts ~16k tokens of system
prompt per batch to ~10k.

### `local` — llama.cpp `llama-server` (no key)

```bash
llama-server -m ~/models/gemma-4-12b-it-qat-q4_0.gguf --port 8080 -c 8192 --jinja &
python3 scripts/verify_pack.py <pack> --provider local --model gemma-4-12b
```

Serves any GGUF over an OpenAI-compatible `/v1` endpoint with no auth. `--model`
is required and is your statement of *what you loaded*: llama-server ignores the
requested id and answers with the real GGUF path, so requested and observed both
land in the cert and a mismatch stays visible. Override the endpoint with
`QUIZZLER_LOCAL_URL` (this is also how you give a second local model its own
port for a genuinely independent two-model local panel).

Preflight probes `/v1/models`, so a server you forgot to start costs one second
and one actionable sentence rather than N identical batch errors.

### `ollama`

```bash
ollama serve & ollama pull <model>
python3 scripts/verify_pack.py <pack> --provider ollama --model <model>
```

Same keyless shape; use it if your models are already Ollama pulls rather than
loose GGUF files. Preflight also verifies the model is actually pulled.

## Secrets

There is **no secret to handle** for any provider above, which is the strongest
form this can take. The section below governs `openai-compatible`, kept for the
day a gateway does need a key.

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
python3 scripts/verify_pack.py <pack> --provider openai-compatible --model <id>
```
