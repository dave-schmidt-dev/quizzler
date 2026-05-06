# Quizzler Change History

A chronological record of meaningful changes to the codebase, bugs, remediation, and regression tests.

## 2026-05-06 — Quiz Elapsed-Time Timer

**Added:**
- Live elapsed-time readout in the sticky `progress-strip` on the quiz screen, ticking once per second from `0:00`. Switches to `H:MM:SS` once a quiz exceeds 60 minutes.
- "Time: M:SS" line appended to the results bar on completion (hidden until then).
- Persisted session report now carries `started_at` (ISO 8601) and `duration_ms` (number) alongside the existing `completed_at`. Wall-clock duration is `quizCompletedAt - quizStartedAt` from `Date.now()` snapshots, independent of `setInterval` drift.
- Session History list shows the run's duration inline next to the date for sessions that have one. Legacy sessions saved before this change render unchanged (the field is omitted, not faked).
- 13 new Playwright cases in section 23 ("Quiz Timer") covering: visible-and-zero on start, ticks upward, freezes on completion, results-bar line appears, persisted `started_at`/`duration_ms` shape and consistency, history rendering for both new and legacy sessions, timer reset on a second quiz, `formatDuration` boundary inputs (null / undefined / NaN / negative all return `"0:00"`), and hour-format display in history. Orphan-interval guard parameterized over both reachable mid-quiz exit paths (`#backToConfig`, `#returnToSelectionBtn`) asserting timer is cleared and stopped advancing post-exit.

**Changed:**
- `buildSessionReport` now stores `completed_at` from the already-captured `quizCompletedAt` snapshot (instead of re-reading `Date.now()`), making `duration_ms === completed_at - started_at` exact rather than off by 0–few ms.
- `returnToSelectionBtn`, `backToCourses`, and `backToConfig` handlers now call `stopQuizTimer()` before navigating, preventing an orphan `setInterval` from ticking against a hidden `#statElapsed` node when the user leaves a quiz mid-attempt.

**Fixed (post-review):**
- `formatDuration(NaN)` was returning `"NaN:NaN"` because the guard `ms == null || ms < 0` does not catch `NaN`. Hardened to `ms == null || Number.isNaN(ms) || ms < 0` (returns `"0:00"`).

**Notes:**
- No "exam mode" / countdown / pause-on-modal behavior — Quizzler is a study tool, not a timed exam, so `duration_ms = completed_at - started_at` is treated as an invariant.
- `tabular-nums` applied to the live timer and history duration so digit-width changes (`1:09 → 1:10`) don't shift surrounding layout.

**Verification:**
- `npx playwright test`: 91 passed / 0 failed / 3 skipped (was 78 / 0 / 3 before this change; +13 from the new section).

## 2026-05-06 — Manifest-based Auto-Discovery for Question Packs

**Added:**
- `scripts/build_manifest.py` — walks `question-packs/` directory and generates `question-packs/manifest.json` with auto-discovered courses and packs.
- `_course.json` support in question-pack folders. Optional metadata file with `id`, `name`, `description`, and `sort_order`. Falls back to folder-name derivation if absent. Skips folders starting with `.` or `_`.
- New `_course.json` files for all four course folders (samples, itd256, bcccce, itn213). Samples committed; others gitignored per existing rules.
- ITN 213 comprehensive final exam pack at `question-packs/itn213/final-comprehensive.json` (80 questions, modules 1–11 + financial modeling).

**Changed:**
- `app/index.html` no longer has hard-coded `COURSES` array. Now async-fetches `question-packs/manifest.json` with fallback error handling.
- `start.sh` runs `build_manifest.py` before starting local server.
- `playwright.config.js` `webServer.command` runs build script before test server.
- `README.md` and `question-packs/AUTHORING.md` updated to reflect new folder-based workflow (removed manual COURSES registration step).

**Verification:**
- Test suite: 78 passed / 0 failed / 3 skipped.
- Readiness test fixed by adding `sort_order` to samples course to maintain first-place order.

**Added (2026-05-06, commit 882faec):**
- Build script now validates `notes` field length (max 120 chars) and warns during manifest generation if a pack's notes exceed this limit. Notes appear as the subtitle on the home-screen course card and are truncated in the UI beyond 120 characters.
- Updated `pack-template.json` and `question-packs/AUTHORING.md` to document the 120-char limit and rationale.

**Added (2026-05-06, post-ship review):**
- Build script now warns when a question prompt contains sequential-coupling phrases ("Same X scenario:", "in the previous question", "as discussed earlier", etc.). The engine randomizes question order, so any prompt that references a prior question breaks for the user when the follow-up draws first. AUTHORING.md gains quality rule #11 codifying the standalone-question requirement.
- New Python test suite at `tests/test_build_manifest.py` (15 cases via `unittest`). Covers folder-name fallback, malformed `_course.json`, malformed pack JSON, natural-sort filename ordering, notes length boundary, sequential-coupling warning, dot/underscore folder skip, sort-order ties, and missing `PACKS_DIR`. Run with `python3 -m unittest tests.test_build_manifest -v`.

**Fixed (2026-05-06, post-ship review):**
- XSS hardening in `app/index.html`: `renderHome` and the module-row template now run `c.name`, `c.description`, `mod.title`, `mod.description`, `c.id`, and `mod.file` through the existing `escapeHtml` helper, matching the convention used elsewhere in the file.
- ITN 213 pack: rewrote q63-q80 as standalone prompts. Earlier draft used "Same hospital scenario:", "Same Instagram-like platform:", etc. — sequential coupling that breaks under random order. Each scenario sub-question now restates its own setup.
- Comment in `scripts/build_manifest.py` corrected from `_archived` to `_archive` to match the actual folder name in use.
