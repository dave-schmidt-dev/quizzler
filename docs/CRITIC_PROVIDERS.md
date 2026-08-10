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
| `openai-compatible` | `QUIZZLER_OPENAI_BASE_URL` | `QUIZZLER_OPENAI_API_KEY` | none — pass `--model` |

Model ids are config, not constants: a vendor rename is a `--model` flag or a
one-line `ProviderSpec` edit.

**No provider here requires Quizzler to handle a secret unless you add one.**
`claude` and `opencode` each authenticate from their own credential stores,
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

Cheap providers make review affordable. They do **not** make certification
cheaper to mint — those are different questions, and conflating them would have
weakened the gate this feature exists to strengthen.

| Command | Runs the gate | Writes a certification |
|---|---|---|
| `verify_pack.py <pack>` (default `claude`) | yes | yes — `external-layer-c-strict` |
| `verify_pack.py <pack> --panel a,b[,c]` | yes | yes — `external-layer-c-panel` |
| `verify_pack.py <pack> --provider opencode` | yes | **no** — exit 3, `REVIEW PASSED` |
| `verify_pack.py <pack> --panel opencode` | — | **no** — exit 1, refused |

A single non-default provider reviews and reports; it leaves the pack unchanged.
Otherwise a single cheap model — or an HTTP stub that answers `{"findings": []}`
to every batch — could stamp the same certification the install gate trusts,
which is the self-attestation INV-7 exists to refuse.

A one-entry `--panel` is refused for the matching reason: it would mint
`external-layer-c-panel`, a name the gate reads as "several independent models
looked", from the single pass whose false negative started all of this.

Two cheap passes certify. `--panel opencode=deepseek-v4-flash-free,opencode=mimo-v2.5-free`
is a complete, Claude-free, **credential-free** certifying run — two distinct
free-tier models, both reached through the one `opencode` CLI.

`scripts/hybrid_verify.py <pack>` automates the single-critic row above into a
cheap-then-certify sequence instead: an `opencode` review pass first (default
model `opencode-go/deepseek-v4-flash`, opencode's paid "go" tier, not the free
tier), then a `claude` certifying pass — but ONLY if that first pass is clean,
so Claude quota is spent only on packs that already look ready. It calls
`verify_pack.py` for both passes and changes nothing about which command
certifies; see the script's own module docstring for its exit-code contract.

### A roster is not independence

Distinct `--panel` entries prove nothing about distinct *weights*. Two
`openai-compatible` entries pointed at the same gateway, for example, could both
be routed to the same underlying model by that gateway — two labels, one model,
`external-layer-c-panel` minted from correlated repetition.

So the gate compares `model_observed` across completed passes and refuses to
certify when one model served more than one pass. Providers that report **no**
model (`opencode`) are not counted as duplicates: two unknowns are not evidence
of sameness, and for those the distinct-request rule in `parse_panel` is the
guarantee available.

**This is a real, open gap, not a solved problem.** opencode never reports which
model actually served a request, so `--panel opencode=deepseek-v4-flash-free,opencode=mimo-v2.5-free`
looks independent by label and cannot be proven either way by `model_observed` —
the gate trusts that opencode routed each `-m` argument to the model it named.
There is no cheap way to close this today; it is a known limitation of the
`opencode` transport, not something the dedup check missed.

## Running it

Certifying runs:

```bash
python3 scripts/verify_pack.py question-packs/<course>/<pack>.json
python3 scripts/verify_pack.py <pack> --panel opencode,claude
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
python3 scripts/verify_pack.py <pack> --provider opencode
python3 scripts/critic_panel.py <pack> --panel opencode=deepseek-v4-flash-free,opencode=mimo-v2.5-free
```

`--panel` syntax is `provider[=model]`, comma-separated, **two or more entries**.
The separator is `=` rather than `:` because some gateway model ids contain
colons. Duplicate passes are rejected: repeating one model is correlated
repetition that would inflate `agreement` into fake consensus.

### Suggested shapes

- **Drafting loop** — `--provider opencode --variant max`. Free at every
  reasoning effort, fast enough to run every edit, and catches the obvious
  defects while you are still editing. Does not certify, which is correct: you
  are still editing. Run it as many times as you want; nothing here spends a
  Claude call.
- **Pre-certification** — `--panel opencode=deepseek-v4-flash-free,opencode=mimo-v2.5-free,claude`.
  Three independent opinions; the free passes do the volume, Claude anchors it.
  Dropping to `--panel opencode,claude` (two passes instead of three) is
  cheaper in wall-clock but strictly LESS assurance, not "more legitimate" —
  the merge is union-never-majority, so every pass you remove is a chance to
  catch a unique finding you gave up. Decide that trade-off deliberately, don't
  default into it because free passes feel free to skip.
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
    {"label": "opencode/deepseek-v4-flash-free", "provider": "opencode",
     "model_requested": "deepseek-v4-flash-free",
     "model_observed": null, "coverage_ok": true},
    {"label": "claude/claude-sonnet-5", "provider": "claude",
     "model_requested": "claude-sonnet-5",
     "model_observed": "claude-sonnet-5", "coverage_ok": true}
  ],
  "passes_completed": 2,
  "passes_attempted": 2,
  "solo_qids": ["q17"]
}
```

That is a real certification shape from this repo. `model_observed` is read out
of the provider's **own response**, never back-filled from the request:

- `claude` reports the model id its own CLI envelope names.
- `opencode` reports `null`. Its JSON event stream carries no model field at
  all. Its SQLite store does hold a `modelID`, but that is the string passed in
  `-m` echoed back through a database, not the provider attesting to anything —
  recording it as observed would be self-attestation by a longer route.

A cert that records the requested model proves nothing about what graded the
questions. Unknown is recorded as unknown.

`critic_panel` is not part of `questions_hash` (which hashes question content),
so richer provenance can never invalidate an existing certification.

## Backends

### `opencode` (nothing to configure)

```bash
python3 scripts/verify_pack.py <pack> --provider opencode                     # default model
python3 scripts/verify_pack.py <pack> --provider opencode --model mimo-v2.5-free
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
python3 scripts/verify_pack.py <pack> --provider opencode --variant max
python3 scripts/verify_pack.py <pack> --panel opencode,claude --variant max
```

Two implementation details, both found the hard way and both locked by tests:
**stdin must be closed** or `opencode run` hangs indefinitely rather than
failing, and the run uses the repo-local `.opencode/agent/pack-critic.md` agent,
which drops the stock `build` agent's tools and cuts ~16k tokens of system
prompt per batch to ~10k.

## Secrets

There is **no secret to handle** for `claude` or `opencode`. The section below
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
python3 scripts/verify_pack.py <pack> --provider openai-compatible --model <id>
```
