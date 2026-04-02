# Question Pack Schema

## Purpose

Define the stable data contract for quiz packs, generated packs, and manually authored content.

This schema is intentionally practical:

- strict enough for validation
- flexible enough for multiple question types
- compatible with static HTML today
- compatible with a helper or backend later

## Top-Level Pack Schema

```json
{
  "pack_id": "itd256-round-4",
  "subject": "ITD 256",
  "title": "Round 4",
  "version": 1,
  "generated_at": "2026-03-23T16:00:00-04:00",
  "generation_mode": "manual|templated|llm|hybrid",
  "source_rounds": ["round-2", "round-3"],
  "notes": "Optional pack-level note",
  "questions": []
}
```

## Required Top-Level Fields

- `pack_id`
- `subject`
- `title`
- `version`
- `questions`

## Question Base Schema

Every question must include:

```json
{
  "id": "q1",
  "type": "multiple_choice",
  "topic": "referential-integrity",
  "difficulty": "easy|medium|hard",
  "prompt": "Question text",
  "explanation": "Why the correct answer is correct",
  "tags": ["integrity", "chapter-3"]
}
```

## Optional Shared Fields

These fields may appear on any question type when useful:

```json
{
  "subtopic": "fk-rule",
  "chapter": "3",
  "diagram": "<svg>...</svg>",
  "diagram_alt": "Short text description of the visual",
  "source": "manual|templated|llm",
  "recent_pattern_key": "crowfoot-1m-basic",
  "author_note": "Optional internal note"
}
```

## Multiple Choice Schema

```json
{
  "id": "q1",
  "type": "multiple_choice",
  "topic": "referential-integrity",
  "difficulty": "medium",
  "prompt": "Which statement defines referential integrity?",
  "options": [
    "A primary key can never be NULL",
    "A foreign key must match an existing parent primary key or be NULL",
    "Every key must be numeric",
    "Every table must be in 3NF"
  ],
  "answer": 1,
  "explanation": "Referential integrity defines valid foreign key values."
}
```

Rules:

- `options` must contain at least 2 choices
- `answer` must be a valid zero-based index
- exactly one correct answer is allowed

## Matching Schema

```json
{
  "id": "q2",
  "type": "matching",
  "topic": "normal-forms",
  "difficulty": "medium",
  "prompt": "Match each normal form to what it removes.",
  "leftItems": [
    "1NF",
    "2NF",
    "3NF",
    "4NF"
  ],
  "rightItems": [
    "Repeating groups",
    "Partial dependencies",
    "Transitive dependencies",
    "Multivalued dependencies"
  ],
  "correctPairs": [0, 1, 2, 3],
  "explanation": "Each normal form removes a specific class of structural problem."
}
```

Rules:

- every `left_item` must map to exactly one `right_item`
- `correctPairs` is a flat array where `correctPairs[i]` = the index in `rightItems` that matches `leftItems[i]`
- `rightItems` must contain only unique values — when multiple left items share the same right answer, reuse the same index in `correctPairs` (e.g., `[0, 1, 0, 1]`) instead of duplicating the right-side entry
- `leftItems` and `rightItems` do NOT need to be the same length — fewer right items than left items is normal when categories are shared
- matching may be rendered as click-to-pair before drag-and-drop exists
- the displayed right-side choices should be randomized so the set is not always shown in the same order
- randomization should not change the stored `right_items` or `correct_pairs` mapping

## True/False Schema

```json
{
  "id": "q3",
  "type": "true_false",
  "topic": "erm",
  "difficulty": "easy",
  "prompt": "The ER model depends on the type of DBMS being used.",
  "answer": false,
  "explanation": "The ER model is database-independent."
}
```

Rules:

- `answer` must be a boolean
- the renderer should treat the visible choices as `True` and `False`
- `options` is not required for this type
- the explanation should reference the truth value directly, not an option index

## Scenario Schema

```json
{
  "id": "q4",
  "type": "scenario_multiple_choice",
  "topic": "4nf",
  "difficulty": "hard",
  "prompt": "A professor can have many skills and many languages, and the two lists are independent. What normal form issue is most likely present?",
  "options": [
    "2NF",
    "3NF",
    "4NF",
    "DKNF only"
  ],
  "answer": 2,
  "explanation": "Independent multivalued facts in one table indicate a 4NF issue."
}
```

## Result Report Compatibility

Each question should be compatible with result tracking fields such as:

```json
{
  "question_id": "q1",
  "correct": true,
  "selected_option": 1,
  "response_ms": 4200
}
```

For matching questions:

```json
{
  "question_id": "q2",
  "correct": true,
  "selected_pairs": [[0, 0], [1, 1], [2, 2], [3, 3]],
  "response_ms": 9100
}
```

## Required Validation Rules

Any valid pack must satisfy:

1. every question has a unique `id`
2. every question has a supported `type`
3. every question has `topic`, `difficulty`, `prompt`, and `explanation`
4. every question passes type-specific validation
5. no malformed diagram payload if `diagram` is present
6. no empty options or empty matching sides

## Supported Types for Version 1

- `multiple_choice`
- `matching`
- `true_false`
- `scenario_multiple_choice`

More types can be added later, but these should be enough to move the engine forward cleanly.
