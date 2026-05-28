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
- matching choices are not so similar that they create avoidable ambiguity unless the distinction itself is the learning objective
- matching choices are not left in the same obvious 1-2-3-4 order across packs unless the order is intentionally part of the concept

Reject if:

- a visual is used when a plain question would be clearer
- the question is trivial because of the phrasing
- the explanation is too weak to support correction
- a matching set contains obvious outliers that make the answer too easy
- a matching set uses near-duplicate choices that make the learner guess between wording variants rather than concepts
- a matching set repeatedly shows the right-side choices in the same unshuffled order
- the topic is inherently visual (charts, patterns, diagrams, topologies) but the diagram field is null
- abbreviations or acronyms appear in explanations without being spelled out on first use

## Level 5: Repetition Validation

Check:

- no duplicate prompt wording in the same pack
- no near-duplicate pattern overload relative to recent rounds
- no adjacent questions that test nearly the same thing in the same way unless deliberate contrast is intended

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
- Any question has duplicate option text after normalization (whitespace collapse, lowercase)

### L8 — Parenthetical Justification (Multiple Choice / Scenario)

Reject if the correct option’s parenthetical does not paraphrase its own pre-parenthesis label with at least 3 shared content words.

**Example pass:** `"(C) ATP (energy currency of the cell)"` — "ATP" and "energy" and "currency" or "cell" share concepts.

**Example fail:** `"(C) Mitochondria (site of glycolysis)"` — Mitochondria and glycolysis are unrelated; no paraphrase.

### L9 — Intra-Pack Near-Duplicate Stem

Pairwise Jaccard similarity on prompt tokens:
- ≥ 0.5 → WARN
- ≥ 0.7 → CRITICAL FAIL

Rewrite or merge overlapping questions.

## Manual QA Checklist

Before shipping a round, ask:

1. Does any image give away the answer?
2. Is any question visually sloppy?
3. Are there too many repeats from the last round?
4. Are some questions better as plain text?
5. Does the pack include enough breadth for the learner’s score level?

If any answer is yes, revise before release.
