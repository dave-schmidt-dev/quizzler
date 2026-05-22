# Generation Prompt Template

Use this template when the quiz engine helper asks an LLM to generate a new question pack.

This template is designed to reduce repetition, preserve schema quality, and adapt differently when the learner is struggling versus when the learner is consistently scoring above 90%.

## System Intent

Generate a new study pack for a quiz engine.

The output must be:

- strictly in scope for the subject
- non-repetitive relative to recent rounds
- schema-valid
- answer-key safe
- harder and broader when the learner is already performing well
- able to use more than one question type when appropriate

## Input Variables

Replace these placeholders before sending the prompt:

- `{{SUBJECT}}`
- `{{PACK_SIZE}}`
- `{{RECENT_SCORE_HISTORY}}`
- `{{RECENT_TOPICS}}`
- `{{WEAK_TOPICS}}`
- `{{UNDERCOVERED_TOPICS}}`
- `{{RECENT_PATTERNS_TO_AVOID}}`
- `{{MASTERY_SUMMARY}}`
- `{{QUESTION_SCHEMA}}`
- `{{EXAMPLE_QUESTIONS}}`

## Prompt Template

```text
You are generating a new quiz pack for {{SUBJECT}}.

Output only valid JSON matching this schema:
{{QUESTION_SCHEMA}}

Pack size:
{{PACK_SIZE}}

Recent score history:
{{RECENT_SCORE_HISTORY}}

Weak topics:
{{WEAK_TOPICS}}

Under-covered topics:
{{UNDERCOVERED_TOPICS}}

Topics used heavily in recent rounds:
{{RECENT_TOPICS}}

Recent prompt or diagram patterns to avoid:
{{RECENT_PATTERNS_TO_AVOID}}

Mastery summary (seen/correct counts from engine tracking):
{{MASTERY_SUMMARY}}

Reference examples of acceptable question quality:
{{EXAMPLE_QUESTIONS}}

Generation rules:
1. Stay strictly within course scope.
2. Do not repeat the same prompt pattern too often.
3. Do not leak the answer inside the diagram or prompt wording.
4. Use visuals only when a visual genuinely improves the question.
4a. However, if the topic is inherently visual (charts, diagrams, patterns, network topologies, flowcharts), the question MUST include a diagram. A question about reading a candlestick chart should show one. A question about a Head and Shoulders pattern should show one. Do not ask about visual concepts with diagram set to null.
5. Some questions may be plain text if that tests the concept more cleanly.
5a. Matching questions are allowed when they improve breadth and speed of review.
6. Every question must have exactly one correct answer.
7. Every question must include a concise explanation.
7a. Abbreviations and acronyms are fine in question text and answer choices, but explanations must spell them out on first use so learners can connect the shorthand to the full concept.
8. Avoid malformed or overcrowded diagrams.
9. Do not rely on left-to-right placement alone to imply dependency direction.
10. Keep difficulty and distractors plausible.

Adaptive policy:
- If performance is below 70 percent, focus on core remediation.
- If performance is between 70 and 89 percent, mix remediation and retention.
- If performance is consistently 90 percent or above, do not over-focus on only recent misses.

High-performance mode behavior:
- Expand into under-covered topics.
- Increase difficulty.
- Add more distinction and definition questions.
- Include some less-common but still in-scope concepts.
- Reduce repetition from recent rounds.
- Mix visual and non-visual questions.
- Include matching questions when they improve coverage and reduce repetition.

Composition guidance for high-performance mode:
- 20 percent weak-topic reinforcement
- 40 percent under-covered topics
- 20 percent harder distinction or definition questions
- 20 percent retention questions

Return only JSON.
```

## Notes

The helper should fill this template using actual quiz history, not guesses.

The `{{MASTERY_SUMMARY}}` variable should be populated from the engine's `quizzler_mastery_{courseId}` localStorage data, which tracks per-question "seen" and "correct at least once" flags. The engine already uses this for weighted selection at runtime (unseen 10x, seen-wrong 5x, mastered 1x), so the generation helper should focus on producing questions that fill remaining coverage gaps rather than duplicating the weighting logic.

The helper should also run a validation pass after generation to check:

- schema compliance
- one correct answer only
- explanation presence
- no direct answer leakage
- no near-duplicate prompts relative to recent rounds

This template is intended to be stable and versioned so the generation policy is explicit rather than improvised.
