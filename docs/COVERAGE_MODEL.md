# Coverage Model

## Purpose

Track whether the engine is sampling enough of the subject instead of repeating a narrow slice.

## What to Track

- topic frequency across recent rounds
- question type frequency across recent rounds
- chapter coverage across recent rounds
- visual vs non-visual balance
- definition vs application balance

## What's Already Implemented

The engine now tracks per-question mastery (`quizzler_mastery_{courseId}` in `localStorage`) and uses it for **weighted question selection** at quiz start:

- **Unseen questions** (never attempted): 10x weight
- **Seen but never correct**: 5x weight
- **Mastered** (correct at least once): 1x weight

This ensures coverage naturally improves over time — unseen questions are strongly prioritized, but mastered questions still appear for reinforcement. The **Exam Readiness banner** on the Quiz Config screen shows seen/correct progress vs total questions.

## Coverage Signals

Useful signals for future adaptive generation (not yet implemented at the helper layer):

- `times_seen_recently`
- `rounds_since_last_seen`
- `question_type_recent_usage`
- `chapter_recent_usage`

## High-Performance Rule

If the learner is consistently above 90 percent:

- prioritize under-covered topics
- reduce repeated prompt families
- add more definitions and distinction questions
- add less-common but in-scope concepts

## Authoring Coverage (blueprint completeness)

The mastery-weighting above is **runtime coverage** — it governs which of a pack's
*existing* questions get sampled as the learner practices. It says nothing about
whether the pack *contains* a question on every topic the exam expects. That is
**authoring coverage**, a separate concern owned at authoring time by the Layer-A
rule **L23 — Coverage Completeness** (`docs/VALIDATION_RULES.md`).

| | Runtime coverage (this doc, above) | Authoring coverage (L23) |
|---|---|---|
| Question | Am I *sampling* enough of what's in the pack? | Does the pack *contain* a question on every required topic? |
| Mechanism | Mastery-weighted selection at quiz start | `coverage_blueprint` contract checked by the linter |
| When | Every quiz, in the browser | At authoring time / the readiness gate |

Declare the topic universe as a top-level **`coverage_blueprint`** array (object
entries `{"topic": "<slug>", "min": N}` or bare-string shorthand). L23 then fails
the gate (CRITICAL) if any declared topic has fewer than its `min` questions,
warns on over-concentration (a single topic > 15% of the pack) and on
near-duplicate topic slugs (fragmentation like `shared-responsibility` vs
`shared-responsibility-model`). A pack with no blueprint is never failed — it gets
one non-blocking advisory nudge — so the standard is opt-in per pack but, once
declared, enforced on every future edit.

## Minimum Breadth Expectation

A strong round should not be composed mostly of one narrow pattern unless it is
explicitly a focused remediation round. Encode this expectation concretely with a
**`coverage_blueprint`**: list every topic the round must cover with its minimum
question count, and L23 enforces it deterministically. The blueprint is the
machine-checkable form of "minimum breadth" — build it from the source of truth
(syllabus, exam objectives, chapter list) **before** authoring the questions, then
make every blueprint topic reach its `min`.
