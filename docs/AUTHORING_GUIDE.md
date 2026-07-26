# Authoring Guide

## Purpose

Define how to write good questions by hand so the project does not depend entirely on generation quality.

## Core Rules

1. Test one real idea at a time.
2. Use the simplest question type that fits.
3. Do not let the diagram explain the answer.
4. Explanations should teach the concept, not just restate the answer.
4a. Abbreviations and acronyms are fine in question text and answer choices, but explanations must spell out **every** acronym on first use (e.g., "DNS (Domain Name System)") so learners can connect the shorthand to the full concept. Expand **all** acronyms — not only obscure ones — in every explanation; this is a learning requirement, backed by the advisory `detect_unexpanded_acronyms` sweep (lint rule L24).
5. Keep distractors plausible but wrong.

## Writing Good Prompts

Good prompts:

- are specific
- are short enough to scan
- test an actual concept boundary

Bad prompts:

- are vague
- require guessing what the author meant
- depend on hidden assumptions

## Writing Good Distractors

Good distractors:

- are in the same conceptual neighborhood
- reflect common mistakes
- are clearly wrong once the concept is understood

Bad distractors:

- are nonsense
- are unrelated to the topic
- create accidental second correct answers

## Using Visuals Well

Use visuals when:

- notation itself is being tested
- a relationship shape matters
- the learner must read a model or diagram
- the topic is inherently visual (e.g., candlestick charts, head-and-shoulders patterns, network diagrams, flowcharts) — if the question asks about something you would normally explain with a picture, include the picture

Do not use visuals when:

- the concept is definitional
- the image adds no new information
- the image must include so much text that it becomes the answer

If the question references a diagram, chart, or visual pattern by name, the diagram field should not be null. A question about reading a candlestick chart should show a candlestick chart. A question about a Head and Shoulders pattern should show one.

## Matching Questions

Use matching for:

- breadth review
- quick concept reinforcement
- high-performance rounds

Keep items:

- short
- unambiguous
- clearly pairable
- from one coherent subject area unless the whole set is deliberately mixed and balanced

Avoid right-side choices that are so similar they make the item harder for wording reasons instead of concept reasons. If two choices would both look correct to a learner who understands the topic, rewrite the set unless that fine distinction is the exact skill being tested.

Randomize the visible right-side order when rendering matching questions. Do not leave the answers in the same obvious sequence unless the sequence itself is the thing being tested.

## Difficulty Guidelines

### Easy

- direct recall
- one concept
- simple wording

### Medium

- requires distinction between similar ideas
- may use a short scenario
- plausible distractors

### Hard

- requires applying a concept
- may combine two nearby ideas
- may use edge-case or less-common material that is still in scope

## Round-to-Round Rules

- avoid repeating the same pattern too often
- do not stack near-duplicate questions back to back
- vary question types
- if scores are high, widen coverage before drilling the same area again

## Self-Review Before Commit

### Before You Author — Build the Coverage Blueprint

- [ ] **Coverage blueprint first** — before writing questions, derive the pack's
  topic universe from the source of truth (syllabus, exam objectives, chapter
  list) and record it as a top-level **`coverage_blueprint`** array (see
  `docs/QUESTION_SCHEMA.md`). Give each required topic a `min` question count.
  Authoring against the blueprint (not the other way round) is how you get full
  topic coverage instead of an accidental topic mix.

### Cover-Test Checklist (30 Seconds Per Question)

Run through this before committing:

- [ ] **Cover test** — mentally cover left labels in matching questions; can you still pair right column from context alone? If yes, tokens are leaking.
- [ ] **Parallel construction** — all distractors within ±20% length of each other, same grammatical shape, same specificity level.
- [ ] **No example smuggling** — examples that uniquely identify the correct option belong in `explanation`, never in option text itself.
- [ ] **Plausibility floor** — every distractor must be wrong for a reason a beginner would believe, not nonsense.
- [ ] **Stem reuse** — search the pack for similar prompts before authoring; if Jaccard ≥0.5, rewrite or merge questions.
- [ ] **Blueprint completeness (L23)** — every `coverage_blueprint` topic has **≥ its `min`** questions; no single topic exceeds ~15% of the pack; topic slugs are consistent (no `shared-responsibility` vs `shared-responsibility-model` fragmentation). Run `python3 scripts/lint_packs.py <pack>.json` — a blueprint topic short of its `min` is a **CRITICAL**.

The automated check `python3 scripts/lint_packs.py --all` backstops this and runs at precommit (see "Tier 7" in `docs/VALIDATION_RULES.md`).

## Final Author Check

Before adding a question, ask:

- why is this question worth asking?
- is this the best type for it?
- does anything leak the answer?
- does the explanation actually help after a wrong answer?
- is this repeating something already asked in this same pack?

## Authoring at Scale (Parallel Agents)

For a large pack (> ~40 questions), author it in parallel **cluster-agents** rather than one pass — it is faster and avoids the model's per-message output-token ceiling, which can crash an agent mid-write and lose all its work. Partition the `coverage_blueprint` into ~20–30-question slices (one agent per slice, each with a unique question-`id` prefix, each self-linted to zero findings), then **merge and run the full gate**: L23 coverage completeness across the whole blueprint, plus L9 near-duplicate-stem detection across clusters (independent agents drift into similar phrasing, especially `multiple_select` boilerplate). The full workflow, the output-safety rules, and the merge steps are in `question-packs/AUTHORING.md` → "Authoring Large Packs with Parallel Agents".
