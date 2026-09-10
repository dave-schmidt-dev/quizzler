# Question-Pack Authoring Specification

## Purpose and authority

Use this document as the complete working contract for a per-domain question-pack
author. Create original questions from the assigned, permitted source material;
do not copy source text into a pack. The deterministic linter implementation in
`scripts/lint_packs.py` is authoritative. `docs/VALIDATION_RULES.md` explains
each rule and owns its thresholds; do not work around a rule by reproducing or
changing a threshold here.

Before writing questions, read the course taxonomy and build the pack's
`coverage_blueprint` from the published objectives, syllabus, or assigned source
outline. Author to that blueprint, not to a list reverse-engineered from the
questions you happened to write. Keep all generated question JSON in the target
pack and report only concise status, counts, and lint results.

## Non-waivable rules

The five non-waivable rules are **L25, L26, L27, L29, and L30**. A waiver for
one of them is ignored; correct the pack instead.

## Pack content contract

Start from the assigned pack template. Preserve its pack-level identity and
native metadata: pack id, subject, title, version, generation metadata, optional
notes, coverage blueprint, and question array. Use real values, never
placeholders. Each question needs an id, supported type, topic, difficulty,
prompt, explanation, and `exam_area`; add `exam_objective` when the course
declares numbered objectives. Include a diagram and useful alternative text when
the objective genuinely requires a visual.

For `multiple_choice` and `scenario_multiple_choice`, provide options and one
zero-based `answer` index. For `multiple_select`, provide options and an
`answers` array of the zero-based indices for all correct options. Do not add
extra formats or infer fields from an archived pack.

## Required workflow

1. Confirm the course metadata, declared areas, published objectives (when
   present), and any grounding mapping that applies to the assigned pack.
2. Create the coverage blueprint before drafting questions. Give every required
   topic or objective the intended minimum and area mapping.
3. Write only `multiple_choice`, `scenario_multiple_choice`, and
   `multiple_select` questions. Do not author `true_false` or `matching` items.
4. Give every question a unique id, one precise topic, an allowed difficulty,
   an exam-area mapping, and a self-contained prompt and explanation.
5. Run `python3 scripts/lint_packs.py <pack>` and fix every critical and warning
   before returning the pack. Treat advisory findings as authoring feedback.
6. Run the required certification campaign and strict manifest build after the
   course-level merge; local preview bypasses never make a pack installable.

## LEAN MODE

For a LEAN pack, write one question per blueprint topic unless that topic's
blueprint requirement calls for more. Size the course to its published objective
weights, rather than padding easy areas. A course total above 200 questions is
an advisory planning signal; a total above 240 blocks installation. If a
comprehensive draft must be reduced, retain the strongest valid question for
each required topic and re-run the full validation path.

## Rule-by-rule authoring requirements

### L1 — prevent token leaks in legacy matching items

Do not author new matching items: L26 rejects them. When repairing an archived
matching item, ensure a left label does not reveal its paired right label and
the correct-pair order is not a predictable identity sequence. See L1 in
`docs/VALIDATION_RULES.md` for the detector's matching behavior.

### L2 — keep the answer out of the stem

Do not place a distinctive prompt term or an obvious semantic echo in only the
correct option. Make all options equally plausible from their wording.

### L3 — keep option length neutral

Write parallel options of comparable length and specificity. Do not make the
key conspicuously longer or shorter than every distractor; see L3 in
`docs/VALIDATION_RULES.md` for the linter-owned comparison.

### L7 — produce valid, complete schema

Use the pack content contract above, provide valid field shapes and a valid key
for every authored question, and do not duplicate normalized option text. For
multiple-select questions, use a meaningful nontrivial answer set; do not make
every option correct.

### L8 — keep parentheticals honest

If the correct multiple-choice or scenario option contains a parenthetical,
make it clarify that option itself. Do not use a parenthetical as an unrelated
hint to the key.

### L9 — avoid near-duplicate prompts

Search the whole pack for substantially overlapping prompts. Merge, remove, or
rewrite duplicate concepts and stems before linting; consult L9 for the
linter-owned similarity threshold.

### L10 — explain the distractors

Write explanations that teach why the wrong choices are wrong, not merely why
the key is right. Use a concise contrast when individual distractor treatment
would be repetitive.

### L12 — provide teaching metadata

Give every authored question a nonblank topic and a permitted difficulty. Give
each supported authored type a substantive explanation that teaches the concept.

### L13 — keep ids unique

Assign each question id once within its pack. Give parallel authors distinct id
prefixes before they begin.

### L14 — avoid meta and position options

Do not use all/none/both/any-of-the-above style options, and never refer to
option letters, numbers, or positions. The renderer shuffles options, so a
position reference is incorrect at display time.

### L15 — prevent legacy matching-option overlap

Do not author new matching items: L26 rejects them. When repairing an archived
matching item, make each label and description conceptually distinct rather
than a near-duplicate wording variant; consult L15 for the linter-owned overlap
test.

### L16 — distribute answer positions

Vary the keyed option position across questions with the same option count.
Do not create a predictable answer-position pattern; see L16 for the linter's
pack-level threshold.

### L17 — remove legacy true/false tells

Do not author new true/false items: L26 rejects them. When repairing an archived
item, remove absolute-qualifier giveaways and avoid a predictable true/false
key pattern; see L17 for the linter-owned balance rule.

### L20 — prevent legacy acronym-expansion leaks

Do not author new matching items: L26 rejects them. When repairing an archived
matching item, do not put an acronym's expansion into its paired description;
describe the concept without handing over the match.

### L21 — make scenarios and diagrams meaningful

Use enough concrete context for a scenario to require application rather than
bare recall. If a question includes a diagram, provide useful alternative text
and ensure the diagram does not reveal the key; see L21 for the linter-owned
scenario threshold.

### L22 — make multiple-select questions discriminating

Use multiple-select only when more than one option is genuinely correct. Keep
correct and incorrect options parallel, avoid stem echoes and meta-options, and
never disclose how many answers to choose or use position references.

### L23 — fulfill the coverage blueprint

Declare a coverage blueprint and meet every stated topic, area, and objective
requirement. Keep topic naming consistent and do not over-concentrate the pack
on one topic. See L23 in `docs/VALIDATION_RULES.md` for its minimum, concentration,
and slug-similarity thresholds.

### L24 — expand acronyms in explanations

On first use in each explanation, expand an acronym in a learner-readable form.
Review L24 advisory findings and correct misses unless the terminology is an
intentional allowlisted exception.

### L25 — make every prompt self-contained

**NON-WAIVABLE.** Write the fact or scenario the learner needs into the prompt.
Never ask what a chapter, textbook, lecture, author, or other unavailable source
says. Reword even a legitimate source attribution into a direct content question.

### L26 — use only exam-valid authored types

**NON-WAIVABLE.** Do not author `true_false` or `matching` questions. Convert a
binary claim into a multiple-choice distinction and use multiple-select only for
a genuine multi-answer skill.

### L27 — align every question with the published area taxonomy

**NON-WAIVABLE.** Use the course's declared, externally sourced syllabus areas;
do not invent a taxonomy. Set every question's `exam_area` to one declared area,
and ensure the course metadata includes the required citation and attestation
for its source kind. Keep blueprint areas aligned with the same declared set and
size the completed course to published area weights as L27 requires.

### L28 — honor opted-in source grounding

When the course declares a grounding block, use the source text mapped to this
pack and ensure the mapping resolves to readable source text. Do not substitute
an informal chat pointer or a merely named source for the declared mapping.

### L29 — satisfy the native pack contract

**NON-WAIVABLE.** Fill the required pack-level metadata with real, nonblank
values that the native client accepts. Use only documented generation metadata,
a valid timestamp, valid notes, and a nonempty question array. See L29 for the
native-contract values and limits.

### L30 — cover the published objective taxonomy

**NON-WAIVABLE.** When the course declares numbered published objectives, map
every question and every applicable blueprint requirement to a declared objective.
Represent every declared objective, and keep each objective's area consistent
with the course taxonomy.

## Waivers and handoff

Only use `lint_waivers` for an intentional, documented finding that the linter
permits to be waived. Never add a waiver for L25, L26, L27, L29, or L30; fix the
pack instead. Before handoff, report the pack path, question count, blueprint
coverage, lint result, and any advisory follow-up.
