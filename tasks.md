# Quizzler Task Tracking

Status key: pending | in progress | done | blocked

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
- **Description:** After user's Monday final exam, consolidate or archive ITD 256 question packs per exam review and performance.
- **Blocked by:** user completing final exam
- **Tests:** manual verification of retained/archived content

### Task 4: Add dev/contributing section to README
- **Status:** pending
- **Description:** If missing, add section documenting `build_manifest.py` workflow and folder-based course registration for future contributors.
- **Tests:** manual verification that README covers dev setup and manifest regeneration
