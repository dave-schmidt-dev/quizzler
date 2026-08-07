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
  "pack_id": "samples-round-4",
  "subject": "Sample Course",
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

## Top-Level Metadata Fields


- `coverage_blueprint` — **required for installed packs** (manifest-visible under
  `question-packs/<course>/`). The pack's intended **topic universe**, enforced by
  the Layer-A rule **L23 — Coverage Completeness** (see `docs/VALIDATION_RULES.md`).
  Omitting it is a CRITICAL (INV-7; formerly advisory before 2026-07-20). An array
  whose entries accept two shapes:
  - object: `{"topic": "<slug>", "min": <int, default 1>}`
  - bare string `"<slug>"` — shorthand for `{"topic": "<slug>", "min": 1}`

  ```json
  "coverage_blueprint": [
    {"topic": "rds-multi-az", "min": 2},
    {"topic": "cloudformation", "min": 2},
    "sqs-vs-sns"
  ]
  ```

  **Semantics:** every declared `topic` must be carried by at least `min`
  questions (matched by exact slug equality after case-insensitive strip), or L23
  fails the gate with a CRITICAL. L23's over-concentration and near-duplicate-slug
  WARNINGs apply whether or not a blueprint is declared (but the blueprint itself
  is mandatory for installed packs).
- `certification` — normally written by **`verify_pack` on a full-gate exit 0**
  (INV-7). An explicitly authorized private local cutover may instead use
  `scripts/certify_codex_review.py`, which records its distinct review method in
  `codex_review`. The normal form proves the pack passed Layer A + Layer C with
  full coverage at certify time; the Codex fallback proves fresh hashes and
  bounded local review metadata, not external Layer-C review.
  Freshness is checked by `pack_cert.certification_fresh()` (pre-commit hook,
  install gate). Bumping `hash_schema_version` or `critic_contract_version` in
  `scripts/pack_cert.py` invalidates all existing stamps — re-run `verify_pack`
  on every installed pack.

  ```json
  "certification": {
    "certified": true,
    "hash_schema_version": "2026-07-20",
    "critic_contract_version": "2026-07-20",
    "verified_at": "2026-07-20T18:30:00+00:00",
    "questions_hash": "sha256:…",
    "critic_model": "claude-sonnet-5",
    "blocking_count": 0,
    "questions_examined": 113
  }
  ```

  Any edit to hashed question content or `source_directive` invalidates the stamp
  until the full gate is re-run. `--no-factcheck` and `--only` never write or
  refresh this block.
- `codex_review` — present only for the explicitly authorized local fallback
  review. It records `reviewer: "codex"`,
  `review_method: "codex-local-semantic-review"`, the exact reviewed question
  IDs, the review count, blocking count, the human spot-check disposition, and
  the external-review status. `human_spotcheck` may be `"completed"` or the
  explicit `"waived-by-David-explicit-cutover-request"` value. This metadata
  must never be described as Claude/Agy or independent external certification.
  The fallback is invoked only by `scripts/certify_codex_review.py` with its
  explicit waiver flag; it does not bypass the strict manifest install gate.
- `lint_waivers` — Layer-A finding suppressions; see *Waivers* in
  `docs/VALIDATION_RULES.md`.
- `factcheck_waivers` — Layer-C finding suppressions; see the same doc.
- `source_directive` — a string naming the pack's source text, injected into the
  Layer-C critic prompt so it grades against the course; see the same doc.

## Question Base Schema

Every question must include:

```json
{
  "id": "q1",
  "type": "multiple_choice",
  "topic": "referential-integrity",
  "exam_area": "2.0",
  "difficulty": "easy|medium|hard",
  "prompt": "Question text",
  "explanation": "Why the correct answer is correct",
  "tags": ["integrity", "chapter-3"]
}
```

### `topic` vs `exam_area`

Two different grains, both required for a pack in a course directory:

| field | grain | who defines it | used for |
|---|---|---|---|
| `topic` | fine, one concept | the pack author | `coverage_blueprint` (L23), near-duplicate detection, recent-memory |
| `exam_area` | coarse, published | the exam vendor / class syllabus | per-area accuracy, targeted study |

`exam_area` must name an area id declared in the course's `_course.json` under
`syllabus.areas`. Rule **L27** enforces that reference and is **non-waivable** —
an undeclared area silently becomes a phantom objective holding a handful of
questions at 0% accuracy, which any per-area weakness ranking would surface as
the learner's largest gap. Nothing else in the toolchain would catch it.

The taxonomy is transcribed from a published source, never invented; see
`question-packs/AUTHORING.md` for the `syllabus` block and its `source.kind`
values (`exam_objectives`, `syllabus`, `none`).

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

## Multiple Select Schema

```json
{
  "id": "q5",
  "type": "multiple_select",
  "topic": "transport-layer",
  "difficulty": "medium",
  "prompt": "Which of the following operate at the transport layer? (Select all that apply.)",
  "options": [
    "TCP",
    "UDP",
    "ARP",
    "ICMP"
  ],
  "answers": [0, 1],
  "explanation": "TCP and UDP are transport-layer protocols; ARP and ICMP are not."
}
```

Rules:

- `options` must contain at least 3 choices
- `answers` is an array of zero-based indices of the correct options — the plural `answers`, distinct from the single-value `answer`
- `answers` must be non-empty, contain only distinct valid indices, and must not cover every option (at least one distractor is required)
- grading is all-or-nothing: the response is correct only when the selected set exactly equals `answers`
- use at least two correct answers — a single correct answer should be authored as `multiple_choice`
- the renderer shows a checkbox per option and grades on submit; options are shuffled at display time

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

For multiple_select questions:

```json
{
  "question_id": "q5",
  "correct": true,
  "selected_options": [0, 1],
  "response_ms": 6100
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
- `multiple_select`

More types can be added later, but these should be enough to move the engine forward cleanly.
