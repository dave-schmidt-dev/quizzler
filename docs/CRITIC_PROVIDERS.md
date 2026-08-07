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
make that affordable — several DeepSeek or local passes cost a fraction of one
frontier pass, so "review it several independent times" stops being a budget
decision.

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
| `deepseek` | `https://api.deepseek.com/chat/completions` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `ollama` | local `http://127.0.0.1:11434` | none | none — pass `--model` |
| `openai-compatible` | `QUIZZLER_OPENAI_BASE_URL` | `QUIZZLER_OPENAI_API_KEY` | none — pass `--model` |

Base URLs are overridable per provider (`QUIZZLER_DEEPSEEK_URL`,
`QUIZZLER_OLLAMA_URL`). Model ids are config, not constants: a vendor rename is a
`--model` flag or a one-line `ProviderSpec` edit.

DeepSeek model ids (`deepseek-v4-flash`, `deepseek-v4-pro`) and base URL verified
2026-08-07 against <https://api-docs.deepseek.com/quick_start/pricing>.

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
| `verify_pack.py <pack> --provider ollama --model …` | yes | **no** — exit 3, `REVIEW PASSED` |
| `verify_pack.py <pack> --panel deepseek` | — | **no** — exit 1, refused |

A single non-default provider reviews and reports; it leaves the pack unchanged.
Otherwise a 1B local model — or an HTTP stub that answers `{"findings": []}` to
every batch — could stamp the same certification the install gate trusts, which
is the self-attestation INV-7 exists to refuse.

A one-entry `--panel` is refused for the matching reason: it would mint
`external-layer-c-panel`, a name the gate reads as "several independent models
looked", from the single pass whose false negative started all of this.

Two cheap passes certify. `--panel deepseek,ollama=qwen3:8b` is a complete,
Claude-free certifying run.

## Running it

Certifying runs:

```bash
python3 scripts/verify_pack.py question-packs/<course>/<pack>.json
python3 scripts/verify_pack.py <pack> --panel deepseek,ollama=qwen3:8b,claude
python3 scripts/recert_sweep.py question-packs/<course>/ --panel deepseek,claude
```

Non-certifying review — fast, cheap, use it while editing:

```bash
python3 scripts/verify_pack.py <pack> --provider ollama --model qwen3:8b
python3 scripts/critic_panel.py <pack> --panel deepseek,ollama=qwen3:8b
```

`--panel` syntax is `provider[=model]`, comma-separated, **two or more entries**.
The separator is `=` rather than `:` because Ollama model ids contain colons
(`qwen3:8b`). Duplicate passes are rejected: repeating one model is correlated
repetition that would inflate `agreement` into fake consensus.

### Suggested shapes

- **Drafting loop** — `--provider ollama --model qwen3:8b`. Free, local, fast,
  and catches the obvious defects while you are still editing. Does not certify,
  which is correct: you are still editing.
- **Pre-certification** — `--panel deepseek,ollama=qwen3:8b,claude`. Three
  independent opinions; cheap passes do the volume, Claude anchors it.
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
    {"label": "deepseek/deepseek-v4-flash", "provider": "deepseek",
     "model_requested": "deepseek-v4-flash",
     "model_observed": "deepseek-v4-flash", "coverage_ok": true}
  ],
  "passes_completed": 1,
  "passes_attempted": 2,
  "solo_qids": ["q17"]
}
```

`model_observed` is read out of the provider's **own response**, never
back-filled from the request. A cert that records the requested model proves
nothing about what actually graded the questions — that is self-attestation, the
thing INV-7 exists to stop. When a provider does not report its model, the field
is `null`; unknown is recorded as unknown.

`critic_panel` is not part of `questions_hash` (which hashes question content),
so richer provenance can never invalidate an existing certification.

## Secrets

**Every API key comes from `bws-secret-exec`.** Never `bws-run`, `bws-get`, or
`bws secret get` — those print values to stdout, which puts them in scrollback,
logs, and agent transcripts.

The code holds up its end: keys are read from the environment only, never logged,
never in an exception message, never in a request body (header only), and
`_redact` scrubs key material out of any upstream error text a gateway might echo
back. Tests in `tests/test_critic_providers.py::SecretHygieneTests` enforce each
of those.

### One-time setup (owner action — an agent cannot do this)

1. **Store the key in BWS** under the secret name `DEEPSEEK_API_KEY`.

2. **Register the pinned consumer** in `~/Documents/Projects/bws/bws-secret-exec.py`,
   in the `CONSUMERS` dict:

   ```python
   "quizzler-critic": Consumer(
       executable=Path("/Users/dave/Documents/Projects/quizzler/scripts/critic-with-key.sh"),
       executable_sha256="42315595c286c156635fe9473e7829a40d88d254bec50e0664f0502fbff2bf83",
       mode="single",
       secret_name="DEEPSEEK_API_KEY",
       environment_name="DEEPSEEK_API_KEY",
       preserve_home=True,   # only needed if `claude` is one of the panel passes
   ),
   ```

   The hash pins `scripts/critic-with-key.sh` as of this commit. **Re-run
   `shasum -a 256 scripts/critic-with-key.sh` and update the pin whenever that
   file changes** — the broker refuses to run on a mismatch, which is the point.
   The file must stay mode `0755`.

3. **Run it:**

   ```bash
   bws-secret-exec quizzler-critic -- scripts/critic-with-key.sh \
       question-packs/<course>/<pack>.json --panel deepseek,claude
   ```

The wrapper is deliberately not a general command runner: it can only `exec`
`scripts/verify_pack.py`, resolved relative to itself. Note the trust boundary —
the pin covers the wrapper, and the repo covers `verify_pack.py`. Anyone who can
edit the repo can change what runs with the key in its environment.

### Ollama (no registration needed)

```bash
ollama serve &          # the provider preflight tells you if it is not running
ollama pull qwen3:8b    # nothing is pulled by default
python3 scripts/verify_pack.py <pack> --provider ollama --model qwen3:8b
```

Preflight checks reachability and that the model is actually pulled, so a
misconfigured local provider costs one second and one actionable sentence rather
than N identical batch errors.
