# Validation Rules

## Purpose

Define the checks that a question pack must pass before it should be shown to a learner.

Validation must cover:

- schema validity
- answer integrity
- layout quality
- pedagogical quality
- repetition control

## Level 1: Schema Validation

Check:

- pack has required top-level fields
- each question has a unique `id`
- each question has a supported `type`
- required fields exist for that type
- answer structure matches the type
- true/false questions use a boolean `answer` and do not require an `options` array

Reject if:

- missing prompt
- missing explanation
- duplicate question IDs
- invalid answer index
- invalid matching pair references
- duplicate entries in `rightItems` (reuse indices in `correctPairs` instead)
- a true/false question is rendered or validated as if it were multiple choice with arbitrary options

## Level 2: Answer Integrity

Check:

- exactly one correct answer for single-answer multiple choice
- matching pairs are valid (each left item maps to exactly one right item; multiple left items may share a right item)
- `rightItems` contains no duplicate entries — shared answers reuse the same index in `correctPairs`
- true/false answers are boolean
- no ambiguous wording that makes two options equally correct
- question-specific rendering logic exists for each supported type

Reject if:

- two options can reasonably both be correct
- explanation contradicts the marked answer
- a supported type is added to the schema but not handled in the renderer or grader

## Level 3: Visual Validation

Run when a visual is present, and also check that visuals are not missing when required.

Check:

- diagram markup is syntactically valid enough to render
- text does not overflow obvious bounds
- the image is not overcrowded
- the image does not explicitly include the answer word unless the task is about reading that notation itself
- dependency direction is explicitly shown if direction matters

Reject if:

- answer leakage is embedded in the image
- the image relies on left/right placement alone to imply logic
- the image contains too much explanatory text
- a question about an inherently visual topic (charts, patterns, diagrams) has no diagram

## Level 4: Pedagogical Validation

Check:

- the chosen question type fits the concept
- the prompt is testing something real
- the explanation teaches the reason, not just the answer
- the distractors are plausible
- matching groups are internally coherent and not gameable by obvious elimination
- matching right-side descriptions all distinguish their terms along ONE consistent classification axis (e.g., all by channel, or all by mechanism — not a mix), and each captures the term's actual defining feature rather than a secondary attribute (semantic — Layer B/C critic, rule L11)
- for single-answer multiple choice, all options belong to ONE conceptual category/axis the stem's framing admits (e.g., all threat-actor types, all certificate coverage-scopes); an option drawn from a sibling taxonomy the stem excludes is a free elimination (semantic — Layer B/C critic, single-answer companion to L11)
- matching choices are not so similar that they create avoidable ambiguity unless the distinction itself is the learning objective
- matching choices are not left in the same obvious 1-2-3-4 order across packs unless the order is intentionally part of the concept

Reject if:

- a visual is used when a plain question would be clearer
- the question is trivial because of the phrasing
- the explanation is too weak to support correction
- a matching set contains obvious outliers that make the answer too easy
- a matching set mixes classification axes (e.g., three items described by channel and one by mechanism) or describes a term by a trait that is not its defining characteristic, so the set feels inconsistent even though each keyed pair is correct
- an MC/scenario option set mixes categories so one or more distractors self-eliminate on category grounds (e.g., a CIA-triad term among AAA-framework options; a validation-level certificate among coverage-scope certificates), or a NOT/EXCEPT item whose keyed answer is the only made-up / non-standard term so it is eliminable as the lone unfamiliar token
- a matching set uses near-duplicate choices that make the learner guess between wording variants rather than concepts
- a matching set repeatedly shows the right-side choices in the same unshuffled order
- the topic is inherently visual (charts, patterns, diagrams, topologies) but the diagram field is null
- abbreviations or acronyms appear in explanations without being spelled out on first use

## Level 5: Repetition Validation

Check:

- no duplicate prompt wording in the same pack
- no near-duplicate pattern overload relative to recent rounds
- no adjacent questions that test nearly the same thing in the same way unless deliberate contrast is intended
- no concept or answer-fact is re-tested across question types in the same course — a matching right-item (or its keyed pair) should not restate the keyed answer of a standalone MC/scenario item. L9 compares prompt tokens only, so concept-level reuse (e.g., a matching pair duplicating an MC answer, or two items both keying the same wildcard-certificate fact) is a semantic/manual or Layer-B/C corpus-pass check, not caught by Layer A

Reject or downgrade if:

- too many questions reuse the same relationship pattern
- too many visuals use the same layout
- the pack narrows too much into a single concept when learner performance is already high

## Level 6: Coverage Validation

Check:

- the pack reflects the intended topic mix
- under-covered topics are included when high-performance mode is active
- definition and distinction questions appear when needed
- the pack is not visually homogeneous
- recent audit findings about repetition and coverage are reflected in the pack

## Tier 7 — Cue / Leak Detection (Layer A Pack Linter)

Automated checks run at precommit, during `build_manifest.py`, and in the Playwright test suite (`tests/pack-quality.spec.js`). Invoke locally via:

```bash
python3 scripts/lint_packs.py --all
```

Exit codes: 0 (clean), 1 (critical failure), 2 (warnings only).

### L1 — Token Leak (Matching)

Reject if tokens from `leftItems[i]` appear in `rightItems[correctPairs[i]]`, or if `correctPairs` is identity-ordered `[0, 1, 2, …]` (risk of trivial pairing).

**Example fail:** Left item "DNS protocol" paired with right item "Domain Name System protocol" — the tokens `DNS` and `protocol` leak the answer.

### L2 — Stem Echo (Multiple Choice / Scenario)

Reject if a distinctive noun from the prompt appears only in the correct option.

**Exempt:** Vocabulary-pattern stems like "What does X stand for?" where the X itself is expected to appear only in the right answer.

**Example fail:** Prompt "Which is a lipid?" with options (A) "carbohydrate", (B) "protein", (C) "cholesterol" — word "lipid" is absent from all options, but "cholesterol" is the only one that *contains* a synonym of "lipid," making it guessable by echo.

### L3 — Length Tell (Multiple Choice / Scenario)

Reject if the correct option is conspicuously longer OR shorter than every distractor. Thresholds: 1.4× length ratio + 25-character absolute gap, both directions.

**Example fail:** Correct option 95 chars, all distractors 40–50 chars.

### L7 — Schema

Reject if:
- Pack structure violates `docs/QUESTION_SCHEMA.md`
- Any MC/scenario question has duplicate option text after normalization (whitespace collapse, lowercase)
- A matching question's `correctPairs` length ≠ `leftItems` length, or any `correctPairs[i]` is not a valid index into `rightItems`
- A matching question's `rightItems` contains duplicate entries after normalization (reuse the index in `correctPairs` instead of repeating an entry)

**Matching length note:** `rightItems` MAY be shorter than `leftItems`. Per
AUTHORING.md and Level 1/2, several left items can legitimately share one right
answer by reusing its index in `correctPairs`, so L7 does **not** require
`len(leftItems) == len(rightItems)` (a former false-critical, now removed).

### L8 — Parenthetical Justification (Multiple Choice / Scenario)

Reject if the correct option’s parenthetical does not paraphrase its own pre-parenthesis label with at least 3 shared content words.

**Example pass:** `"(C) ATP (energy currency of the cell)"` — "ATP" and "energy" and "currency" or "cell" share concepts.

**Example fail:** `"(C) Mitochondria (site of glycolysis)"` — Mitochondria and glycolysis are unrelated; no paraphrase.

### L9 — Intra-Pack Near-Duplicate Stem

Pairwise Jaccard similarity on prompt tokens:
- ≥ 0.5 → WARN
- ≥ 0.7 → CRITICAL FAIL

Rewrite or merge overlapping questions.

### L10 — Distractor Coverage (Multiple Choice / Scenario)

A good explanation says why the *wrong* answers are wrong, not only why the right
one is right — a learner torn between two plausible options is helped only when
the explanation addresses the distractor. This is the deterministic proxy for the
Level 4 rule "the explanation teaches the reason, not just the answer."

Because "addresses the distractor" is semantic, Layer A uses a token proxy: for
each distractor, does a distinctive token from it appear in the explanation?

- Addresses **none** of the checkable distractors **and** uses no contrast
  language → **CRITICAL**.
- Addresses **some but not all** → **WARN** (high recall; surfaces partials even
  when a contrast cue is present).
- Addresses **all** → clean.

**Contrast-cue rescue (critical tier only):** an explanation with zero literal
token matches but comparative prose ("…address other threats", "unlike a stream
cipher", "instead", "whereas") is assumed to cover distractors by paraphrase and
is *not* failed. A literal token check cannot see paraphrase, so the guard errs
toward not blocking — the safe direction for a gate. This is why a one-clause
contrast statement is enough to satisfy L10 on pure-recall questions (e.g. "what
year…", "which planet…") that have no per-distractor concept to explain.

**Checkable distractors:** options carrying no token ≥ 3 chars (e.g. "16", "$2.00")
cannot be assessed and are excluded from the denominator rather than counted as
unaddressed — otherwise every numeric-answer question would false-fail.

**Example fail (critical):** Prompt "Which cipher is provably unbreakable…?",
answer "One-time pad", explanation describes only the one-time pad and never
mentions stream cipher, RSA, or block cipher.

**Example pass:** the same answer, explanation adds "A stream cipher only
approximates it…; RSA and block ciphers rely on computational hardness instead."

**Known limit:** L10 is a heuristic. It cannot distinguish "ignores the
distractors" from "addresses them in different words" with certainty — the cue
rescue handles the common paraphrase case, but genuine semantic coverage checks
belong to the Layer B/C critic. Treat L10-critical as "this explanation almost
certainly only justifies the key" and L10-warning as "consider whether the
unaddressed distractors deserve a sentence."

### L12 — Explanation Presence + Topic/Difficulty Hygiene

Closes the Level-1 gap "reject if missing explanation" that no automated rule
previously enforced. L12 also owns the empty-explanation defect that L10
deliberately ignores (L10 returns clean on a blank explanation rather than
double-reporting it).

- Missing or blank `explanation` (after strip) on a `multiple_choice`,
  `scenario_multiple_choice`, or `matching` question → **CRITICAL**.
- Missing or blank `topic` → **WARNING** (all types).
- Missing or blank `difficulty`, or a `difficulty` not in
  `{easy, medium, hard}` → **WARNING** (all types).

`true_false` is exempt from the explanation-presence critical — the schema does
not require one. Topic/difficulty issues are warnings (not criticals) so a pack
lacking metadata cannot break the no-new-criticals ratchet.

**Example fail (critical):** an MC question with `"explanation": ""`.

**Example warn:** a question with `"difficulty": "trivial"` (not a recognized
difficulty level).

### L13 — Duplicate Question ID (pack-level)

Level-1 schema validation requires unique question `id`s ("reject if duplicate
question IDs"). L7 only checks each id is a non-empty string per question;
uniqueness is a pack-level property, so L13 owns it (sibling to L9).

- Any `id` appearing more than once in a pack → **CRITICAL**, attributed to the
  duplicated id.

**Example fail:** two questions in the same pack both with `"id": "ch1q4"`.

### L14 — Meta-Distractor (Multiple Choice / Scenario)

- An option matching `all/none/both/any of the (above|following)` → **WARNING**
  (gameable: pickable by elimination, and it interacts badly with option
  shuffling).
- A **position-referential** option — "Both A and B", "A and C", "options 1 and
  3", "1 and 3" → **CRITICAL**. The renderer's `shuffleOptions` reorders options
  at display time, so a reference to a fixed position points at the **wrong**
  option once shuffled. This is a correctness bug, not a style smell. The
  bare-number form is restricted to single digits so a real numeric answer
  ("16 and 32") does not false-fire.

### L15 — Matching Near-Duplicate Options

The L9 Jaccard machinery applied to a matching question's `leftItems` and
`rightItems` (Level 4: reject a set whose choices are so similar the learner
guesses wording variants rather than concepts). Pairwise token-Jaccard:

- ≥ 0.6 → **WARN**, ≥ 0.8 → **CRITICAL**.

Tuned higher than L9's 0.5/0.7 because matching options are short and naturally
share a domain noun ("digital signature", "private key"). A min-token guard skips
any item with fewer than 3 content tokens so 2-3-word options do not false-fire.
Synonym variants (verify/confirm) are **not** caught here — that is semantic,
Layer B/C.

### L16 — Answer-Position Distribution (pack-level)

Within an option-count group (all 4-option MC, all 5-option MC, …) with at least
5 items, if more than **70%** of the correct indices fall in one slot →
**WARNING**. **Never critical** — the renderer shuffles options at display time,
so a constant answer index is an authoring-hygiene smell (a rushed-batch tell,
and gameable only on a surface that bypasses the shuffle: export, seeded review,
print), not a live-play exploit. Advisory; attributed to the pack.

### L17 — true_false Tells + T/F Balance

`true_false` items were previously touched only by L7/L9. L17 adds two advisory
checks, both **WARNING**, never critical (detection is deterministic but the
gameability inference is heuristic):

- **(a)** an absolute qualifier (`always`, `never`, `all`, `none`, `every`,
  `only`, `cannot`, `guaranteed`) in a statement keyed **False** — the
  "absolutes are usually false" giveaway. A True-keyed absolute is fine (the
  statement may be legitimately absolute).
- **(b)** pack-level T/F key imbalance: with ≥ 5 `true_false` items, a minority
  share below **30%** → the pack is guessable by always picking the majority.

**Example warn (a):** "Compliance with PCI DSS is *always* legally mandatory."
keyed `false`.

### L18/L19 — Precision pass + threshold tuning (refinements, not new rules)

These tune existing rules; they do not add codes.

- **Word-boundary matching (L1/L2/L10).** Token-presence tests use a
  word-boundary regex, not raw substring, so "port" no longer matches
  "Reporting", "host" no longer matches "Ghost", and "attack" no longer counts
  "Replay attack" as covered just because the explanation says "attacker". L1
  keeps **substring** matching for short all-caps acronym left-tokens, so an
  acronym that is a prefix of a longer term (DNS → DNSSEC) still flags.
- **L2** distinctive-noun floor bumped 4 → 5 chars for plain MC, and `STOP_TOKENS`
  extended with tech-filler connectives (use/used/using/via/per/because/…).
- **L3** adds a **WARNING** tier below the critical: the correct option is the
  single strictly-longest AND exceeds the **mean** distractor length by ≥ 25%
  (with a modest absolute-gap floor so trivial-length differences do not fire).
- **L9** adds a min-token guard: a stem with fewer than 5 content tokens cannot
  reach **CRITICAL** on a couple of shared words; it is capped at WARNING.
- **L10** `CONTRAST_CUES` tightened: the over-broad generic cues (`differ`,
  `rather`, `instead`, `others`, `the other`, `while the`) were dropped; the
  phrase-level cues (`unlike`, `by contrast`, `in contrast`, `as opposed`,
  `whereas`, `not because`, `other option/answer/choice`) and the calibrated
  `other threat` phrase are kept.

### L20 — Acronym-Expansion Leak (Matching)

L1 catches a literal acronym string leaking across a pair, but misses the common
case where the correct right item paraphrases the acronym's **expansion** —
MD5 → "message-digest hash", ECC → "curve mathematics", SRTP → "real-time",
S/MIME → "mail". The pair is then guessable by surface overlap with no domain
knowledge, even though the acronym string itself is absent. → **WARNING**.

This is the linter's only domain-aware rule: a curated `ACRONYM_EXPANSIONS` table
(security/networking) maps each known acronym to distinctive expansion keywords,
and the rule flags a correctly paired right item that contains one as a whole
word. It is a surface-overlap heuristic (a few keywords like "standard"/"mail"
are generic), so it is WARNING, not critical, and deliberately **incomplete**:

- An acronym **absent** from the table is not checked (under-fire, never a
  false-fire). Extend the table per-course as new acronym families appear.
- The semantic **synonym-leak** variant (left "verify" vs right "confirm") is out
  of scope for Layer A and routes to the Layer B/C critic.

**Investigation note (Task 20):** an initial-letter heuristic was rejected as
high-FP / low-recall (it misses paraphrased expansions like ECC → "curve
mathematics"). The curated-dictionary proxy was chosen because it is empirically
false-positive-free across the live + archived corpus (every item it flags is a
real leak), while keeping its known limits explicit.

### L21 — Low-Priority Deterministic Checks

- **(a) Scenario floor (scenario_multiple_choice).** A scenario prompt under
  ~15 words is bare recall mislabeled as a scenario → **WARNING**. A genuine
  scenario sets up a situation; the floor is set well below the live corpus
  minimum so it only catches genuinely bare prompts.
- **(b) Diagram answer-leak (MC / scenario_MC with a diagram).** When a diagram
  is present (string SVG/Mermaid/text, or an object with those fields), a
  distinctive token of the **correct** option that appears in the diagram markup
  but in **none** of the distractors leaks the answer → **CRITICAL**. A diagram
  with no `diagram_alt` text → **WARNING** (accessibility + a nudge to review the
  visual for leaks). Latent today — no shipped pack uses diagrams — but enforced
  the moment one does.
- **(c) Article a/an agreement** — DEFERRED. No shipped pack ends a stem in a
  standalone "a"/"an" before a blank, so the check is left as a `# TODO(L21c)` in
  the rule rather than shipping untested code; revisit when such a stem appears.

## Authoring-time gate (shift-left)

Quality is enforced when a pack is **created**, not when the app launches:

- `scripts/lint_hook.py` is a Claude Code PostToolUse hook (wired in
  `.claude/settings.json`, matcher `Write|Edit|MultiEdit`). The moment a pack
  under `question-packs/<course>/` is written or edited, it runs the linter and,
  if any live finding remains, exits 2 with the report — Claude Code feeds that
  back to the model so the finding is fixed in the same session.
- `scripts/build_manifest.py` (run by `start.sh`) is therefore **quiet** about
  quality: it prints one summary line, surfaces only criticals per-pack, and
  writes full detail to `/tmp/quizzler-lint.log`. Use `--verbose` (or
  `QUIZZLER_LINT_VERBOSE=1`) for the full inline list. The wall of per-question
  warnings no longer appears at launch because packs are already clean.

The standard is **0 critical and 0 warning** before a pack is "done". Run the
gate by hand anytime with `python3 scripts/lint_packs.py path/to/pack.json`.

### Why the three gates disagree on "clean" (launchable ⊂ done)

The build and the readiness gate apply the **same Layer-A rules at different
severity thresholds** — this is intentional, not a bug:

- **`build_manifest.py` (per-launch)** blocks only on Layer-A **criticals**;
  warnings are advisory (logged, not fatal). A pack with warnings still *launches*
  so a metadata gap or a borderline distractor-coverage heuristic never bricks the
  app at startup.
- **`scripts/lint_hook.py` (per-edit)** and **`scripts/verify_pack.py` (readiness
  gate)** block on **any** live Layer-A finding — criticals **and** warnings.

So a warning-only pack is **launchable but not done**: it boots fine yet will not
pass `verify_pack`. Read it as a ladder — *launchable ⊂ done*. The build keeps the
app running; the hook and the readiness gate hold the bar for "ship-ready". Note
that **WAIVER hygiene** warnings (a stale/malformed `lint_waivers` entry) are the
one exception the readiness gate treats as advisory rather than blocking — they
are list-rot nudges, not content defects (the same way Layer C treats its own
waiver hygiene).

## Waivers

A finding can be genuinely intentional (a deliberately tricky distractor that
trips a heuristic, a teaching example, a known token coincidence). Suppress it —
with an auditable reason — via a top-level `lint_waivers` array in the pack:

```json
{
  "pack_id": "...",
  "lint_waivers": [
    { "rule": "L10", "qid": "c3q7", "reason": "pure-recall year question; distractors share no concept to contrast" }
  ],
  "questions": [ ... ]
}
```

- `rule` (required) — the rule code to suppress (e.g. `"L1"`, `"L10"`).
- `qid` (optional) — limit the waiver to one question; **omit** to waive the
  rule pack-wide.
- `reason` (required) — the justification; recorded in the linter's `waived`
  output for the audit trail.

A waived finding moves from `violations` to `waived` (non-blocking). The linter
keeps the list honest: a waiver that matches no finding (**stale**) or carries no
`reason` is reported back as a `WAIVER` warning, which itself blocks the gate
until cleaned up. Prefer **fixing** a finding over waiving it — a waiver is a
deliberate, reviewed exception, not a mute button.

### `factcheck_waivers` — the Layer-C escape valve

Layer C (the factual critic) has the same escape valve, with the same shape, via
a top-level `factcheck_waivers` array. Because a Layer-C finding is keyed by
question rather than by rule code, a waiver targets a `qid` (not a `rule`):

```json
{
  "pack_id": "...",
  "factcheck_waivers": [
    { "qid": "c3q7", "reason": "textbook simplification; verified against SY0-701 objectives" },
    { "qid": "c3q9", "severity": "nit", "issue_contains": "acronym", "reason": "spelled out elsewhere in the pack" }
  ],
  "questions": [ ... ]
}
```

- `qid` (required) — the question the waiver applies to.
- `severity` (optional) — narrow the waiver to one finding class
  (`wrong-answer`, `misleading-explanation`, `ambiguous`, `nit`).
- `issue_contains` (optional) — a case-insensitive substring of the finding's
  `issue`, so one waiver can dismiss a single finding on a `qid` without
  suppressing every finding the critic raises for that question.
- `reason` (required) — the justification, recorded in the critic's `waived`
  output.

Mirroring `lint_waivers`: a waived finding moves out of the blocking set; a
malformed (non-object) entry, a stale waiver (matched nothing), or one missing a
`reason` is reported as a non-blocking hygiene warning. Because Layer C is
probabilistic, a waiver here is the right tool for a genuine **false positive** —
verify against a source first, then waive with the citation in `reason`.

## Pack-readiness gate (`verify_pack`)

Layer A and Layer C run independently — the hook and build enforce Layer A, and
the critic is run on demand. The single command that certifies a pack is **done**
runs BOTH as one hard gate:

```bash
python3 scripts/verify_pack.py question-packs/<course>/<pack>.json
```

- Exit **0** (`PACK READY`) only when Layer A has zero live findings AND Layer C
  ran with zero live findings, zero batch errors, and **full coverage** — every
  question actually inspected (each after its own waivers are applied).
- Exit **2** (`PACK NOT READY`) when either layer reports a live finding, when
  Layer C coverage was incomplete (a batch errored/timed out, or the critic
  self-reported inspecting fewer questions than were sent — printed as
  `Layer C coverage incomplete (N question(s) unchecked)`), or when the pack has
  no questions. A timed-out or partial-coverage run **never** certifies ready.
- Exit **3** (structure-only run, `--no-factcheck`): Layer A is clean but Layer C
  did **not** run, so the pack is **NOT** certified ready. `--no-factcheck` never
  returns 0 — a CI `verify_pack --no-factcheck && deploy` can't ship an
  unfactchecked pack.
- Exit **1** on operational error (pack unreadable, or the `claude` CLI is
  missing when a factcheck was requested).

Flags: `--no-factcheck` (structure-only — prints a prominent note that this is
**NOT** the full readiness gate, since the full gate requires Layer C; exits 3),
`--model <name>`, `--batch-size N`, `--timeout S`, `--json`.

`verify_pack` is **not** wired into the per-edit hook or the per-launch build:
Layer C is a slow, costly, non-deterministic LLM pass, so it is a deliberate,
on-demand step run once before a pack ships — Layer A alone covers the
per-edit/per-launch path.

## Layer C — factual critic (structure vs. truth)

The Layer-A linter is deterministic and token-based: it checks a question's
**structure** — schema, answer-leak tells, distractor coverage, duplicate stems —
but has **no domain knowledge and cannot tell whether a claim is true**. A
well-formed question that says "JSON is non-human-readable" or "a Bridge CA signs
no certificates" passes every Layer-A rule. Factual correctness is a separate
concern, owned by **Layer C**:

```
python3 scripts/factcheck_pack.py question-packs/<course>/<pack>.json
```

`scripts/factcheck_pack.py` sends each question's keyed answer + explanation to an
LLM (via the `claude` CLI) and reports suspect factual claims with a suggested
correction and a confidence. Flags: `--dry-run` (print prompts, no LLM call),
`--batch-size N`, `--model <name>`, `--json`. Exit 2 if it reports findings.

Properties to keep in mind:

- **Not in the hook.** An LLM pass is slow and costs money (~$0.10+/call), so it is
  a deliberate, on-demand authoring step — run it before a new or substantially
  changed pack is "done" — not part of the per-edit PostToolUse gate.
- **Probabilistic.** The critic can be wrong in both directions (false positives and
  misses). Its output is a review aid, not a verdict: verify each finding against a
  source before editing, and spot-check exam-critical content yourself.
- **Layers compose.** Layer A guarantees *well-formed*; Layer C raises confidence in
  *correct*. Neither replaces a human read of content that a student will be graded on.
- **Run it via the gate.** The pack-readiness gate `scripts/verify_pack.py` runs
  Layer A + Layer C together and is the only thing that certifies a pack "done"
  (see *Pack-readiness gate* above). A genuine critic false-positive is dismissed
  with a `factcheck_waivers` entry, not by editing a correct question.

## Manual QA Checklist

Before shipping a round, ask:

1. Does any image give away the answer?
2. Is any question visually sloppy?
3. Are there too many repeats from the last round?
4. Are some questions better as plain text?
5. Does the pack include enough breadth for the learner’s score level?

If any answer is yes, revise before release.
