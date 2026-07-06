# Creating New Question Packs

## Quick Start

1. Copy `pack-template.json` into the course folder (e.g., `my-course/round-8.json`).
2. Fill in questions following the schema below.
3. Lint it clean (Layer A): `python3 scripts/lint_packs.py my-course/round-8.json` must report **0 critical, 0 warning**. (When you author through Claude, the PostToolUse hook in `.claude/settings.json` runs this automatically on every write and surfaces any finding to fix on the spot — quality is enforced at creation time, not at launch.)
4. **Run the readiness gate — this is the "done" check:** `python3 scripts/verify_pack.py my-course/round-8.json` must exit **0** (`PACK READY`). It runs Layer A + the Layer-C factual critic together; the pack is not done until this passes. A reviewed critic false-positive can be dismissed with a `factcheck_waivers` entry (see `docs/VALIDATION_RULES.md`). (`--no-factcheck` runs structure-only and does NOT certify readiness.)
5. Run `./start.sh` (or `python3 scripts/build_manifest.py`) — the manifest auto-discovers your new pack. The build is strict-by-default: a Layer-A critical aborts it (`--no-strict` to override).
6. Reload the app.

No code edits required. The home-screen course list is generated from `question-packs/manifest.json`, which `scripts/build_manifest.py` rebuilds by walking the `question-packs/` folder. The build/launch pass is quiet about quality (summary line + criticals only; full detail in `/tmp/quizzler-lint.log`, `--verbose` for inline) because the gate already ran at authoring time. A genuinely intentional finding can be recorded as a `lint_waivers` entry — see `docs/VALIDATION_RULES.md`.

## Adding a New Course

1. Create a folder under `question-packs/` (e.g., `question-packs/mycourse/`).
2. Add a `_course.json` file in that folder:

   ```json
   {
     "id": "mycourse",
     "name": "My Course",
     "description": "Short description shown on the course card",
     "sort_order": 100
   }
   ```

   - `id`: kebab/snake-case identifier used in localStorage keys; should match the folder name.
   - `name`: display label.
   - `description`: one-line tagline shown on the card.
   - `sort_order` (optional): lower numbers appear first on the home screen. Default `100`. The bundled `samples` course uses `0` to stay first as a demo.

   `_course.json` is itself optional — if missing, the build script derives `id` and `name` from the folder name. Adding it is recommended for a polished display.

3. Drop one or more pack JSON files into the same folder, following the schema below.
4. Run `./start.sh` (or `python3 scripts/build_manifest.py`) and reload the app.

The build script ignores hidden files, validates pack JSON, and warns about empty courses. Pack ordering inside a course is the natural-sorted filename (so `mod1.json`, `mod2.json`, ..., `mod10.json` all sort correctly).

## Pack Schema

```json
{
  "pack_id": "course-round-N",     // unique ID
  "subject": "Course Name",         // display name
  "title": "Round N",               // short title
  "version": 1,                     // increment when editing
  "generated_at": "ISO-8601",       // when created
  "generation_mode": "manual|llm|hybrid",
  "notes": "Optional focus description (max 120 chars — shown as the module subtitle on the home screen)",
  "coverage_blueprint": [           // optional — the pack's required topic universe (rule L23)
    {"topic": "rds-multi-az", "min": 2},
    "sqs-vs-sns"                    // bare string == {"topic": "sqs-vs-sns", "min": 1}
  ],
  "questions": [ ... ]
}
```

> **Length limit:** `notes` becomes the module subtitle on the course's home-screen card. Keep it to **≤ 120 characters** — the build script (`scripts/build_manifest.py`) prints a warning if a pack exceeds this and the UI will truncate it. One short sentence beats a paragraph; put longer rationale in the pack's questions, not the subtitle.

> **`coverage_blueprint` (optional):** declares the topics the pack must cover, so
> the linter can enforce **full topic coverage** (rule **L23**). Each entry is
> either `{"topic": "<slug>", "min": N}` or a bare `"<slug>"` string (min 1). When
> present, a required topic with fewer than its `min` questions is a **CRITICAL**;
> when absent, the pack gets only a non-blocking advisory nudge. Build the
> blueprint from your source of truth (syllabus / exam objectives) **before**
> authoring the questions. See `docs/QUESTION_SCHEMA.md` and `docs/VALIDATION_RULES.md`.

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

### multiple_select
- `options`: array of **3+** strings
- `answers`: array of zero-based indices of **all** correct options (the plural `answers`, NOT the single `answer` field)
- Grading is **all-or-nothing** — the learner must select exactly the correct set, no partial credit
- Best for: "select all that apply", multi-fact recall, exam parity with select-all formats
- Use **2+** correct answers (exactly one correct → author as `multiple_choice`) and always leave at least one distractor
- Do NOT state how many to pick in the prompt — disclosing the count narrows guessing (linter rule L22 warns)

## All Questions Must Have

- `id`: unique within the pack (e.g., "r8q1")
- `type`: one of the five types above
- `topic`: kebab-case topic slug (e.g., "partial-dependency")
- `difficulty`: "easy", "medium", or "hard"
- `prompt`: the question text
- `explanation`: teaches the concept, not just "the answer is X"
- `diagram`: SVG string or `null` (optional)

## Quality Rules

1. Test one real idea per question
2. Use the simplest question type that fits
3. Do not let diagrams reveal the answer
4. Explanations should teach, not just restate. Say why the **wrong** answers are wrong, not only why the right one is right — a learner stuck between two plausible options needs the distractor addressed. The linter (rule L10) flags MC/scenario explanations that name no distractor as a critical; a brief contrast clause ("unlike X, …", "the others address other threats") satisfies it and is the right fix for pure-recall items that have no per-distractor concept to explain.
5. Abbreviations/acronyms in explanations must be spelled out on first use (e.g., "Proof of Work (PoW)")
6. Keep distractors plausible but clearly wrong
7. Do NOT use "All of the above", "None of the above", "Both A and B", or any position-referential option ("A and C"). The engine shuffles options at render time (`shuffleOptions` in `app/index.html`), so an option that names a position points at the wrong option after the shuffle — a correctness bug, not merely a style issue. "All/None of the above" is also gameable: one known-true or known-false option settles it without full knowledge. Enumerate the specific combinations as complete option text instead.
8. No duplicate prompts within a pack or across recent packs
9. Matching sets must be coherent (no obvious outliers). All right-side descriptions must distinguish their terms along ONE consistent axis (all by channel, or all by mechanism — not a mix), and each must capture the term's defining feature, not a side trait. Counter-example: a social-engineering set describing Phishing/Vishing/Smishing by channel (email/voice/SMS) but Business Email Compromise by mechanism (fund-transfer fraud), where the BEC description never mentions its defining email-account compromise — every pair is correct, but the set feels inconsistent.
10. Randomization is handled by the engine — store answers in canonical order
11. If the topic is inherently visual (charts, patterns, diagrams), the question must include a diagram
12. **Every question must stand on its own.** The engine randomizes question order, so prompts cannot reference previous questions. Phrases like "Same scenario:", "as discussed earlier", "in the previous question", or "referring to the prior" will break for the user when the engine draws the follow-up before the setup. If two questions share a scenario, restate the scenario setup in each prompt. The build script warns on common sequential-coupling phrases.
13. **Cover the whole topic universe (L23).** Declare a `coverage_blueprint` (above) and make sure every blueprint topic has at least its `min` questions — a short topic is a CRITICAL. Don't let one topic dominate (L23 warns above ~15% of the pack) and keep topic slugs consistent so coverage isn't fragmented across near-duplicate variants (e.g. `shared-responsibility` vs `shared-responsibility-model`).

### Common answer tells to avoid

- Parallel construction / qualifier polarity: keep distractors within ~±20% length of the key and matching grammatical shape. Do not confine absolute words (always, never, all, none, only, must, cannot, reliably) to the distractors while the key is the lone hedged option, nor confine hedges (usually, can, typically, may) to the key — either lets a test-wise reader pick by polarity without knowing the content.
- true_false: avoid keying a statement False purely on an absolute qualifier ("X is ALWAYS required" → False) — the "absolutes are false" heuristic makes it free. Keep each pack's True/False split reasonably balanced so blind-guessing one value does not score well.
- Matching acronym leak: when left items are acronyms, do not let the right-side description contain the acronym's expansion words (MD5 → "message-digest", SRTP → "real-time", S/MIME → "mail"); describe by function instead.
- Cross-question concept reuse: do not re-test the same answer-fact across question types in the same course — a matching right-item that restates a standalone MC's keyed answer hands the learner a free pairing. L9 compares prompt text only, not concepts, so this is on the author.
- multiple_select: keep the correct and incorrect options in parallel construction and similar length — L22 flags a correct set that averages conspicuously longer or shorter than the distractors, a distinctive prompt term that appears only in the correct options, and a prompt that discloses how many answers are correct.

## Feeding Packs via Claude

When asking Claude to generate a new pack, provide:
1. The course name and topic areas to cover
2. Weak topics from session history (visible on the history screen or in localStorage under `quizzler_sessions`)
3. A reference to this schema

Example prompt:
> Generate a 20-question pack for your subject covering [topics]. Use the schema from pack-template.json.
> Here are my weak areas from the last session: [paste session JSON].
> Mix question types: ~55% multiple choice, ~20% matching, ~10% true/false, ~10% scenario, ~5% multiple select.

## Authoring Large Packs with Parallel Agents

For a large pack (roughly **> 40 questions**), do NOT have one agent author the whole thing. Split it across parallel cluster-agents — it is faster and, critically, avoids the model's **per-message output-token ceiling**: an agent that tries to emit an entire large pack in one tool call can exceed the limit and lose all its work (observed: a ~46-question cluster crashed at the 64k-output-token ceiling while writing, producing nothing).

### Split by blueprint slice
1. Build the `coverage_blueprint` first — it is the authoritative topic list (Quality Rule 13).
2. Partition it into clusters of **~20–30 questions** each, by module or topic group. Size each cluster by its **sum-of-mins**, not topic count; split a module further when its floor is large (e.g. a 30-topic / ~46-question module → two agents, one per grounding source).
3. Give each agent: the schema + quality rules, **its blueprint slice** (as that mini-pack's own `coverage_blueprint`), its grounding sources, and a **unique question-`id` prefix** (e.g. `n5s`, `n7`) so IDs never collide at merge.
4. Each agent authors only its slice and **self-lints its mini-pack to 0 findings** (`lint_packs.py`, including L23 for its own topics) before returning.

### Output-safety rules for each authoring agent
- **Never echo question JSON into the agent's messages or reasoning.** Write questions to the target file only and report summary stats. Dumping the pack into chat is what blows the output-token limit.
- For a large file, **write the pack skeleton first, then append questions in batches via `Edit`**; keep every single tool call's output well under the model's max output tokens.
- Prefer more, smaller clusters over one big one.

### Merge + full gate (cross-cluster problems surface only here)
Splitting defers whole-pack checks to a merge step. After collecting the cluster files:
1. Concatenate all `questions` into the master pack (which carries the **full** `coverage_blueprint`); confirm there are **no duplicate `id`s**.
2. Run `lint_packs.py` on the merged pack — these fire for the first time across clusters:
   - **L23 coverage completeness** over the whole blueprint (no cluster left a topic short).
   - **L9 near-duplicate stems** across clusters — two agents can independently write similar prompts, especially the `multiple_select` "select all that apply" boilerplate; reword the stems to disambiguate (avoid re-introducing an L22 stem-echo — keep answer-descriptive words out of the reworded prompt).
   - **L23 duplicate-slug** across the merged topic set (rename to break a shared prefix rather than waiving L23, which would disable coverage enforcement pack-wide).
3. Fix to 0 findings, then run `verify_pack.py` (Layer A + Layer C) as the final "done" gate.

The `coverage_blueprint` + L23 is what makes splitting safe: the master blueprint guarantees the merged pack is complete even though no single agent ever saw the whole thing.
