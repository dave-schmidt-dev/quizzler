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

Now enforced deterministically by **L23 — Coverage Completeness** (Tier 7 below)
via the top-level **`coverage_blueprint`** contract. Every **installed** pack
(manifest-visible under `question-packs/<course>/`, not archived) must declare its
intended topic universe in-pack; L23 then fails the gate (CRITICAL) when the
blueprint is absent, when a required topic is under-covered, and warns on
over-concentration and near-duplicate (fragmented) topic slugs.

> **Policy reversal (2026-07-20, INV-7):** L23 used to emit a single
> **advisory** nudge when `coverage_blueprint` was absent, so pre-existing
> blueprint-less packs never newly failed a gate. That opt-out is retired —
> missing blueprint is now **CRITICAL**, same as under-covered topics. Pack
> authors must add a blueprint before a pack can install or ship.

Check:

- the pack reflects the intended topic mix — declare a `coverage_blueprint`
  so L23 enforces it (every blueprint topic has ≥ its `min` questions)
- under-covered topics are included when high-performance mode is active
- definition and distinction questions appear when needed
- the pack is not visually homogeneous
- no single topic dominates the pack (L23 warns above the
  `L23_OVERCONCENTRATION_SHARE` ceiling)
- topic slugs are not fragmented across variants (L23 warns on near-duplicate
  slugs like `shared-responsibility` vs `shared-responsibility-model`)
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
- A `multiple_select` question has fewer than 3 `options`, duplicate option text, or an `answers` array that is missing/empty, non-list, contains a non-integer/boolean or out-of-range index, has duplicate indices, or covers **every** option (all-correct is trivial)

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

### L22 — Multiple Select Quality

`multiple_select` keys an `answers` array rather than a single index, so the
single-answer MC heuristics (L2/L3/L8/L10/L14) do **not** run on it. L22 is their
multi-answer analog. Structural validity is L7's job; L22 assumes a well-formed
item. Tiering mirrors L3/L14 — only the shuffle-breaking position reference is a
CRITICAL; the rest are gameable-but-not-broken **WARNING**s so a new
`multiple_select` cannot trip the no-new-criticals ratchet on a style smell.

- **Exactly one correct answer** → WARNING (author as `multiple_choice`).
- **Correct count == options − 1** (one lone distractor) → WARNING (near-trivial).
- **Meta-option** ("all/none of the above") → WARNING (contradictory/gameable in a select-all).
- **Position-referential option** ("Both A and B", "1 and 3") → **CRITICAL**:
  `shuffleOptions` reorders options at render, so the reference points at the wrong option.
- **Length tell** — the correct set averages conspicuously longer/shorter than the
  distractor set (L3's ratio/gap applied to set means) → WARNING.
- **Stem echo** — a distinctive prompt term appears only in the correct options → WARNING.
- **Count disclosure** — the prompt states how many answers are correct → WARNING (narrows guessing).

`explanation` is required for `multiple_select` (enforced by L12), same as MC/scenario/matching.

### L24 — Advisory Acronym-Expansion Rule (non-blocking)

Flags unexpanded first-use acronyms in explanations. Driven by
`detect_unexpanded_acronyms` (shared detector from Task A.2), which scans for
acronym forms (`[A-Z]{2,}` and mixed forms like `S/MIME`, `FIPS 140-2`) lacking a
same-explanation parenthetical expansion. Allowlisted terms (guide-assumed,
well-known standard acronyms) live in `ACRONYM_ALLOWLIST`.

- **Severity: `advisory`** — non-blocking at every gate (hook, build, readiness).
  The rule is an authoring nudge, not a correctness defect; an unexpanded acronym
  does not make the question *wrong*.
- **Blocking tier:** never (advisory-only). A successful hybrid certification is not affected
  by L24 findings.
- **Waiverable:** pack-wide via `{"rule": "L24"}` in `lint_waivers` (omit `qid`).

**Example:** "PCI DSS requires merchants to..." with no "(Payment Card Industry
Data Security Standard)" expansion → advisory.

**Known limit:** domain-agnostic (pure regex + allowlist). L24 cannot distinguish a
genuinely missing expansion from one that appeared earlier in the pack but outside
the same explanation text — treat its findings as surface-level reminders, not
mandates.

### L23 — Coverage Completeness (pack-level)

Codifies the **FULL TOPIC COVERAGE** standard (Level 6 above). A pack may declare
its intended topic universe as a top-level **`coverage_blueprint`** array
(documented in `docs/QUESTION_SCHEMA.md`); L23 enforces that every required topic
is actually covered, and surfaces two coverage smells that apply with or without a
blueprint. It is a **pack-level** rule (sibling to L13/L16): every finding is
attributed to the pack (`qid` omitted) and names its specifics in the detail.

- **Blueprint under-coverage → CRITICAL** *(only when a blueprint is declared)*.
  A required `(topic, area)` pair covered by fewer than its `min` questions,
  one finding per under-covered pair. Topic and area match exact equality after
  case-insensitive strip. Entries without `area` retain the legacy topic-only
  wildcard behavior.
  *Example CRITICAL:* a blueprint `[{"topic": "rds-multi-az", "area": "domain-2", "min": 2}]` on a
  pack with no matching pair →
  `coverage_blueprint requires >=2 question(s) on topic 'rds-multi-az' and area 'domain-2'; found 0`.
- **Over-concentration → WARNING** *(blueprint or not)*. Any single topic whose
  share of all questions exceeds `L23_OVERCONCENTRATION_SHARE` (**0.15**). Fires
  only once the pack has at least `L23_MIN_PACK_FOR_CONCENTRATION` (**10**)
  questions, so a tiny pack can't false-fire on a 1-of-3 topic.
  *Example WARNING:* `topic 'aws-kms' is over-concentrated: 3/10 (30%) …`.
- **Near-duplicate topic slugs → WARNING** *(blueprint or not)*. Two DISTINCT
  slugs that look like fragmentation of one concept — detected by slug-token
  Jaccard ≥ `L23_SLUG_JACCARD` (**0.6**) OR a prefix-extension relationship
  (`shared-responsibility` ⊂ `shared-responsibility-model`), with a min-token
  guard (both slugs ≥ `L23_SLUG_MIN_TOKENS` = **2** tokens) so a 1-token slug like
  `soar` doesn't false-fire against `siem-vs-soar`.
  *Example WARNING:* `near-duplicate topic slugs 'shared-responsibility' and 'shared-responsibility-model' … consolidate to one slug`.
- **No `coverage_blueprint` declared → CRITICAL**. Every installed pack must
  declare a top-level `coverage_blueprint` so required-topic coverage can be
  gated. This was advisory before INV-7 (2026-07-20); it is now a hard
  CRITICAL — same blocking tier as blueprint under-coverage.
  *Example CRITICAL:* `pack declares no coverage_blueprint (CRITICAL — add a
  top-level coverage_blueprint to gate on required-topic coverage)`.
- **Ambiguous topic→area mapping → WARNING** *(Q.9 Layer A)*. A topic used
  with more than one `exam_area` across this pack's own questions. Advisory
  only — a real syllabus can legitimately span one topic across domains (a
  CISSP `risk-assessment` topic under both Domain 1 and Domain 6, for
  example), so this is a review nudge, never a blocking failure. A
  single-area-per-topic CRITICAL was considered and rejected (2026-08-07) as
  an unsatisfiable gate against real multi-domain topics.
  *Example WARNING:* `topic 'risk-assessment' is used with 2 different
  exam_area values (domain-1, domain-6); confirm this is a deliberate
  multi-domain topic and not an inconsistent tag`.

**Constants** (in the `lint_packs.py` constants block):
`L23_OVERCONCENTRATION_SHARE = 0.15`, `L23_MIN_PACK_FOR_CONCENTRATION = 10`,
`L23_SLUG_JACCARD = 0.6`, `L23_SLUG_MIN_TOKENS = 2`, `L23_DEFAULT_MIN = 1`.

> **Blueprint vacuousness (Q.9), closed 2026-08-10.** L23 checks the
> blueprint the pack declares; on its own it does not check that the
> blueprint asks for anything. Both packs in this repo's early history were
> generated with one `{"topic": X, "min": 1}` entry per *distinct* topic, and
> because topics were near-unique per question (sy0-701: 157 topics across
> 160 questions; samples: 6 across 6), every entry was satisfied by the
> question that produced it — such a blueprint cannot fail on its own. Three
> layers close the gap, all cross-checking the blueprint against the course's
> externally published `syllabus`: L23's `(topic, area)` keying (Layer A,
> above) plus L27 findings 11-14 (Layers B and C, below) bind blueprint
> topics to real syllabus areas and their published weights, so a
> self-derived blueprint no longer passes just by restating the pack that
> produced it.

L23 is waiverable pack-wide via a `{"rule": "L23"}` `lint_waivers` entry (omit
`qid` for the pack-level finding). Being a Layer-A rule, the **hybrid gate** picks it
up automatically** — CRITICAL and WARNING tiers block the readiness gate like any
other live finding. **Installed packs must not carry an L23 waiver** (INV-7).

### Course workload guardrail (INV-10)

Pack-level quality does not control the total number of questions a learner must
process. `scripts/build_manifest.py` therefore applies a course-level workload
gate to the sum of all valid modules in each course:

- **More than 200 questions:** advisory planning warning.
- **More than 240 questions:** hard build failure; no manifest is written.
- **`question_budget.target` in `_course.json`:** optional lower planning target;
  it may document intent but cannot raise the 240-question ceiling.

`--no-strict` does not bypass the hard ceiling. The explicit
`--allow-course-size-preview` switch exists only for local WIP/test preview
servers while legacy fixtures are still oversized; it is not an installation,
CI, pre-push, or shipping path.

### Strict manifest and harness contract

The normal manifest build is strict: `QUIZZLER_LINT_STRICT=1` is pinned by the
Playwright web server, and the web server is never satisfied by an already-running
process. The builder returns **0** for a clean install, **2** when failing courses
are excluded but survivors produce a manifest, and **1** when no manifest is
written. The web server accepts 0 and 2 and rejects 1. `--no-strict` and
`--allow-course-size-preview` are preview-only bypasses and must not appear in CI,
pre-push, or shipping commands.

The static server's `resolve_static_path` resolves both the requested path and
the canonical route root before containment checking. Traversal and symlink
escapes therefore return 404; this is covered by `tests/test_serve.py`.

### Course-Level Aggregate Stats (`--course-stats <dir>`)

Course-wide advisory checks run on demand via `lint_packs.py --course-stats
<course-dir>`. Every finding emits `severity: "advisory"` — non-blocking at every
gate. These are authoring-hygiene nudges, not correctness defects.

**Checks:**

- **(a) T/F key balance:** when a course has ≥ 10 `true_false` items and the
  minority key share falls outside 35–65%, emit an advisory. A heavily imbalanced
  T/F pack is guessable by always picking the majority key — the renderer does not
  shuffle true/false options (they are binary True/False, not positional), so
  imbalance is genuinely gameable in-app.
- **(b) Type-mix distribution:** report the course-wide question-type percentages
  against the canonical target from `question-packs/AUTHORING.md` (~55% MC, 20%
  matching, 10% scenario, 10% true_false, 5% multiple_select). A significant
  deviation is surfaced as advisory — the target is a guideline, not a hard bar.
- **Dropped by default:** per-pack answer-position skew (L16 already covers it
  at the pack level, and the renderer shuffles option order at runtime).

**Blocking tier:** never (advisory-only).

**Example:** `--course-stats sy0-701` reports T/F True at 71% with 28 T/F items
→ advisory: "T/F key share outside 35–65% band."

## Authoring-time gate (shift-left)

Quality is enforced at repository boundaries, not by an editor integration:

- `.githooks/pre-commit` runs deterministic Layer-A lint for staged packs and
  native source lint/dead-code checks. `.githooks/pre-push` performs the heavy
  gate and checks certification freshness for packs changed by the proposed
  push (or the staged set only when invoked without push input).
- `scripts/lint_hook.py` remains only as a legacy standalone stdin adapter for
  compatibility tests; it is not wired to Claude Code or any PostToolUse event.
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
- **The pre-commit hook (staged-pack gate)** and **`scripts/hybrid_verify.py`
  (readiness gate)** block on **any** live Layer-A finding — criticals **and**
  warnings.

So a warning-only pack is **launchable but not done**: it boots fine yet will not
pass `hybrid_verify`. Read it as a ladder — *launchable ⊂ done*. The build keeps the
app running; the hook and the readiness gate hold the bar for "ship-ready". One
class of finding is treated as **advisory-at-gate** — surfaced but never a reason
to fail an otherwise-clean pack:

- **WAIVER hygiene** warnings (a stale/malformed `lint_waivers` entry) — list-rot
  nudges, not content defects (the same way Layer C treats its own waiver
  hygiene). Excluded from the readiness gate's blocking set (rule `WAIVER`).

L23 absent-`coverage_blueprint` is **CRITICAL** (INV-7) and blocks every gate,
including the hybrid gate, the pre-commit hook, and strict `build_manifest`.

### L25 — Self-Contained Prompts (usability)

**CRITICAL. Non-waivable.**

A prompt must be answerable from the prompt and its options alone. A question
that points at material the learner does not have in front of them ("According
to the chapter…", "Which port does the textbook list…") is unanswerable at quiz
time no matter how correct its key is.

This rule exists because a real pack shipped 54 of them. They arrived from
per-chapter packs — where "according to the chapter" was *correct*, because the
learner had the chapter open — and were consolidated verbatim into a standalone
final-review pack. Nothing in the pipeline modeled that the same sentence changes
truth value when the surrounding context is removed. **When you move questions
between packs, prompt self-containment does not move with them.**

Matching is two-tier, to keep ordinary security vocabulary out of the net:

- **Unambiguous source nouns** fire bare — `chapter`, `textbook`, `Exam Cram`,
  `study guide`, `courseware`, `handout`, `lecture`, `course material`. These
  have no legitimate reading as exam content.
- **Ambiguous source nouns** (`book`, `text`, `author`, `module`, `lesson`,
  `reading`, `section`, `slide`, `video`, `transcript`) fire **only inside an
  attribution frame** — a possessive or an attribution verb (`the author says`,
  `which risks does the module identify`). Bare uses stay clean, so "the author
  of the signing request", "the text field", "which symbols the module exports",
  and "a cipher that uses a book as its key" are not flagged.

**Known false positive.** An attribution frame over a *citable standard* reads
the same to the matcher as one over course material: "Which section of RFC 5280
defines…" or "what the NIST publication recommends" will fire even though the
learner can be expected to know it. Because L25 is non-waivable, rewording is the
only recourse — name the thing directly ("Which RFC 5280 extension marks a CA
certificate…"). This is rare enough that the false-negative cost of loosening the
frame is worse.

### L26 — Exam-Invalid Question Formats (usability)

**CRITICAL. Non-waivable.**

`true_false` and `matching` (`EXAM_INVALID_TYPES`) are rejected. Practice in a
format the real exam never presents trains the wrong retrieval skill and inflates
measured readiness — a 50/50 T/F item is mostly a coin flip, and matching gives
away answers through elimination across the pair set.

This is a **hard fail everywhere**, not only on packs tagged as exam prep: the
distinction between "exam course" and "study course" is metadata an author can
get wrong, and that is exactly the kind of narrow scoping that let the last bad
pack through.

### L27 — Exam-Area Alignment (pack-level + per-question)

**CRITICAL. Non-waivable.** Fires only for packs that live in a course directory
(one with a sibling `_course.json`); a bare file passed on the command line or a
tmp fixture has no course to align against, and gating it would gate on the
caller's directory layout rather than on the pack.

An absent `_course.json` means the pack is not in a declared course, so L27 does
not fire. A present file that is invalid JSON or whose root is not a JSON object
is different: L27 emits a critical because the course taxonomy cannot be read,
and `build_manifest.py` refuses to install that course instead of deriving
silent defaults. This distinction preserves the 0/1/2 build exit contract while
making malformed course metadata fail closed.

The course declares its taxonomy once, in `_course.json`:

```json
"syllabus": {
  "source": {
    "kind": "exam_objectives",
    "title": "…",
    "url": "…",
    "version": "…",
    "syllabus_verified_by": {"reviewer": "independent reviewer", "date": "YYYY-MM-DD"}
  },
  "areas": [
    {"id": "1.0", "name": "Fundamentals", "weight": 25},
    {"id": "2.0", "name": "Threats and Mitigations", "weight": 30},
    {"id": "3.0", "name": "Architecture and Design", "weight": 25},
    {"id": "4.0", "name": "Operations", "weight": 20}
  ]
}
```

and every question carries `exam_area` naming one declared area id. The area
names above are placeholders for a fictional exam, and the list is shown
*complete* on purpose: an abridged copy of a real vendor's domains has weights
that do not total 100 and fails (6) below.

| # | condition | severity |
|---|---|---|
| 1 | course declares no `syllabus.areas` | critical |
| 2 | `source.kind` missing or not one of `exam_objectives` / `syllabus` / `none` | critical |
| 3 | a published `kind` with no `title`, or `exam_objectives` with a non-absolute/non-HTTPS `url` | critical |
| 4 | `exam_objectives` missing `syllabus_verified_by.reviewer` or `.date` | critical |
| 5 | duplicate, missing, or unnamed area ids | critical |
| 6 | weights on every area must be finite, non-negative, and sum to 100 (±0.5); a partial set is invalid | critical |
| 7 | question missing `exam_area` | critical |
| 8 | question naming an area the course does not declare | critical |
| 9 | a published source whose areas carry no weights | advisory |
| 10 | a declared area with no questions in this pack | advisory |
| 11 | a declared syllabus area named by no explicit-area `coverage_blueprint` entry | critical |
| 12 | a `coverage_blueprint` entry naming an area not declared in `syllabus.areas` | critical |
| 13 | a weighted pack at or above the 20-question floor whose area count falls outside the published-weight range | critical |
| 14 | a weighted pack whose `coverage_blueprint` `min` values, aggregated by area, fall outside the published-weight range | critical |

Every declared weight must be a finite JSON number: `NaN`, positive or negative
`Infinity`, and negative values are critical findings. These checks run before
the total comparison, so invalid values cannot pass merely by producing a total
within tolerance. Finite, non-negative weights that total 100 (within ±0.5)
produce no weight finding.

**Why the taxonomy is external.** For a certification or licensing exam the
vendor publishes objective domains with percentages; for a class there is a
syllabus with chapters. Those are the guide. A taxonomy the pack author invents
measures alignment against the author's own mental model, which is the thing
under test, so `source` is required and `kind: "none"` exists to make "no
published authority" a deliberate declaration rather than an omission. For
`kind: "exam_objectives"`, L27 requires an absolute HTTPS citation plus an
attestation naming an independent reviewer and review date. A cited source is
not proof that the areas or weights were transcribed faithfully; the URL is
parseable evidence of where to look, and the reviewer/date are recorded
self-attestation rather than automated proof of the review's independence or
accuracy. `kind: "syllabus"` and `kind: "none"` deliberately do not require
either the URL or this attestation.

**Why (8) is the decisive check.** A typo'd `exam_area` does not look like an
error anywhere else in the toolchain: it produces a well-formed pack with a
plausible-looking area that holds one or two questions. Per-area accuracy then
computes over a near-empty denominator, and targeted study — which by
construction points at the *weakest* area — sends the learner straight at the
typo. That is why L27 is non-waivable: a justification attached to the waiver
does not make the resulting ranking any less wrong.

**Why (10) is advisory, not blocking.** A pack is one module of a course. An area
absent from one module is normal; whole-course area coverage is a course-level
question, and gating it per-pack would force every module to touch every domain.

**Why (11) and (12) are critical.** The blueprint and the published taxonomy
must describe the same area universe. An explicit blueprint area absent from the
syllabus is usually a typo that can silently evade coverage checks; a declared
syllabus area absent from the blueprint is an unmeasured part of the published
course. L27 emits one critical finding for each mismatch. Area ids compare after
case-insensitive stripping. Legacy topic-only blueprint entries keep their L23
wildcard behavior and do not establish an area-to-taxonomy join.

**Why weights matter (6, 9).** Presence-only coverage is unfalsifiable — see the
note under L23 about blueprints that declare `min: 1` for every topic when
topics are unique per question. Published percentages give area coverage a real
target to be measured against.

**Why (14) is a separate check from (13), not a duplicate (Q.9 Layer C).**
(13) checks the pack's actual output, which only exists once
`L27_AREA_DISTRIBUTION_FLOOR` questions are written. (14) checks the
blueprint's declared *intent* — `min` values, aggregated by area — against the
same weight-derived range, so a self-derived blueprint (`min: 1` per topic
regardless of how many topics an area holds) is caught at pack-creation time,
before any questions exist, not only after 20 are written. Topic-only
blueprint entries (no `area`) are excluded from the aggregate, matching their
legacy L23 wildcard treatment. Like (13), a single-module pack may
legitimately concentrate its blueprint on one area, so (14) also respects
`include_distribution`.

### Weighted area distribution (L27-DISTRIBUTION)

For a course whose syllabus gives every area a finite, non-negative `weight`,
`lint_pack` derives each area's expected question count from that published
percentage. The shared `area_weight_count_range` helper rounds expected counts
**half up** and defines an **inclusive ±5 percentage-point** integer range. A
weighted pack outside that range emits a **critical** L27 finding; packs with
fewer than **20 questions** and courses with no area weights are exempt. The
archived and staged courses predate the `syllabus` block, so re-linting one emits
L27 criticals unrelated to why it was retired.

The strict manifest build applies the same range to the course-wide aggregate
only after per-pack lint and install-gate failures have been pruned. That
surviving-pack aggregate remains the course installation gate: a rejected pack
cannot distort it, and the existing partial-install result remains exit **2**
when other courses survive (exit **1** when none do).

### Strict course-level installation gate (L27-DISTRIBUTION)

The builder reparses surviving pack files and aggregates their `exam_area`
counts across the whole course. An area outside its inclusive range excludes
the course; the narrow band is intentional because it rejects a deliberately
concentrated bank while the floor avoids arithmetic failures on small samples.

### Weighted blueprint distribution (L27-BLUEPRINT-DISTRIBUTION)

The pack-declared counterpart to L27-DISTRIBUTION, and the check that closes
Q.9: instead of the pack's actual `exam_area` counts, this aggregates
`coverage_blueprint` `min` values by area (topic-only entries excluded) and
runs the same `area_weight_count_range` helper — half-up expected count,
inclusive ±5 percentage-point range — against that aggregate. A weighted pack
whose blueprint aggregate falls outside the range emits a **critical** L27
finding; the same 20-unit floor and no-weight exemption apply.

### Strict course-level installation gate (L27-BLUEPRINT-DISTRIBUTION)

Every pack is linted with `include_distribution=False` (a module pack may
legitimately concentrate its blueprint on one area), so per-pack lint alone
never evaluates this finding — mirroring how L27-DISTRIBUTION defers to the
course-level aggregate below. `build_manifest.course_blueprint_distribution_findings`
reparses each surviving pack's `coverage_blueprint`, sums `min` values by area
across the whole course, and runs the same range check; an area outside its
inclusive range excludes the course, same as L27-DISTRIBUTION's course-level
gate. Without this course-level pass, nothing on the install path would ever
evaluate a course's declared blueprint intent against its published weights —
only the pack-level authoring-time and hybrid-gate checks would (both use
the `include_distribution=True` default).

### L28 — Source-Text Grounding Coverage (pack-level)

**CRITICAL. Waivable.** Fires only for courses that opt in by declaring a
`grounding` block in `_course.json`; a course with none configured (no chapter
text set up, or one that never will have any) is not gated — that is a
decision the course hasn't made yet, not a defect.

```json
"grounding": {
  "text_root": "/absolute/path/to/chapter/text",
  "packs": {
    "ch01-obj1.1-security-controls.json": "Chapter 1 Security Controls.txt"
  }
}
```

`source_directive` names a pack's source but supplies no content — the Layer-C
critic can only check a claim against real text when `grounding.packs` maps
this pack's filename to a `.txt` file under `text_root`, resolved by
`scripts/course_grounding.py` (the same lookup Layer C uses at review time, so
authoring and review are grounded in the identical file).

| # | condition | severity |
|---|---|---|
| 1 | course declares `grounding`, but this pack has no entry in `grounding.packs` | critical |
| 2 | this pack has an entry, but the mapped file cannot be resolved (missing, non-`.txt`, outside `text_root`, or empty) | critical |

**Why it's waivable, unlike L25-L27.** Those three rules protect properties
every question must have to be worth a learner's time — answerable, exam-shaped,
attributed to a real objective. Grounding coverage is different: a course can
legitimately opt in for most packs while one pack (a cross-chapter final review
with no single source chapter, say) has none. `lint_waivers` lets an operator
make that call explicitly, with a `reason`, rather than the rule forcing every
pack in an opted-in course to have one.

**Why a broken mapping is reported the same as no mapping.** A `grounding.packs`
entry that points at a typo'd or deleted filename degrades to the exact same
directive-only trust as an absent entry — the only difference is it *looks*
wired in on a casual read of `_course.json`. Checking that the mapped file
actually resolves (rather than just checking key presence) catches that
silently-broken state before it ships.

### L29 — Native Pack-Metadata Contract (pack-level)

**CRITICAL. Non-waivable.** Every field checked here is one QuizzlerKit's
`PackManifest.validate()` already enforces on device. A violation is not a
style opinion: the iOS client rejects the entire pack and the course never
appears, with no error surfaced on either side.

| # | condition | severity |
|---|---|---|
| 1 | `pack_id`, `subject`, or `title` missing or blank | critical |
| 2 | `version` is not `1` (the native contract version) | critical |
| 3 | `generation_mode` present and not one of `manual`, `templated`, `llm`, `hybrid` | critical |
| 4 | `notes` present and blank, or longer than 120 characters | critical |
| 5 | `generated_at` present and not an RFC 3339 timestamp with an explicit offset | critical |
| 6 | `questions` missing or empty | critical |

**Why this rule exists.** Packs are authored in Python and consumed in Swift,
and until 2026-08-18 nothing checked that the two agreed about anything above
the question array. The live CISSP pack declared
`"generation_mode": "llm-assisted"`; the Swift allowlist has never contained
that value; the app refused all 203 questions and shipped a three-question
hardcoded array instead. One undocumented string, no signal, an entire course
missing from the product.

`tests/test_lint_packs.py::NativeContractParityTests` reads the allowlist, the
contract version, and the notes limit **out of `PackManifest.swift`** and
asserts the Python constants equal them. A test that restated the values would
pass while the two languages drifted apart again, which is the failure this
rule was added to catch.

**Why it's non-waivable.** A waiver expresses "this finding is intentional."
No intention makes the app able to decode a file it rejects.

### Non-waivable rules

`NON_WAIVABLE_RULES` (currently `L25`, `L26`, `L27`, `L29`) cannot be suppressed. A matching
`lint_waivers` entry is **ignored** — the finding stays live — and the linter
emits one WAIVER hygiene warning naming the ignored entry, so a silenced-looking
waiver is never silently trusted. These are quality-bar rules; a bar you can
waive is not a bar. L29 is there for a different reason: it states what the
shipping client can physically load.

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

## Pack-readiness evidence (`hybrid_verify`)

Layer A and Layer C run independently — the hook and build enforce Layer A, and
the critic is run on demand. Reviewer commands record discovery or targeted
evidence; certification is a separate deterministic command after the campaign
ledger is complete:

```bash
python3 scripts/hybrid_verify.py question-packs/<course>/<pack>.json --no-certify --json --campaign-snapshot sha256:<frozen-snapshot>
python3 scripts/hybrid_verify.py question-packs/<course>/<pack>.json --certify-campaign <ledger>
```

- Discovery exit **0** (`PACK READY`) only when Layer A has zero live findings AND Layer C
  ran with zero **blocking** findings, zero batch errors, and **full coverage** —
  every question actually inspected (each after its own waivers are applied).
- Exit **2** (`PACK NOT READY`) when Layer A reports a live finding, when Layer C
  reports a **blocking** finding, when Layer C coverage was incomplete (a batch
  errored/timed out, or the critic self-reported inspecting fewer questions than
  were sent — `Layer C coverage incomplete (N question(s) unchecked)`), or when
  the pack has no questions. A timed-out or partial-coverage run **never**
  records complete evidence; it does not certify.
- Exit **3** is a clean non-certifying review result: targeted and
  `--no-certify` runs may return it, but neither writes a stamp.
- Exit **1** on operational error (pack unreadable, or a required critic CLI is
  missing when its pass is requested).

Hybrid flags: `--strict`, `--no-certify`, `--only`, `--ds-model`, `--variant` (default `max`),
`--verifier-profile` (default `codex-terra-high`), `--batch-size N`, `--timeout S`,
and `--jobs N`.

Layer C runs its independent batches **concurrently** (`--jobs`, default 6) via a
thread pool — the critic only compares questions *within* a batch, so batches
parallelize cleanly for a near-linear speedup (a full 162-question verify drops
from ~19 min serial to ~3 min at `--jobs 6`). Results are aggregated in
batch-index order, so findings/errors/coverage gaps are identical to a serial
run; use `--jobs 1` to force serial, or lower it if you hit API rate limits.

### Certification campaign

`scripts/certification_campaign.py` is evidence and batching orchestration, not
a certification authority. It freezes a snapshot over the question content,
waivers, grounding/source fingerprint, and critic/profile contract, then records
DeepSeek advisory evidence and one complete high-capability-verifier census from
`hybrid_verify.py --no-certify --json --campaign-snapshot sha256:<frozen-snapshot>`.
Repository-pack JSON evidence defaults under `.logs/hybrid_verify/`; an explicit
`--evidence-output` inside `question-packs/` is rejected. A malformed or incomplete high-verifier
report blocks the campaign; DeepSeek operational or incomplete evidence remains
advisory. An operational failure may retry only on the unchanged snapshot; any
content, waiver, grounding, or critic contract change creates a new campaign.
Batch the discovery findings, declare the exact changed IDs, and use
`hybrid_verify.py --only <edited-ids>` for targeted confirmation with bounded
duplicate-neighborhood context. Once those rechecks are clean, invoke
`hybrid_verify.py --certify-campaign <ledger>`. This deterministic route checks
the frozen evidence and Layer-A structure and makes no fresh reviewer/LLM call.
New concerns defer to the next campaign.

The internal `verify_pack` primitive is **not** wired into the per-edit hook or the per-launch build:
Layer C is a slow, costly, non-deterministic LLM pass, so it is a deliberate,
on-demand step run once before a pack ships — Layer A alone covers the
per-edit/per-launch path.

### Certification stamp (INV-7)

`hybrid_verify.py --certify-campaign <ledger>` atomically writes a top-level
**`certification`** block onto the pack JSON (via `verify_pack._write_certification`)
after deterministic snapshot, evidence, and Layer-A checks. It makes no fresh
reviewer/LLM call. That block is the install/ship contract — not decorative
metadata.

**Fields stamped:**

| Field | Role |
|-------|------|
| `certified` | Must be `true` |
| `questions_hash` | Canonical SHA-256 over question content (`pack_cert.questions_hash`) |
| `hash_schema_version` | Which projection rules produced `questions_hash` (currently `2026-07-20`) |
| `critic_contract_version` | Which Layer-C critic contract was in force at certify time (currently `2026-08-11`) |
| `verified_at` | ISO-8601 UTC timestamp of the stamp |
| `critic_provider` | Approved verifier provider selected by the hybrid profile |
| `critic_model` | Model identity attested by the provider; `unknown` when it does not report one |
| `critic_model_requested` | Model requested by the approved verifier profile |
| `critic_reasoning_effort` | Reasoning effort requested by the approved verifier profile, when supported |
| `review_method` | Approved external review method written by the designated hybrid verifier |
| `blocking_count` | Layer-C blocking findings at certify time (must be `0`) |
| `questions_examined` | Layer-C coverage count (must equal pack question count) |
| `question_stamps` | Per-qid registry `{qid: sha256:…}` — one content hash per question, same projection as `questions_hash` (INV-7 B.1; required for a fresh certification) |

### Who may write a certification

`review_method` is the field the install gate reads to know **how** a pack was
reviewed. It is only worth reading if it is not mintable by anything the caller
happens to point Layer C at, so exactly one supported operator pipeline writes
one:

| `review_method` | Written by | Meaning |
|---|---|---|
| `external-layer-c-strict` | Configured high-capability verifier inside `hybrid_verify.py` | The project's designated external critic reviewed the pack; DeepSeek bulk review is advisory evidence only |

Everything else **reviews without certifying**. Use `factcheck_pack.py` or
`critic_panel.py` for non-certifying authoring-time review. The direct
`verify_pack.py` shell command, including its `--panel` route, is retired and
fails fast with guidance to `hybrid_verify.py`.

### No local self-certification

There is **no** fallback for "external reviewer capacity is unavailable". The
former `scripts/certify_codex_review.py` / `codex-local-semantic-review` path was
deleted 2026-08-07: it let the session that authored a pack certify its own work,
emitting a hardcoded `human_spotcheck: "waived-by-David-explicit-cutover-request"`
whenever a CLI flag was passed — recording that a flag was set, not that a human
consented. That is how `sy0-701` shipped 115 criticals behind a clean gate
report. `--no-strict` is preview-only and is not a cutover mechanism either.

**Freshness (`pack_cert.certification_fresh`):** a pack is certification-fresh when
`certified` is true, both version axes match the current module constants, and
`questions_hash` equals a fresh recompute from the live question content (including
`source_directive` when present). Any edit to hashed fields — prompts, options,
answers, matching pairs, `source_directive`, etc. — invalidates the stamp until
the full gate is re-run.

**Per-question stamps + coverage rule (INV-7 B.1).** A fresh cert carries
a **`question_stamps`** registry: one `pack_cert.question_content_hash` per qid,
computed from the *same* `RELEVANT_FIELDS` + `source_directive` projection as the
aggregate, so a per-qid stamp and the aggregate agree on what "content" means. When
`question_stamps` is required, and `certification_fresh` requires that
**every** question have a matching fresh per-qid stamp
(`pack_cert.question_stamps_fresh`) — the aggregate is fresh *only* when the whole
pack is covered qid-by-qid. This is what lets a single edited question be
re-certified cheaply (below) while still proving the rest is unchanged, and it
closes the internal verifier's subset-and-deploy bypass: a subset run that recomputes
the whole-pack `questions_hash` cannot forge a fresh aggregate while some *other*
qid was edited but not re-graded — that qid's carried stamp won't match, and the
pack is left uncertified.

**Legacy stamps.** A certification with no `question_stamps` registry is stale.
The aggregate alone cannot prove that a later targeted process refreshed only a
subset while carrying another question's stale review forward. Re-certify legacy
packs through an evidence-final campaign to produce the required registry.

**Two-axis version bump = hard re-cert:** changing **either**
`hash_schema_version` or `critic_contract_version` in `scripts/pack_cert.py`
invalidates every existing stamp, even when question text is unchanged. Treat a
bump as a fleet-wide re-cert event: start a new evidence-final campaign for each
installed pack.

**What does NOT write or refresh a cert:** any `hybrid_verify.py --no-certify`
discovery run, any NOT READY run (exit 2), any direct `verify_pack` invocation,
or a stamp write failure (exit 1).
Structure-only and `--only <subset>` runs may leave a prior cert in place but
never replace it. Subset re-certification is unavailable; after batched edits,
use the campaign's deterministic `--certify-campaign <ledger>` route.

Enforcement boundaries:

- **`hybrid_verify.py --certify-campaign <ledger>`** — the sole operator command
  that creates a certification from completed frozen evidence. It makes no new
  LLM call; reviewer-running modes only create evidence. Its internal verifier
  primitive is not a shell route.
- **`scripts/hooks/pre-commit`** — rejects staged installed packs whose cert is
  missing or stale (fast, no LLM).
- **Strict install path** — `npm test`, pre-push, and default
  `build_manifest` / `./start.sh` (see *Authoring-time gate* and README); local WIP
  preview may use `QUIZZLER_LINT_STRICT=0` / `--no-strict` but that bypass must
  never appear in ship or CI paths.

### The severity gate — why the bar is "no errors", not "zero findings"

Layer C is a **probabilistic** LLM critic: it surfaces a *different* set of
findings each run, and its low/medium-confidence tail (nits, `ambiguous` hedges,
off-axis-distractor gripes) shifts question-to-question. Gating exit-0 on *zero
live findings* therefore never converges — fix ten, the next run finds ten new
ones elsewhere. (Not hypothetical: one 105-question pack was re-run **7×** doing
exactly that; a severity gate would have certified it at run 4.)

So the gate blocks only on **blocking** findings and reports the rest as
**advisory**:

> **A finding is BLOCKING iff it is a `wrong-answer`, a high-confidence factual
> `misleading-explanation`, or an `ambiguous` finding with structured evidence
> naming at least two defensible option indices** (`factcheck_pack.is_blocking`).
> Nit, duplicate/repetition, option-quality, off-axis, and cue findings remain
> advisory even at high confidence.

The critic's `severity`/`confidence` labels are normalized **fail-safe**: an
unrecognized/garbled label coerces to the *most* severe (blocking), never the
least, so a mislabeled real error fails the gate rather than slipping through.

The critic also emits a stable semantic `category`. `wrong-answer` blocks at
any confidence; factual `misleading-explanation` blocks only at high
confidence. `nit`, duplicate/repetition, option-quality, off-axis, and cue
categories are advisory even when high-confidence. `ambiguous` blocks only
when `ambiguity_evidence` explicitly contains
`multiple_defensible_answers: true` and at least two distinct 0-based
`option_indices`; an ambiguity complaint without that structure is classified
as option-quality advisory. `--strict` remains the diagnostic override that
treats every live finding as blocking.

**What discovery exit 0 guarantees — and does not.** `PACK READY` is a
non-certifying discovery result: it means no Layer-A defect,
no wrong-answer, and nothing the critic was *highly confident* was wrong, over a
fully-covered run. It does **not** write a certification stamp or prove the pack
is factually flawless — a
genuine explanation error the critic rated `medium` ships as advisory. And because
the critic's confidence is itself probabilistic, "blocking-clean" is a *first
green run*, not a reproducible fixed point (a finding cleared on five runs can
resurface as high-confidence on the sixth). `--strict` remains an optional
diagnostic pass; it is not the final certification gate.

### `--only` — shrinking confirmation runs (INV-7 B.1)

After frozen-snapshot discovery and batched remediation, re-verify the edited
questions with `hybrid_verify.py <pack> --no-certify --only c14q5,c10q6`. Each confirmation
run uses bounded duplicate-neighborhood context rather than a full-pack prompt.

**Bounded context.** A `--only` run grades only the named ids and supplies the
deterministically selected duplicate-neighborhood as context. It is a cheaper
confirmation check, not evidence of pack-wide semantic duplicate coverage; the
frozen full census and deterministic campaign stamp remain required.

**When `--only` certifies.** It does not: the hybrid operator forwards `--only` to
both reviewers, but a subset result is evidence only. After every changed ID is
clean, `--certify-campaign <ledger>` may write the stamp without another reviewer
pass.

### `source_directive` — grade against the course text (front-line FP defense)

The largest false-positive class is a general-purpose critic flagging a course
textbook's faithful-but-idiosyncratic framing as an "error". A pack may carry a
top-level **`source_directive`** string naming its source (e.g. "follows Ciampa
8e; treat its exception/exemption split and Table 15-4 threat taxonomy as
correct"); it is injected into the critic prompt so the critic grades against the
course, not generic CompTIA/CISSP/RFC convention. Prevented at the source beats
waived after the fact.

**Trust model — read this.** `source_directive`, `factcheck_waivers`, and
`--only` all assume author **good faith**. A broad directive ("treat X as
correct") can *launder* a genuinely wrong claim; a blanket `{qid, reason}` waiver
suppresses every finding on that question; a shrinking `--only` loop can dodge
cross-question checks. The gate is a quality tool, **not a tamper boundary**. Two
mitigations: (1) the report/JSON surfaces `source_directive active` and the waiver
count so a reviewer sees what was told-to-accept, not just the residue; (2)
**`--strict` ignores the `source_directive`** (re-grades against generic
Security+) and blocks on every finding, so it remains available for diagnosis.

The persisted `certification` stamp is likewise **edit-detecting, not
anti-forgery**: `questions_hash` (and each per-qid `question_stamps` entry) is a
pure function of pack content, so a determined author can hand-write a matching
block without running Layer C. That is intentional for a solo local study tool —
the stamp's job is to catch *accidental* drift ("edited but not re-verified"), not
to prove an LLM run occurred. The B.1 per-qid coverage rule tightens exactly that
accidental class — it closes the `--only <subset> && deploy` path that could
otherwise ship an edited-but-unaudited question under a recomputed aggregate — but
it is **not** a defense against deliberate hand-forging of the stamp block.

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

- **Not in the commit hook.** An LLM pass is slow and costs money (~$0.10+/call),
  so it is a deliberate, on-demand authoring step — run it before a new or
  substantially changed pack is "done" — not part of the fast staged-file gate.
- **Probabilistic.** The critic can be wrong in both directions (false positives and
  misses). Its output is a review aid, not a verdict: verify each finding against a
  source before editing, and spot-check exam-critical content yourself.
- **Layers compose.** Layer A guarantees *well-formed*; Layer C raises confidence in
  *correct*. Neither replaces a human read of content that a student will be graded on.
- **Run it via the campaign.** `scripts/hybrid_verify.py --no-certify` runs
  DeepSeek then the configured high-capability verifier as discovery evidence;
  only `--certify-campaign <ledger>` writes certification (see *Pack-readiness
  gate* above). A genuine critic false-positive is dismissed
  with a `factcheck_waivers` entry, not by editing a correct question.

## Manual QA Checklist

Before shipping a round, ask:

1. Does any image give away the answer?
2. Is any question visually sloppy?
3. Are there too many repeats from the last round?
4. Are some questions better as plain text?
5. Does the pack include enough breadth for the learner’s score level?

If any answer is yes, revise before release.
