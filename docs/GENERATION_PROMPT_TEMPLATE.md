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
5b. In a matching set, every right-side description must distinguish its term along ONE consistent classification axis (e.g., all by communication channel, OR all by mechanism — never a mix), and each description must capture the term's actual defining feature, not a secondary attribute. Example defect to avoid: describing Phishing/Vishing/Smishing by channel (email/voice/SMS) but Business Email Compromise by mechanism (fraudulent fund transfer), where the BEC description never mentions its defining compromised/spoofed email account. Such a set feels "off" even when every pair is technically correct.
5c. When a matching set's left items are acronyms/initialisms, the right-side description MUST NOT contain the acronym's own expansion words (e.g., MD5 → avoid "message-digest"; ECC → avoid "curve"; SRTP → avoid "real-time"; S/MIME → avoid "mail"). The literal expansion lets a learner pair by surface word-overlap with zero domain knowledge. Describe by function/property instead (MD5 → "deprecated 128-bit hash, no longer collision resistant").
6. Every question must have exactly one correct answer.
7. Every question must include a concise explanation.
7a. Abbreviations and acronyms are fine in question text and answer choices, but explanations must spell them out on first use so learners can connect the shorthand to the full concept.
7b. For every multiple_choice and scenario_multiple_choice question, the explanation must say why EACH wrong option is wrong — one short clause per distractor — not only why the correct answer is right. A learner torn between two plausible options is helped only when the distractor itself is addressed. This is enforced by linter rule L10; for a pure-recall item whose distractors share no per-option concept, a single contrast clause ("unlike X, …") satisfies it. Do not ship an explanation that names the key but ignores the alternatives.
8. Avoid malformed or overcrowded diagrams.
9. Do not rely on left-to-right placement alone to imply dependency direction.
10. Keep difficulty and distractors plausible.
11. Do not produce "All of the above", "None of the above", "Both A and B", or any position-referential option. Options are shuffled at render time, so position references break; enumerate the specific combinations as full options instead.

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

The helper MUST run a validation pass after generation. The deterministic
Layer-A gate is:

```
python3 scripts/lint_packs.py path/to/new-pack.json
```

It covers (rules L1–L13):

- schema compliance (L7) and unique question ids (L13)
- exactly one correct answer (L7) and explanation presence (L12)
- **distractor coverage — every wrong option addressed (L10)** (see rule 7b)
- answer-leak tells: stem echo (L2), length tell (L3), parenthetical self-paraphrase (L8), matching token leak (L1)
- near-duplicate prompts within the pack (L9)

A pack is not "done" until that command reports **0 critical and 0 warning**
(exit 0). The authoring hook (`scripts/lint_hook.py`, wired in
`.claude/settings.json`) runs the same check automatically the moment a pack
file is written or edited, so any finding must be fixed before the pack is
complete — or, if a finding is genuinely intentional and reviewed, recorded as a
`lint_waivers` entry in the pack (see `docs/VALIDATION_RULES.md`).

The deterministic linter checks STRUCTURE, not TRUTH. For factual correctness run
the **Layer-C critic** before the pack is done:

```
python3 scripts/factcheck_pack.py path/to/new-pack.json
```

It sends each keyed answer + explanation to an LLM and reports suspect claims
(probabilistic — verify each against a source). Remaining judgment a critic can
still miss — off-axis distractors, cross-round prompt duplication, exam-critical
accuracy — is the author's responsibility.

This template is intended to be stable and versioned so the generation policy is explicit rather than improvised.
