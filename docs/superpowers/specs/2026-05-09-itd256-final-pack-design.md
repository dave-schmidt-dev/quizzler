# ITD 256 Final Wrap-Up Question Pack — Design Spec

**Date:** 2026-05-09
**Status:** Approved by user 2026-05-09, pending implementation plan
**Owner:** dave
**Deliverable:** `question-packs/itd256/round-12-final-ch1-14.json` (Quizzler) + 5 scraped chapter quizzes (ITD256)

## Goal

Create a comprehensive, instructor-faithful final-exam wrap-up question pack for ITD 256 (Database Concepts & Design, Coronel 14e). The pack covers chapters 1–14 (final exam scope per syllabus), totals 120 questions, and is sourced from the actual chapter quizzes assigned in Canvas — per the instructor's stated guidance that "quizzes will be wrap-ups of those."

## Context

- **Course:** ITD 256, NVCC Spring 2026, Cuong Hoang. Final exam covers Coronel ch1–14.
- **Existing Quizzler coverage:** 13 itd256 packs covering ch1–10 (rounds 1–11 plus quiz2-ch7-10). Chapters 11–14 have zero Quizzler coverage today.
- **Existing chapter-quiz materials in `~/Documents/ITD256/`:**
  - Ch 5 (`Quiz: Ch5.pdf`, `ITD256_Ch5_Quiz_Explanations.md`)
  - Ch 6 (`Quiz: Ch6.pdf`, `ITD256_Ch6_Quiz_Explanations.md`)
  - Ch 7 (`Quiz Ch7.pdf`)
  - Ch 8 (`QuizCh8.pdf`)
  - Ch 9 (`ch9-quiz.md`)
  - Ch 10 (`ch10-quiz.md`)
  - Ch 13 (`ch13-quiz.md`)
- **Missing chapter quizzes (must scrape from Canvas):** Ch 1, 2, 3, 4, 14
- **Existing scraping infrastructure:** `~/Documents/ITD256/canvas_771757/` — Playwright + persistent SSO profile (`profile/`). Built for Cengage NextBook decks; the Canvas quiz path is a separate target requiring a fresh recon pass.

## Two-Phase Plan

### Phase 1 — Canvas chapter-quiz scrape (prerequisite)

Pull the 5 missing chapter quizzes from `https://learn.vccs.edu/courses/771757/quizzes` (the course's quiz index page), including answer keys with explanations where available.

**Outputs (in `~/Documents/ITD256/`):**

- `ch1-quiz.md`
- `ch2-quiz.md`
- `ch3-quiz.md`
- `ch4-quiz.md`
- `ch14-quiz.md`
- Convert existing PDFs (Ch5, 6, 7, 8) to matching `chN-quiz.md` markdown for source uniformity. Keep originals as `.pdf` for provenance.

**End state:** 14 uniformly-named `chN-quiz.md` files in `~/Documents/ITD256/`, each containing question text, options, correct answer, and explanation where the source provided one.

**Constraints:**
- Reuse `canvas_771757/profile/` for SSO; do not store credentials in scripts.
- Do not modify the existing NextBook scraper (`scrape_nextbook.js`) — Canvas quizzes are a distinct target.
- Recon-then-scrape pattern (per `tasks.md` Task 2): write a recon script first to confirm Canvas quiz URL/auth model, then a production scraper.

### Phase 2 — Pack authoring

With 14 chapter quizzes as canonical source, author `question-packs/itd256/round-12-final-ch1-14.json`.

## Pack Composition

- **File:** `question-packs/itd256/round-12-final-ch1-14.json`
- **`pack_id`:** `itd256-round-12-final-ch1-14`
- **`title`:** `Round 12 — Final Wrap-up (Ch 1–14)`
- **`subject`:** `ITD 256`
- **`generation_mode`:** `manual`
- **`source_rounds`:** `["ch1-quiz", "ch2-quiz", ..., "ch14-quiz"]` (filenames sans extension, for traceability)
- **`notes`:** `Cumulative final exam prep — 120 questions across all 14 chapters, weighted toward ch7–14.` (≤120 chars per pack-template guidance)
- **Total questions:** 120
- **Type mix:** 42 multiple_choice (35%), 42 scenario_multiple_choice (35%), 14 true_false (12%), 22 matching (18%)
- **Difficulty mix:** 36 easy (30%), 48 medium (40%), 36 hard (30%)
- **Topic slugs:** kebab-case, consistent with existing pack vocabulary (e.g., `normalization`, `sql-joins`, `transaction-isolation`, `referential-integrity`). Reuse existing slugs wherever the topic was previously covered; introduce new slugs only for ch11–14 concepts.

## Per-Cluster Distribution

Clusters mirror the structure of `~/Documents/ITD256/Textbook/study/roundups/final_ch01-14.md`. Distribution is weighted toward ch7–14 to reflect typical cumulative-final emphasis on second-half material.

| Cluster | Chapters | Questions | Rationale |
|---|---|---|---|
| Foundations | 1–3 | 14 | Light recall — well-covered by rounds 1–4 already |
| Design | 4–6 | 18 | ER/EER/normalization is exam-heavy; medium weight |
| SQL & Schema | 7–8 | 22 | Highest exam emphasis; SQL questions skew scenario |
| Process | 9 | 12 | Moderate weight; mostly MC/T-F |
| Transactions & Performance | 10–11 | 22 | Heavy scenario weight (ACID, deadlock, tuning) |
| Distributed | 12 | 12 | Terms-heavy → matching well-suited |
| BI/Warehousing | 13 | 12 | OLAP/star-schema scenarios |
| Big Data/NoSQL | 14 | 8 | Lighter — terminology and CAP tradeoffs |
| **Total** | **1–14** | **120** | |

Per-cluster type and difficulty allocations are derived from the global mix proportionally during authoring. Authors may shift ±2 per cell where source material justifies it (e.g., terminology-heavy ch12 may absorb extra matching).

## Diagram Budget

Approximately 12 hand-authored inline-SVG diagrams, matching the visual style established in rounds 1–7:

- Dark theme palette: `#1f2937` (entity fill), `#38bdf8` (entity stroke), `#22c55e` (relationship stroke), `#e5e7eb` (text)
- Always include `viewBox` and `aria-label` on the root `<svg>` (renderer injects via `innerHTML` — accessibility lives inside the SVG)
- Stored in the question's `diagram` field as a string; `diagram_alt` optional but encouraged for redundancy

**Allocation:**

| Cluster | Diagrams | Concepts |
|---|---|---|
| Design (4–6) | 5 | 2× ER notation (1:M, M:N with bridge); 1× EER specialization (disjoint vs overlapping); 2× normalization FD diagrams |
| Transactions/Performance (10–11) | 2 | Transaction state diagram; deadlock wait-for graph |
| Distributed (12) | 1 | Fragmentation topology (horizontal vs vertical) |
| BI (13) | 2 | Star schema; snowflake schema |
| Big Data (14) | 1 | CAP theorem triangle with labels |
| Foundations / SQL / Process | 1 | One bonus diagram TBD during authoring (likely a JOIN visualization for ch7) |
| **Total** | **12** | |

**Rules:**
- Diagram-bearing questions are always `medium` or `hard` difficulty — never `easy`.
- No raster/image diagrams (no PNG/JPEG references). Inline SVG only — keeps the pack file self-contained.
- SVG content should be hand-authored or hand-cleaned; no machine-generated SVG dumps with bloated path data.

## Authoring Rules

1. **Source-bound provenance.** Every question's `_comment` field cites the source chapter quiz and original question number (e.g., `"Source: ch7-quiz.md Q4; rephrased as scenario"`). Questions without traceable provenance are not allowed.
2. **Wrap-up principle.** Each question is one of:
   - (a) a fresh rephrasing of a chapter-quiz Q,
   - (b) a scenario combining two chapter-quiz concepts,
   - (c) a high-yield Q lifted with strengthened distractors.
   No verbatim copies — this is study material, not a test bank reproduction.
3. **Distractor quality.** Wrong answers must be plausible and drawn from common student errors visible in the chapter quiz answer keys. No filler/random distractors.
4. **Explanations.** Every question has an `explanation` that names the underlying concept and references the source chapter when relevant. For T/F questions, explanations reference the truth value directly (per `docs/QUESTION_SCHEMA.md`).
5. **Topic slug consistency.** Reuse existing slugs from prior itd256 packs where the topic overlaps. Introduce new slugs sparingly and document the new ones in this design spec under "Open Questions" before the pack ships.
6. **Validation.** Pack must pass Quizzler's 6-tier validation per `docs/VALIDATION_RULES.md` before delivery.
7. **Manual smoke test.** After dropping the pack into `question-packs/itd256/`, run `./start.sh`, complete a 20-question session in the browser, confirm: no validation warnings in console, topic-slug grouping renders, every diagram displays correctly, explanations show after answers.

## Out of Scope

- Sub-packs (e.g., separate "ch11-only" quick-pick) — Quizzler already supports per-chapter quiz size selection from the pack list.
- Regeneration scripts — pack is one-time authored (`generation_mode: "manual"`).
- Modifications to the existing NextBook scraper.
- Raster/image diagrams.
- Coverage of Coronel ch15–16 — outside the syllabus's final exam scope.
- Updates to the existing 13 itd256 packs — rounds 1–11 and quiz2 stay as-is.

## Success Criteria

1. All 5 missing chapter quizzes (`ch1`, `ch2`, `ch3`, `ch4`, `ch14`) exist as `chN-quiz.md` in `~/Documents/ITD256/`, with question text, answers, and explanations where Canvas provided them.
2. `question-packs/itd256/round-12-final-ch1-14.json` exists with exactly 120 questions matching the type/difficulty/cluster distributions above.
3. Every question has source provenance in its `_comment` field.
4. 12 inline-SVG diagrams render correctly in-browser, allocated per the diagram table.
5. Pack passes Quizzler's 6-tier validation with no warnings.
6. Manual smoke test (20-question session in browser) completes with no errors and explanations visible after each answer.
7. ITD256 `HISTORY.md` and Quizzler `HISTORY.md` both updated with the work.

## Open Questions

None at design-approval time. Implementation-time decisions (specific topic-slug additions for ch11–14, exact source-quiz Q numbers per generated question) are deferred to the implementation plan.
