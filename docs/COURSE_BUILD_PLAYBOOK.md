# Course Build Playbook

## Purpose

A step-by-step method for building a **full multi-pack course** (many chapters/
modules, one pack per topic area) via parallel per-chapter authoring agents,
followed by mechanical trimming and an elevated QA gate for high-impact
(exam-stakes) courses. This complements, but does not replace:

- `docs/AUTHORING_GUIDE.md` — how to write a *good individual question* by hand
  (prompts, distractors, difficulty, visuals).
- `question-packs/AUTHORING.md` — the pack **schema**, the standard single-pack
  workflow (`lint_packs.py` → `verify_pack.py` → `build_manifest.py`), and the
  existing "Authoring Large Packs with Parallel Agents" section for splitting
  *one* large pack across cluster-agents.

This playbook is one level up: it is about standing up an **entire course**
(e.g. the SY0-701 build: 28 packs, one per exam objective, one subagent per
chapter) and the sizing/trim/QA decisions that only show up at that scale.

**Provenance note (no-hallucination):** the per-agent authoring contract below
is reconstructed from two sources only:
`question-packs/sy0-701/BUILD_NOTES.md` (the "Authoring method" and "Sizing
decision" sections) and `question-packs/AUTHORING.md`. The original shared
spec these agents actually read — a session-scratchpad file named
`AUTHORING_SPEC.md` — was never committed and is gone. Where BUILD_NOTES only
summarizes what that file contained, this doc says so and marks the missing
detail `(unrecoverable — reconstruct from next build)` rather than inventing
it. Do not treat this document as a byte-for-byte recovery of that spec.

## When to Use This

- You're building a course with enough chapters/modules that one agent
  authoring the whole thing serially isn't practical (SY0-701's 28-chapter,
  ~28-agent build is the reference case BUILD_NOTES calls the "certified
  `itn260` build" pattern).
- Each chapter/module has its own grounding text (a book chapter, a syllabus
  unit) that maps cleanly to one pack.

For a single oversized pack (not a whole course), use
`question-packs/AUTHORING.md` → "Authoring Large Packs with Parallel Agents"
instead — same splitting idea, smaller scope.

## Step 0 — Pre-Authoring Gate: Sizing Decision

Before the first authoring agent runs, decide and **record in the course's own
`BUILD_NOTES.md`**: is this course **lean** (~1 question per blueprint topic,
sized for drilling against a short real exam) or **comprehensive** (multiple
questions per topic, ~1.3× sum-of-mins)? SY0-701 started comprehensive and was
reassessed to lean mid-build (BUILD_NOTES "Sizing decision"); deciding this
*before* authoring avoids a mid-build resize and the extra
comprehensive→trim step (Step 3) it forces on every already-authored pack.
This is a pre-authoring gate decision, not something to leave implicit — see
the sizing-gate line queued for `question-packs/AUTHORING.md`.

## Step 1 — Course Setup

1. Identify the grounding source per pack: one chapter/module of text per
   pack, one pack per syllabus/exam objective. Record the objective→pack map
   and the source material's location in the course's `BUILD_NOTES.md`
   (gitignored if the source text is copyrighted — see SY0-701's own
   "Provenance / grounding" section for the pattern: chapter text lives
   outside the repo, only original questions are authored from it).
2. Add the course under `question-packs/<course>/` with a `_course.json`
   (see `question-packs/AUTHORING.md` → "Adding a New Course").
3. Confirm the Step 0 sizing decision is recorded before proceeding.

## Step 2 — Per-Chapter Authoring Agent Contract

One subagent per chapter/module (BUILD_NOTES: "Per-chapter subagent
orchestration (Sonnet)... Mirrors the certified `itn260` build"). Each agent's
task, reconstructed from BUILD_NOTES + AUTHORING.md:

1. **Read three things before authoring anything:**
   - Its chapter/module's source text (the sole grounding for its questions —
     zero-hallucination: questions are original/paraphrased, never copied,
     and the source text is never redistributed).
   - The shared authoring spec. `(unrecoverable — reconstruct from next
     build)`: the actual spec was a session-scratchpad file, `AUTHORING_SPEC.md`,
     never committed. BUILD_NOTES states only its table of contents — schema,
     blueprint rule, type/difficulty mix, "all L1–L23 rules restated as
     authoring rules," and a LEAN MODE toggle — not its exact wording. The
     schema and blueprint rule are independently recoverable from
     `question-packs/AUTHORING.md` and `docs/QUESTION_SCHEMA.md`; the type/
     difficulty mix is recoverable (below); the verbatim "L1–L23 restated as
     authoring rules" phrasing and the precise LEAN MODE instruction text are
     not — a future build should re-derive them from the current
     `scripts/lint_packs.py` rule docstrings and re-save the result as a
     committed doc (not a scratchpad) so this gap doesn't recur.
   - The **Layer-A linter source** (`scripts/lint_packs.py`) directly, so the
     agent authors against the actual current rules rather than a
     possibly-stale restatement of them.
2. **Derive the pack's `coverage_blueprint`** from the chapter's own
   structured topic list before writing any questions (SY0-701 used each
   chapter's "Essential Terms and Components" list as its sub-objective
   universe). Generalize: use whatever structured topic enumeration the
   source material provides — a syllabus unit's learning objectives, an
   exam blueprint's sub-objectives, a chapter's own summary/glossary list.
   Authoring against the blueprint (not the reverse) is what produces full
   topic coverage instead of an accidental topic mix (`AUTHORING.md` Rule 1;
   `docs/AUTHORING_GUIDE.md` "Before You Author").
3. **Author against the type/difficulty targets** (BUILD_NOTES, this build):
   type mix ~60% multiple_choice / 15% scenario_multiple_choice / 12%
   matching / 8% true_false / 5% multiple_select; difficulty ~35% easy / 45%
   medium / 20% hard. (Note: `question-packs/AUTHORING.md`'s own worked
   example under "Feeding Packs via Claude" gives a different illustrative
   split — ~55/20/10/10/5 — that is a generic example, not this build's
   target; use the BUILD_NOTES numbers for a course-build agent.)
   - Under a **lean** sizing decision (Step 0): author directly at ~1
     question per blueprint topic; no trim step needed for that pack.
     Whether "LEAN MODE" also changed anything else about how an individual
     agent authored (versus just the target count) is
     `(unrecoverable — reconstruct from next build)`.
   - Under a **comprehensive** sizing decision: author the fuller set per
     topic; the course then applies Step 3 (mechanical trim) afterward if a
     later sizing reassessment moves it to lean.
4. **Self-lint to 0 critical / 0 warning before returning** — run
   `scripts/lint_packs.py` on the mini-pack (including its own slice of
   `coverage_blueprint`, i.e. rule L23) and fix every finding. This mirrors
   the merge-safety pattern in `AUTHORING.md` → "Authoring Large Packs with
   Parallel Agents": each agent's self-lint pass is what makes the later
   whole-course merge safe. If authoring through Claude interactively, the
   `PostToolUse` hook (`scripts/lint_hook.py`, wired in `.claude/settings.json`)
   runs this automatically on every write.
5. **Output-safety rules** (`AUTHORING.md`): never echo full question JSON
   into the agent's own messages/reasoning — write directly to the target
   file and report only summary stats; for a large file, write the skeleton
   first and append questions in batches via `Edit`. Give each chapter agent
   a unique question-`id` prefix (SY0-701 used `c<chapter>q<n>` throughout —
   e.g. `c19q18`, `c4q35`, per BUILD_NOTES' own "Findings remediated" list)
   so ids never collide at merge.

## Step 3 — Mechanical Trim (Comprehensive → Lean)

If Step 0 decided lean sizing but one or more packs were authored
comprehensively (or a mid-build resize happens, as in SY0-701), run
`scripts/trim_pack.py` per pack:

```
python3 scripts/trim_pack.py question-packs/<course>/<pack>.json
```

What it does and does not do (see the script's own docstring for the full
contract):

- Keeps ~1 question per blueprint topic (more if a topic's `min` > 1),
  choosing the survivor by a **mechanical, metadata-only** rule — current
  question-type and keyed-answer-index representation in the running trimmed
  set, then original file order. It never reads prompt/explanation text and
  never ranks "which question is better."
- Backs up the untrimmed pack to `<course>/_full/<pack>.json` first (a
  path already covered by the repo's `question-packs/*/` gitignore rule —
  committed-ignored, never a session scratchpad).
- Emits a trim report (dropped question ids, plus a `manual_review` entry
  for every topic where an alternate was mechanically dropped).
- **Does not decide which question is "strongest."** BUILD_NOTES defines no
  such heuristic, so the script never invents one. Every topic with a
  dropped alternate is flagged for a **human** to confirm the mechanical
  survivor against the `_full/` backup — this is what satisfies INV-8's
  "keep the strongest per topic" requirement without silently discarding
  content behind a fake quality ranking.

Re-run `scripts/lint_packs.py` course-wide after trimming (BUILD_NOTES:
"Enforced at authoring time; re-run course-wide after trimming").

## Step 4 — Merge + Full Gate

Once all chapter packs exist (trimmed or not): run `verify_pack.py` per pack
(Layer A + Layer C) per `AUTHORING.md`'s "done" gate, then
`build_manifest.py` to install the course. For a course built as parallel
slices of what is conceptually one pack, also do the cross-cluster checks in
`AUTHORING.md` → "Merge + full gate" (duplicate ids, L23 across the full
blueprint, L9 near-duplicate stems across clusters).

## Step 5 — Elevated QA for High-Impact Courses (INV-8)

A several-hundred-question bank someone stakes a real exam on needs more than
lint-clean. For that class of course, BUILD_NOTES documents a 5-layer gate:

1. Layer A (`lint_packs.py`) — 0 critical / 0 warning, course-wide.
2. Layer C standard (`verify_pack.py`) — factual critic, stamps `certification`.
3. Layer C strict (`verify_pack.py --strict`) — re-grades against generic
   subject knowledge, ignoring the pack's own `source_directive`.
4. **Independent content review** (a different model/reviewer than authored
   the content, per domain/section) — factual accuracy, objective coverage
   with no scope drift, confirms the lean survivor per topic is the
   strongest available (swapping from the `_full/` backup if not), difficulty
   calibration.
5. Human spot-check — a sample plus every review finding surfaced before the
   course is called "done."

Record the outcome of this pipeline in the course's own `BUILD_NOTES.md`
(status, findings remediated, accepted advisories) — see SY0-701's own file
for the reference shape ("Status", "QA outcome", "Findings remediated",
"Accepted lean advisories" sections).

## What This Playbook Does Not Cover

- The exact original `AUTHORING_SPEC.md` wording (see Step 2 note).
- Anything specific to a particular course's grounding material or objective
  map — that belongs in that course's own `BUILD_NOTES.md`.
- Per-question authoring craft (good distractors, visuals, difficulty
  calibration) — see `docs/AUTHORING_GUIDE.md`.
