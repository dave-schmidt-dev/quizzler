# Creating New Question Packs

## Quick Start

1. Copy `pack-template.json` into the course folder (e.g., `itd256/round-8.json` or `bcccce/round-1.json`)
2. Fill in questions following the schema below
3. Register the pack in `app/index.html` under the `COURSES` array
4. Open the app and test

## Adding a New Course

In `app/index.html`, add an entry to the `COURSES` array:

```javascript
{
  id: "bcccce",
  name: "BCCCCE",
  description: "Your course description",
  modules: [
    { file: "round-1.json", title: "Round 1", description: "First pack" }
  ]
}
```

Then create the folder: `question-packs/bcccce/` and add your JSON pack files.

## Pack Schema

```json
{
  "pack_id": "course-round-N",     // unique ID
  "subject": "Course Name",         // display name
  "title": "Round N",               // short title
  "version": 1,                     // increment when editing
  "generated_at": "ISO-8601",       // when created
  "generation_mode": "manual|llm|hybrid",
  "notes": "Optional focus description",
  "questions": [ ... ]
}
```

## Question Types

### multiple_choice
- `options`: array of 3-4 strings
- `answer`: zero-based index of correct option
- Best for: direct recall, single-concept distinction

### true_false
- `answer`: `true` or `false` (boolean, NOT an index)
- Do NOT include an `options` array
- Best for: textbook traps, misconceptions, quick checks

### matching
- `leftItems`: array of terms
- `rightItems`: array of **unique** definitions (may be shorter than leftItems when categories are shared)
- `correctPairs`: array where `correctPairs[i]` = index in rightItems that matches leftItems[i] — reuse the same index when multiple left items share a right answer (e.g., `[0, 1, 0, 1]`)
- Never duplicate a value in `rightItems` — if two left items map to "CEX", list "CEX" once and point both pairs to its index
- Right-side is auto-shuffled at render time
- Best for: term/definition pairs, category sorting, breadth review

### scenario_multiple_choice
- Same structure as multiple_choice
- Best for: applied concepts, normalization problems, key selection

## All Questions Must Have

- `id`: unique within the pack (e.g., "r8q1")
- `type`: one of the four types above
- `topic`: kebab-case topic slug (e.g., "partial-dependency")
- `difficulty`: "easy", "medium", or "hard"
- `prompt`: the question text
- `explanation`: teaches the concept, not just "the answer is X"
- `diagram`: SVG string or `null` (optional)

## Quality Rules

1. Test one real idea per question
2. Use the simplest question type that fits
3. Do not let diagrams reveal the answer
4. Explanations should teach, not just restate
5. Abbreviations/acronyms in explanations must be spelled out on first use (e.g., "Proof of Work (PoW)")
6. Keep distractors plausible but clearly wrong
7. No duplicate prompts within a pack or across recent packs
8. Matching sets must be coherent (no obvious outliers)
9. Randomization is handled by the engine — store answers in canonical order
10. If the topic is inherently visual (charts, patterns, diagrams), the question must include a diagram

## Feeding Packs via Claude

When asking Claude to generate a new pack, provide:
1. The course name and topic areas to cover
2. Weak topics from session history (visible on the history screen or in localStorage under `quizEngine_sessions`)
3. A reference to this schema

Example prompt:
> Generate a 20-question pack for BCCCCE covering [topics]. Use the schema from pack-template.json.
> Here are my weak areas from the last session: [paste session JSON].
> Mix question types: ~60% multiple choice, ~20% matching, ~10% true/false, ~10% scenario.
