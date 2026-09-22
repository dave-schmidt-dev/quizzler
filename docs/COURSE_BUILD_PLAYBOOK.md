# Course Build Playbook

## Purpose

Start with the [Study Delivery Policy](STUDY_DELIVERY_POLICY.md): 20–30 independently checked questions for the next needed topic. Scoped private-study installation and cross-campaign evidence reuse are pending implementation; this scheduling policy does not bypass existing gates.

A step-by-step method for building a **full multi-pack course** (many chapters/
modules, one pack per topic area) via parallel per-chapter authoring agents,
followed by mechanical trimming and an elevated QA gate for high-impact
(exam-stakes) courses. Use this only after an initial private-study starter
path is in place; this playbook is the expansion/release path, not the
first-session path. It complements the normative per-domain authoring
contract:

- `docs/AUTHORING_SPEC.md` — the single document given to each authoring worker.
- `question-packs/AUTHORING.md` — pack schema and the standard installation and
  certification workflow.
- `docs/AUTHORING_GUIDE.md` — supplementary craft guidance for human authors.

This playbook is one level up: it is about standing up an **entire course**
(e.g. the SY0-701 build: 28 packs, one per exam objective, one subagent per
chapter) and the sizing/trim/QA decisions that only show up at that scale.

**Provenance note:** the historical scratchpad was not committed. The new,
versioned worker contract is [AUTHORING_SPEC.md](AUTHORING_SPEC.md); use it as
the authoritative specification rather than treating historical build notes as
an authoring contract.

## When to Use This

- You're building a course with enough chapters/modules that one agent
  authoring the whole thing serially isn't practical (SY0-701's 28-chapter,
  ~28-agent build is the reference case BUILD_NOTES calls the "certified
  `itn260` build" pattern).
- The course has per-chapter source text that maps cleanly to one pack.

The per-chapter grounding pattern applies only to courses that have per-chapter
source text. Courses without it follow the grounding choice documented in their
own `_course.json` and the authoring specification.

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
   in `_course.json`'s `grounding` block — `text_root` (the out-of-repo
   directory the chapter `.txt` files live in) plus `packs` (this pack's
   filename → its chapter `.txt` filename), the same shape
   `scripts/course_grounding.py` resolves for both Layer-C review and Step 2
   authoring below:
   ```json
   "grounding": {
     "text_root": "/absolute/path/to/chapter/text",
     "packs": {
       "ch01-obj1.1-security-controls.json": "Chapter 1 Security Controls.txt"
     }
   }
   ```
   This is the canonical, machine-readable map — `scripts/lint_packs.py`
   rule L28 fails a pack whose course declares `grounding` but has no entry
   for it. `BUILD_NOTES.md`'s "Provenance / grounding" section is commentary
   (why this source, any caveats about it) alongside this, not a substitute
   for it; the two describing different chapter mappings is a bug, not a
   style choice.
2. Add the course under `question-packs/<course>/` with a `_course.json`
   (see `question-packs/AUTHORING.md` → "Adding a New Course"), including the
   `grounding` block from (1).
3. Confirm the Step 0 sizing decision is recorded before proceeding.

## Step 2 — Per-Chapter Authoring Agent Contract

One subagent per chapter/module receives `docs/AUTHORING_SPEC.md` as its
complete authoring contract. For courses with per-chapter source text, that
agent also receives its mapped chapter/module source.

1. **Read three things before authoring anything:**
   - Its chapter/module's source text (the sole grounding for its questions —
     zero-hallucination: questions are original/paraphrased, never copied,
     and the source text is never redistributed). Resolve the file through
     the course's own `_course.json` → `grounding.packs[<this pack's
     filename>]`, under `grounding.text_root` (Step 1.1) — the identical
     lookup `scripts/course_grounding.py` uses for Layer-C review, so
     authoring and review are grounded in the exact same file. Do not accept
     a separately-pointed-to file from BUILD_NOTES prose or a chat
     instruction; if the pack's entry is missing, that is a Step 1 gap to
     fix before authoring, not something to route around.
   - [The shared authoring specification](AUTHORING_SPEC.md). It supplies the
     schema-facing workflow, LEAN MODE, and every linter rule as an authoring
     imperative.
2. **Derive the pack's `coverage_blueprint`** from the chapter's own
   structured topic list before writing any questions (SY0-701 used each
   chapter's "Essential Terms and Components" list as its sub-objective
   universe). Generalize: use whatever structured topic enumeration the
   source material provides — a syllabus unit's learning objectives, an
   exam blueprint's sub-objectives, a chapter's own summary/glossary list.
   Authoring against the blueprint (not the reverse) is what produces full
   topic coverage instead of an accidental topic mix (`AUTHORING.md` Rule 1;
   `docs/AUTHORING_GUIDE.md` "Before You Author").
3. **Author against the current type and difficulty contract.** Use only the
   supported authored types in `AUTHORING_SPEC.md`; choose an objective-appropriate
   mix and difficulty distribution rather than copying historical mixes that
   include rejected formats.
   - Under a **lean** sizing decision (Step 0), follow the LEAN MODE section in
     `AUTHORING_SPEC.md`; no trim step is needed for a pack authored at that
     target.
   - Under a **comprehensive** sizing decision: author the fuller set per
     topic; the course then applies Step 3 (mechanical trim) afterward if a
     later sizing reassessment moves it to lean.
4. **Self-lint to 0 critical and 0 warning before returning** — run
   `scripts/lint_packs.py` on the mini-pack (including its own slice of
   `coverage_blueprint`, i.e. rule L23) and fix every critical finding. Once
   `_course.json` declares `grounding` (Step 1.1), this also runs rule L28,
   which fails the pack if its filename has no working entry in
   `grounding.packs` — the mechanical backstop for the Step 2.1 requirement
   above. This mirrors
   the merge-safety pattern in `AUTHORING.md` → "Authoring Large Packs with
   Parallel Agents": each agent's self-lint pass is what makes the later
   whole-course merge safe. If authoring through Claude interactively, the
   repository pre-commit hook runs this for staged pack files.
5. **Advisories**: track advisory findings in BUILD_NOTES. Warnings block the
   staged-pack and readiness gates; resolve them or use a documented waiver
   when the rule permits one.
6. **Output-safety rules** (`AUTHORING.md`): never echo full question JSON
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

Once all chapter packs exist (trimmed or not), use
`scripts/certification_campaign.py` to freeze each pack's snapshot and evidence
ledger. Run one full `hybrid_verify.py <pack> --no-certify --json` discovery
invocation; it records two reviewer results. Batch the resulting findings and run
`--only` targeted confirmation for edited ids with bounded duplicate-neighborhood
context. A malformed or incomplete report from either reviewer blocks the
campaign; an operational failure may retry only while the snapshot is unchanged.
Then run `hybrid_verify.py <pack> --certify-campaign <ledger>` and only then run
`build_manifest.py`. This deterministic finalizer rechecks the frozen evidence,
snapshot, and Layer A without a new reviewer/LLM call. For a course built as
parallel slices of one conceptual pack, also run
the cross-cluster checks in `AUTHORING.md` → "Merge + full gate" (duplicate ids,
L23 across the full blueprint, L9 near-duplicate stems across clusters).

## Step 5 — Elevated QA for High-Impact Courses (INV-8)

A several-hundred-question bank someone stakes a real exam on needs more than
lint-clean. For that class of course, BUILD_NOTES documents a 5-layer gate:

1. Layer A (`lint_packs.py`) — 0 critical and 0 warning, course-wide; advisory
   findings are tracked in BUILD_NOTES.
2. Layer C campaign — one non-certifying full hybrid discovery on a frozen
   snapshot, recording both reviewer results; batched remediation and targeted
   confirmation, then deterministic `--certify-campaign` stamping with no new
   reviewer call.
3. **Independent content review** (a different model/reviewer than authored
   the content, per domain/section) — factual accuracy, objective coverage
   with no scope drift, confirms the lean survivor per topic is the
   strongest available (swapping from the `_full/` backup if not), difficulty
   calibration.
4. Human spot-check — a sample plus every review finding surfaced before the
   course is called "done."

Record the outcome of this pipeline in the course's own `BUILD_NOTES.md`
(status, findings remediated, accepted advisories) — see SY0-701's own file
for the reference shape ("Status", "QA outcome", "Findings remediated",
"Accepted lean advisories" sections).

## Class courses (syllabus taxonomy, no per-chapter grounding)

A university class course follows a different operational shape than a multi-pack vendor certification course. When building a class course, apply the following rules:

1. **Source citation requirements (Rule L27):** Under `kind: "syllabus"`, the linter requires only a non-blank `source.title`. The absolute-HTTPS `url` and the `syllabus_verified_by` reviewer and date attestation are checked only when `kind: "exam_objectives"` (`scripts/lint_packs.py:2266`).
2. **Optional area weights (Rule L27):** Area weights are optional. If a class syllabus publishes no per-module weights, declare none rather than inventing artificial percentages. Under rule L27 (detailed in `docs/VALIDATION_RULES.md`), declaring weights across all areas requires them to sum to 100, declaring weights on some but not all areas is a critical failure, and declaring no weights under a published kind emits a single advisory.
3. **Opt-in objectives (Rule L30):** The `syllabus.objectives` field is opt-in. Once declared, rule L30 requires every declared objective to be represented in the pack blueprint and question mappings. Consequently, a course built incrementally across terms should declare `syllabus.objectives` only when its packs cover the entire syllabus. See `docs/VALIDATION_RULES.md` for L30 details.
4. **Incremental taxonomy and unused areas (Rule L27):** Two distinct L27 checks govern unused areas, and their severity levels differ:
   - A declared area with no questions in a pack produces an advisory (`scripts/lint_packs.py:2471-2481`).
   - A declared area named by no explicit-area `coverage_blueprint` entry produces a CRITICAL failure (`scripts/lint_packs.py:2365-2373`).
   Because every declared area must be reachable from an explicit-area blueprint entry, a class course cannot declare its full-term taxonomy in advance before blueprints exist to cover it. Instead, declare only the areas reached by the blueprints of shipped packs, and add new areas and their blueprint entries together in the same change as later packs are introduced.

## What This Playbook Does Not Cover

- Historical scratchpad wording; the committed [authoring specification](AUTHORING_SPEC.md)
  is the current contract.
- Anything specific to a particular course's grounding material or objective
  map — that belongs in that course's own `BUILD_NOTES.md`.
- Per-question authoring craft (good distractors, visuals, difficulty
  calibration) — see `docs/AUTHORING_GUIDE.md`.
