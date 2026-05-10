# Quizzler Task Tracking

Status key: pending | in progress | done | blocked

## [2026-05-09 → 2026-05-11] — ITD256 final prep schedule

ITN101 final is during class Saturday 5/9 morning; ITD256 final is Monday 5/11 evening. Two-and-change days between the exams for ITD256 prep. ITD256 already has 13 packs registered (`round-1` → `round-11`, `quiz2-ch7-10`, `round-1-followup`), so the goal is targeted reinforcement, not from-scratch coverage.

### Task 1: Take ITN101 final
- **Status:** pending
- **When:** Sat 2026-05-09, in class
- **Done when:** Exam complete.

### Task 2: Surface weak ITD256 topics
- **Status:** pending
- **When:** Sat 2026-05-09, post-exam (afternoon)
- **Description:** Take one comprehensive quiz across all 13 ITD256 modules. Mastered-exclusion (from today's UX fix) drops correctly-answered questions out of subsequent quizzes automatically; remaining pool *is* the weak-topic list. Read `topic` slugs off missed-question history rows.
- **Done when:** Have a short list of weak `topic` slugs (kebab-case, e.g. `partial-dependency`, `normalization-3nf`) ready to hand to assistant.

### Task 3: Generate targeted ITD256 pack
- **Status:** pending
- **When:** Sat 2026-05-09 evening or Sun 2026-05-10
- **Description:** Hand weak-topic list to assistant; new pack at `question-packs/itd256/<name>.json` modeled on `itn101/advanced-terms.json` (~40 hard-weighted questions, full acronym expansion, matching `rightItems` unique, ≤120-char `notes`). Run `python3 scripts/build_manifest.py` (or `./start.sh`) — manifest auto-discovery, no code edits required.
- **Done when:** Pack JSON valid, manifest builder reports 0 warnings, new module shows up under ITD256 in the app.

### Task 4: Iterate quizzes on remaining weaknesses
- **Status:** pending
- **When:** Sun 2026-05-10 → Mon 2026-05-11 daytime
- **Description:** Run quizzes against the new targeted pack + selected existing rounds. Each session narrows the pool further (mastered drops out). Use "Reset progress" only for a final full-coverage recap pass before the exam if desired.
- **Done when:** Mastery banner shows acceptable coverage on weak topics; recent-accuracy trend acceptable.

### Task 5: Take ITD256 final
- **Status:** pending
- **When:** Mon 2026-05-11 evening
- **Done when:** Exam complete.

## [2026-05-07] — UX Overhaul

### Task 1: Execute the UX Overhaul plan
- **Status:** done
- **Description:** Implemented `~/Documents/Projects/.plans/quizzler/ux-overhaul-2026-05-06-tasks.md` — 19 tasks across 5 phases (semantic + a11y / aesthetic refresh / IA + flow / modals + polish / content + docs). Six contrarian-review constraints (CR-1 through CR-6) all respected.
- **Done when:**
  - Phase 1: course cards / module rows / tabs / retry rows now use semantic interactives; `:focus-visible`, `<main>`, dynamic `<title>`, meta description, favicon, and `prefers-reduced-motion` all in place
  - Phase 2: zero gradients / zero `backdrop-filter` / zero hover-translate; active tab differentiated from primary CTA; visual-regression baselines captured
  - Phase 3: course-name de-dup, course question count, filename-derived module grouping (anchored to prefix after a substring-bleed bug was caught by the unit test), score color tier, quick-pick chips with bidirectional sync, three-action results bar, drillable history with runtime question lookup + missing-question fallback
  - Phase 4: zero `alert()`/`confirm()` calls remain in `app/index.html` (replaced by `showAlert`/`showConfirm` + inline validation); mastery checkbox hidden until answered; info icon explaining weighted selection; empty Retry Missed CTA; readiness next-step copy per band
  - Phase 5: `quiz2-ch7-10.json` notes trimmed to 92 chars (manifest builder warns 0); ARCHITECTURE.md / README.md / HISTORY.md / tasks.md current
  - Tests: 137 Playwright passing / 0 failed / 0 skipped (was 93 / 0 / 3); 15 manifest-builder tests passing
  - Suite is now skip-free — three pre-existing tests that conditionally skipped on the default course (no fixtures to satisfy them) were deleted because they covered behavior that wasn't deterministically reachable in CI



## [2026-05-06] — Decouple mastery from exclusion

### Task 1: Fix mastery numerator/denominator mismatch
- **Status:** done
- **Description:** User reported "Seen 70/80" but "Questions available: 73" — the 7-question gap was manually-mastered (and silently hidden) questions. The bug was the `manual` flag conflating mastery with quiz exclusion. Decouple them so mastered questions are deprioritized, not excluded.
- **Done when:**
  - `manual` flag removed from schema, `getMastery` no longer reads it
  - `setManualMastery` → `setMastered`, `isManuallyMastered` → `isMastered`
  - Checkbox label updated to "Mark as mastered" (no "hide" language)
  - `getEligibleQuestions` deleted; all three call sites (`updateAvailableCount`, `startQuizBtn` handler, `loadRetryQuestions`) use raw pool
  - Weighted selector (`correct: true` weight 1 vs. unseen weight 10) is sole deprioritization mechanism
  - Implicit localStorage migration: legacy `manual: {qX: true}` entries pruned on next mastery write
  - Two prior mastery tests rewritten to pin "flags correct, stays eligible" contract
  - Two new tests added: legacy `manual` field migration, retry-missed including a mastered question
  - Tests: 93 passed / 0 failed / 3 skipped (was 91 / 0 / 3; +2 new)

## [2026-05-06] — Quiz Elapsed-Time Timer

### Task 1: Implement elapsed-time timer with session persistence
- **Status:** done
- **Description:** Add live M:SS (or H:MM:SS >60min) timer in the progress strip, persist `started_at` and `duration_ms` to session reports, and show duration in the session history list.
- **Done when:**
  - Live timer ticks from 0:00 once per second on the quiz screen
  - Timer freezes on completion, "Time: …" line shows on results bar
  - `started_at` (ISO 8601) and `duration_ms` (number) persisted to session report
  - Session history displays duration inline for new sessions; legacy sessions unchanged
  - All "leave quiz" handlers (`backToConfig`, `backToCourses`, `returnToSelectionBtn`) call `stopQuizTimer()` to prevent orphan intervals
  - `formatDuration` hardened against `NaN`, `null`, `undefined`, and negative inputs
  - `buildSessionReport` uses captured `quizCompletedAt` snapshot for exact `duration_ms === completed_at - started_at`
  - 13 Playwright cases passing (orphan-interval guard, boundary inputs, history rendering, etc.)
  - Tests: 91 passed / 0 failed / 3 skipped

## [2026-05-06] — Manifest Auto-Discovery

### Task 1: Implement manifest-based auto-discovery
- **Status:** done
- **Description:** Replace hard-coded COURSES array with auto-generated manifest.json from question-packs folder walk.
- **Done when:**
  - `build_manifest.py` written and functional
  - `app/index.html` async-fetches manifest with fallback
  - `start.sh` and `playwright.config.js` run build before server start
  - All tests passing (78/78, 3 skipped)
  - Documentation updated

### Task 2: Add adaptive features and weak-topic round generation
- **Status:** pending
- **Description:** Revisit auto-rescan in dev mode, weak-topic round generation, and cross-pack mastery view per user discussion.
- **Blocked by:** none
- **Tests:** to be determined per feature design

### Task 3: Clean up ITD 256 packs after final exam
- **Status:** pending
- **Description:** After user's Monday final exam, consolidate or archive ITD 256 question packs per exam review and performance. **Now also handles overlap dedupe** — the UX Overhaul plan punted UI-side dedupe of "Quiz 2 - Chapters 7-10" combined pack vs. individual Ch 7/8/9/10 packs to this content-cleanup pass (per CR-2 contrarian finding: schema additions to `_course.json` would have been silently dropped by `build_manifest.py`). Cleanest path: archive the combined pack and rely on per-chapter modules.
- **Blocked by:** user completing final exam
- **Tests:** manual verification of retained/archived content; after cleanup, verify config screen shows no double-counted question pool

### Task 4: Add dev/contributing section to README
- **Status:** pending
- **Description:** If missing, add section documenting `build_manifest.py` workflow and folder-based course registration for future contributors.
- **Tests:** manual verification that README covers dev setup and manifest regeneration
