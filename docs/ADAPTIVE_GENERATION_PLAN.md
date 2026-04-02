# Adaptive Question Generation Plan

## Purpose

Define a practical path for turning the current visual quiz prototype into a system that can generate the next question set based on what the learner missed, without creating a brittle or unsafe browser-to-shell design.

This document focuses on one specific problem:

- how to decide what the next questions should be
- how to generate them
- how to store them
- how to keep the system safe and explainable

## Recommendation

The best path is a **three-stage architecture**:

1. **Static/offline frontend** for taking quizzes (with localStorage persistence)
2. **Structured session data** in localStorage (already implemented)
3. **Trusted local helper** that reads session data and builds the next question pack

Do not try to make page JavaScript directly run local commands. That is the wrong boundary.

## Why This Path Is Best

It balances:

- low cost
- local control
- offline-first study use
- future LLM integration
- security
- maintainability

It also lets the project stay useful even when the helper layer is not running.

## Current State

The engine already has substantial adaptive behavior built in:

- question display
- answer capture
- immediate feedback
- missed-topic tracking
- session data persisted to localStorage (last 200 sessions)
- a richer quiz set through round 7, including matching questions and broader database-model coverage
- **mastery tracking** — per-question "seen" and "correct at least once" flags persisted across all sessions in `localStorage`
- **weighted question selection** — unseen questions get 10x weight, seen-but-never-correct get 5x, mastered get 1x, so the engine naturally prioritizes coverage gaps while still mixing in familiar questions for reinforcement
- **Readiness score** — composite formula (`coverage × 0.3 + mastery × 0.3 + recentAccuracy × 0.4`) with qualitative labels, displayed prominently on the Quiz Config screen alongside progress bars

The weighted selection means the engine already adapts at the question-pool level without needing a helper script. The next step for the adaptive system is the **helper layer** for generating new question packs based on weakness signals.

## Question Quality Rules

Adaptive generation should not optimize only for topic coverage. It also needs layout and pedagogy rules.

Generation and validation should reject questions that:

- leak the answer through labels inside the diagram
- repeat the same pattern too often across adjacent questions or rounds
- force a visual when text would test the idea more cleanly
- rely on position alone instead of explicit notation
- overflow text outside the visual frame
- include so much annotation that the image becomes the explanation

Round generation should actively mix:

- visual recognition questions
- non-visual definition questions
- short scenario questions
- relationship notation questions

## Audit Best Practice

Before generating a new round, run a lightweight audit against:

- recent quiz rounds
- prep notes
- chapter summaries
- quiz explanation files
- any prior audit findings that were recorded for the engine

The audit should identify:

- under-covered topics
- overused topics
- repeated prompt families
- overused visual patterns
- same-pack rephrasings that make the quiz feel repetitive

When possible, do this in a separate subagent or sidecar analysis pass so the main generation thread stays clean and focused.

The output of that audit should directly influence:

- topic selection
- question type mix
- breadth expansion
- pattern avoidance rules

The concrete schema and question-type references for this are:

- `QUESTION_SCHEMA.md`
- `QUESTION_TYPES.md`
- `VALIDATION_RULES.md`

## Proposed System Design

### Layer 1: Quiz Frontend

Responsibilities:

- render a quiz pack
- capture answer events
- record correctness and timing
- compute topic-level weakness signals
- persist structured session results to localStorage
- track per-question mastery (seen / correct at least once)
- apply weighted question selection based on mastery state

The frontend should stay intentionally dumb:

- no direct shell access
- no direct Codex execution
- no secret handling

### Layer 2: Session Result Format

The frontend persists structured session JSON to `localStorage` under the key `quizEngine_sessions`. Mastery tracking is stored under `quizEngine_mastery_{courseId}`.

Session fields:

```json
{
  "quiz_id": "itd256-midterm-round2",
  "completed_at": "2026-03-23T20:15:00-04:00",
  "score": {
    "correct": 16,
    "total": 20
  },
  "answers": [
    {
      "question_id": "r2q11",
      "topic": "partial-dependency",
      "difficulty": "medium",
      "correct": false,
      "selected_option": 2,
      "correct_option": 0,
      "response_ms": 9400
    }
  ],
  "topic_summary": [
    {
      "topic": "partial-dependency",
      "correct": 0,
      "total": 2
    }
  ]
}
```

This is the minimum useful handoff boundary. The helper can read this directly from localStorage via a headless browser or by extracting the data file.

### Layer 3: Local Helper

The helper is the trusted execution layer.

Responsibilities:

- read session data from localStorage
- merge it with prior history
- compute learner weaknesses
- choose a generation strategy
- produce a new question pack
- optionally write a Markdown report

The helper can be:

- a Python CLI
- a small local HTTP service
- a terminal script invoked manually

For this project, the cleanest first version is a **Python CLI**.

## Best First Adaptive Strategy

Do not start with fully freeform LLM generation. Start with **hybrid adaptive generation**.

### Hybrid adaptive generation

Use three sources of new questions:

1. **Existing question bank**
2. **Templated question generation**
3. **LLM-generated expansion**

This should happen in that order.

### Why hybrid is better than pure LLM generation

Pure LLM generation has risks:

- inconsistent difficulty
- incorrect answer keys
- vague distractors
- duplicated concepts
- unstable quality

Hybrid generation gives you:

- predictable core questions from known-good content
- templated variations for repeated drilling
- LLM help only where it adds value

## Recommended Generation Pipeline

### Step 1: Collect weakness signals

For each topic, compute a weakness score from:

- wrong answers
- repeated wrong answers across sessions
- slow responses
- low confidence, if confidence is later added
- time since last successful review

### Step 2: Select target topics

Rank topics by weakness score.

Example:

- `partial-dependency`
- `referential-integrity`
- `cardinality-constraint`

### Step 3: Build the next pack with quotas

Use a fixed composition rule so every pack has structure.

Example 20-question pack:

- 8 questions from weakest 2 topics
- 6 questions from medium-weak topics
- 4 retention questions from previously learned topics
- 2 stretch questions from adjacent or new topics

This prevents overfitting to only one weak area.

If the learner is consistently scoring `90%+`, the quotas should shift away from narrow remediation and toward breadth expansion:

- more under-covered subject areas
- more definitions and distinction questions
- more difficult distractors
- fewer repeats of the same visual or scenario pattern
- more matching questions when they can cover multiple concepts efficiently

### High-performance mode

If the learner is consistently scoring at or above `90%`, the generator should not keep narrowing further into the same topic cluster.

Instead, it should switch strategies:

- expand into under-covered subject areas
- reduce repetition from recently seen prompts
- increase question difficulty
- add more definition and distinction questions
- include edge-case or less-common topics such as higher normal forms

Recommended policy:

- `< 70%`: focus on core remediation
- `70% to 89%`: mixed remediation plus retention
- `90%+`: breadth expansion plus harder questions

For `90%+` rounds, a better 20-question composition is:

- 4 questions on weak areas that still matter
- 8 questions on under-covered topics
- 4 harder distinction/definition questions
- 4 retention questions to make sure earlier strengths still hold

This helps prevent a false sense of mastery caused by a narrow or repetitive question base.

### Step 4: Fill each quota from the safest source first

Order:

1. exact bank reuse
2. templated bank variation
3. LLM-generated new item

Only use LLM generation when the bank cannot fill the quota well enough.

When the learner is in high-performance mode, the LLM prompt should explicitly request:

- topics not emphasized in the last 2-3 rounds
- harder distractors
- less common but still in-scope concepts
- mixed visual and non-visual questions
- at least some pure definition questions
- no near-duplicate prompt patterns from recent rounds

### Step 5: Validate the generated pack

Every generated question should pass checks before it is shown:

- exactly one correct answer
- explanation exists
- topic is assigned
- difficulty is assigned
- no duplicate prompt in current pack
- no malformed SVG/diagram payload

If LLM generation is used, add one more rule:

- generated question must be reviewed by a validation pass before acceptance

## Best Near-Term Implementation Path

### Phase A: Manual adaptive workflow

Goal: useful immediately, minimal engineering

Build:

- frontend persists sessions to localStorage (done)
- helper reads session data from localStorage
- helper outputs a new JSON question pack
- human opens the next quiz

This is the right first milestone.

### Phase B: Local templated generation

Goal: reduce dependence on manual authoring

Build topic templates like:

- relationship degree recognition
- identify the violated normal form
- choose correct PK/FK placement
- identify determinant direction
- matching sets for definitions and distinctions

Template inputs:

- entity names
- attributes
- relationship pattern
- correct concept
- distractor set

This lets the helper generate many safe variants without an LLM.

### Phase C: LLM-assisted generation

Goal: broader coverage and fresher distractors

The helper sends structured prompts to an LLM with:

- target topic
- target difficulty
- examples of valid questions
- schema rules
- banned mistakes

The LLM returns candidate questions in JSON only.

Then the helper validates them before writing them to disk.

### Phase D: Review reports

Goal: make the engine useful for longitudinal studying

After each session, the helper writes:

- `session-report.md`
- `weak-topics.json`
- optional `next-pack.json`

## Safe LLM Integration Design

If LLM generation is added, use this boundary:

1. helper reads session data and mastery from localStorage
2. helper calculates weak topics
3. helper prepares a constrained generation prompt
4. LLM returns candidate question JSON
5. helper validates schema and duplicates
6. helper writes approved pack to disk

The browser only loads the result. It does not run the generation itself.

Use `GENERATION_PROMPT_TEMPLATE.md` as the starting prompt artifact for the helper so generation policy stays explicit and versioned.

## Question Authoring Model

Use three content buckets.

### Bucket 1: Canonical questions

Hand-authored, instructor-aligned, stable.

Use for:

- exact textbook definitions
- high-confidence core concepts
- known exam traps

### Bucket 2: Templated variants

Programmatically generated from safe patterns.

Use for:

- cardinality recognition
- dependency identification
- PK/FK placement
- bridge entity drills

### Bucket 3: LLM expansions

Higher-variance content that expands practice coverage.

Use for:

- new distractor sets
- alternate diagram layouts
- mixed-topic synthesis questions

## Scoring Model for Adaptation

A simple and explainable first model:

```text
weakness_score =
  (wrong_answers * 3)
  + (slow_answers * 1)
  + (repeat_misses * 2)
  + (overdue_reviews * 2)
```

This is intentionally simple. You can inspect it and debug it.

## Breadth Score

Weakness alone is not enough. The helper should also track a simple breadth signal.

Suggested measures:

- which topics appeared in the last N rounds
- which topics have not appeared recently
- which question types have been overused
- whether recent rounds were too visually homogeneous

Then use a breadth score to push the next pack toward coverage, not just remediation.

Example:

```text
breadth_priority =
  undercovered_topics
  + time_since_last_seen
  + low_recent_question_type_usage
```

In practice:

- a topic not seen in several rounds should become more likely
- a question format overused in recent rounds should become less likely
- definition questions should re-enter the mix if recent rounds leaned too visual

## Spaced Repetition Integration

Adaptive generation and spaced repetition should be connected, but not merged into one vague system.

Use spaced repetition to answer:

- what should come back now?

Use adaptive generation to answer:

- what form should the next questions take?

### Suggested model

Each question and topic gets:

- `last_seen_at`
- `last_correct_at`
- `interval_days`
- `next_due_at`
- `stability_score`

Then the helper chooses:

- overdue topics
- weak topics
- recently missed topics

The next pack should be a blend of all three.

## What the First Real Deliverable Should Be

The best next implementation milestone after the midterm is:

### Deliverable: `generate_followup_pack.py`

Input:

- `session_result.json`
- `question_bank.json`

Output:

- `followup_pack.json`
- `session_report.md`

Rules:

- no LLM required yet
- choose questions by topic weakness
- avoid repeating the exact same question immediately unless the user requests retry mode
- keep output schema simple and stable

This gets the adaptive system working before any agent integration.

## Suggested File Layout

```text
quizzler/
  app/
    index.html
    app.js
  question-packs/
    itd256/
      canonical.json
      templates.json
  session-data/
    latest-session.json
    history.json
  reports/
    session-report.md
  scripts/
    generate_followup_pack.py
    validate_question_pack.py
```

## Hard Requirements

The adaptive system should not be considered done unless it has:

1. a stable JSON schema for results and question packs
2. deterministic helper behavior when LLM mode is off
3. validation for all generated packs
4. explainable topic selection
5. a saved report of what was generated and why

## Decision

Recommended path:

1. keep the browser as a quiz runner with localStorage persistence (done)
2. build a Python helper that reads session/mastery data for follow-up pack generation
4. add templated generation before LLM generation
5. add LLM generation only behind validation
6. add spaced repetition once history is being stored

This is the shortest path that is both useful and technically defensible.

## Prompting Rule for Advanced Learners

When the learner is consistently at `90%+`, the prompt to the helper or LLM should say something close to:

> The learner is consistently scoring above 90 percent. Do not narrow further into only recent misses. Expand into under-covered in-scope topics, increase difficulty, include definition and distinction questions, reduce repetition from recent rounds, and make sure the pack tests breadth as well as accuracy.

That rule should be baked into the generation logic, not left to improvisation.

The concrete reusable prompt artifact for this is `GENERATION_PROMPT_TEMPLATE.md`.
