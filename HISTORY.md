# Quizzler Change History

A chronological record of meaningful changes to the codebase, bugs, remediation, and regression tests.

## 2026-05-07 — UX Overhaul (5 phases, full plan executed)

**Plan:** `~/Documents/Projects/.plans/quizzler/ux-overhaul-2026-05-06.md` (refined post-contrarian-review). 19 tasks. Six contrarian findings (CR-1 through CR-6) all honored.

**Phase 1 — Semantic foundation + a11y** (`e5e98bc` + test backfill `99900eb`):
- Course cards, module rows, tabs, and retry-missed rows became semantic interactives. Module rows are `<label>` wrapping the existing checkbox (not `<button>` — a button can't legally contain a focusable child) — same shape the retry-missed list was already using; the per-row click handler was removed since the native label/input pair handles toggling.
- `:focus-visible` outlines (2px in `--accent-2`) replace the suppressed default `:focus`. `<main class="wrap">` landmark; explicit `for=` labels; `aria-label="Match for ${left}"` on matching selects; `<meta name="description">`; favicon (Q glyph SVG); `prefers-reduced-motion` block.
- Dynamic `<title>` per screen (home / config with course name / quiz with `Q3/10` progress / completion with score / history). The in-progress title call is guarded by `if (!quizCompletedAt)` because `updateProgress` runs *after* `checkCompletion` in the answer-handler chain — without the guard the completion title is clobbered on the final answer.
- Defense-in-depth: retry-missed row labels go through `escapeHtml()` (the prior unescaped `${s.title}` interpolation was a small XSS path since `s.title` ultimately comes from session storage).
- Test backfill replaced manual gates with automation: `<main>` count = 1, meta description non-empty, favicon resource resolves (no 404), full quiz flow produces zero `console.error`s, `prefers-reduced-motion` stylesheet zeros transitions and hover transforms, and `aria-selected` stays in sync with tab activation.

**Phase 2 — Aesthetic refresh** (`3ce8262`):
- Body, progress fill, and primary/secondary/danger buttons all became flat fills (`var(--accent-2)` / `#64748b` / `var(--bad)`). Zero `linear-gradient` or `radial-gradient` substring remains in the stylesheet.
- Hover-lift `transform: translateY(...)` removed from `.course-card`, `label.choice`, `.tf-btn`. `backdrop-filter` dropped on `.progress-strip` (now solid `--panel-2`) and `.modal` (rgba opacity bumped 0.76 → 0.86 to keep the dim readable).
- Active tab moved from cyan pill to transparent label with a 2px cyan underline so it no longer visually competes with primary CTAs.
- `.panel.hero` gained tighter padding (18/22 vs. 22/22) and reduced `.eyebrow` / `h1` margins on hero panels.
- Visual-regression baselines captured for home / config / quiz-mid / quiz-complete using `expect(page).toHaveScreenshot()` with `maxDiffPixelRatio: 0.02`. The implementer added a Mulberry32 PRNG seed via `page.addInitScript` so question-selection randomness doesn't churn the diffs above tolerance — seeding is test-only, not a runtime change.

**Phase 3 — IA + flow** (`727bf8b`):
- Course-name de-dup: home cards drop the eyebrow; config hero uses the existing `description` field (no `_course.json` schema change — the manifest builder filters fields to a fixed allowlist, so any addition would be silently dropped); history rows drop the course-id prefix.
- Course cards now show `${modules} modules · ${total} questions` (manifest field is `questionCount`, not `count`).
- Module list groups by filename prefix — Original rounds / Chapter packs / Combined exams / Modules. Anchored to prefix because an earlier substring-style implementation bucketed `quiz2-ch7-10.json` as a "Chapter pack" (the embedded `ch7` substring matched first); the new unit test for `moduleGroupLabel` caught it before it shipped.
- Score colors split into `score-good` / `score-mid` / `score-poor` by tier (≥85% / 50–84% / <50%), applied at completion and across history.
- Quick-pick chips (10 / 20 / 50 / All) call a direct `setQuizSize()` helper — chips do *not* dispatch input events because `#quizSize` had no listener that would have processed them. `syncQuickPickChips()` is wired to the input's `input` event so chips track when the user types directly, and to the end of `updateAvailableCount` so chip state stays consistent after module-toggle changes.
- Post-quiz bar replaces the single button with three: Retry missed (disabled at 100%; reuses the missed-question loader by passing an inline session-shaped object), Start another (preserves selections, focuses Start), Back to Course.
- History rows became `<details>` with native keyboard support. On expand they look up missed-question prompts/explanations against the loaded packs; if the session belongs to a course not currently loaded, a new `loadCourseModules(courseId)` helper (factored from `loadAllModules` with no UI side effects) hydrates the per-course pack into a `courseModuleCache` Map. Missing question ids fall back to the persisted topic/chapter/picked/correct fields with a `<em>Question removed from pack</em>` marker so the row never breaks.
- Three pre-existing skipped tests deleted — they conditionally `test.skip()`'d on the default course (`samples`, 1 module / 5 questions) because they needed ≥2/3 modules or ≥20 questions to be meaningful. A test that doesn't run in CI is dead code; per CLAUDE.md "Never skip tests. Don't test dead code." The test that depended on `itd256` (gitignored) was rewritten as a unit test of `moduleGroupLabel` against synthetic filenames so it runs everywhere.

**Phase 4 — Modals + microcopy + polish** (`96f7489`):
- All native `alert()` and `confirm()` calls replaced with `showAlert(title, body)` / `showConfirm(title, body) → Promise<boolean>` helpers backed by a new `#dialogModal` (reuses Phase 2 aesthetic — flat colors, no blur).
- Inline validation: Start Quiz is `disabled` with a hint line below it whenever 0 modules are selected (no more native dialog); Check Matches is `disabled` until every `<select>` in its card has a non-default value.
- Mastery affordance: card-meta is `hidden` on initial render; `showFeedback` removes the hidden attribute on the answered card so the "Mark as mastered" toggle only appears post-engagement. Toggle gets a hover tooltip: "Deprioritizes this question in future quizzes (won't exclude it)".
- New info-icon next to "Questions available" on the config screen opens the dialog modal explaining weighted selection (unseen 10× / seen-but-wrong 5× / already-correct 1×).
- Empty Retry Missed state gets a heading, body, and a "Build a quiz instead" CTA that switches the tab back.
- Readiness banner gains a per-band `nextStep` hint: <40% → "Start any module to begin tracking." through 95%+ → "All set. Run a fresh quiz to keep skills sharp."
- 7 new gate tests (no native dialog ever fires during clear-history; inline validation contracts; mastery delayed reveal; info icon modal content; empty-state CTA; per-band next-step copy). 6 existing tests adapted to the new contracts without introducing any skips.

**Phase 5 — Content + docs** (this commit):
- `question-packs/itd256/quiz2-ch7-10.json` notes trimmed from 158 → 92 chars; manifest builder warns 0.
- ARCHITECTURE.md / README.md / tasks.md updated to current behavior.

**Pre-implementation baseline (captured against `main` before Phase 1):**
- Lighthouse desktop: a11y 96 / BP 100 / SEO 90 / Agentic 100. Two fails: `landmark-one-main`, `meta-description`.
- Console: 1 a11y issue ("No label associated with a form field"), 1 favicon 404.
- Playwright: 93 passed / 0 failed / 3 skipped.
- Manifest builder warns: 1.

**Post-implementation:**
- Playwright: 137 passed / 0 failed / 0 skipped.
- Manifest builder warns: 0.
- Manual Lighthouse "agentic" score not re-captured (DevTools-only metric, not a phase blocker since the Lighthouse-equivalent assertions are now automated in Playwright).
- Both Phase 0 Lighthouse fails (`landmark-one-main`, `meta-description`) are now structurally satisfied by markup. Console issues in the home → quiz → history flow drop to 0 (covered by an automated test).

**Constraint compliance:** zero new dependencies; localStorage shape unchanged (legacy sessions still render); all-on-one-page quiz layout preserved.

**Tooling note:** During the original contrarian review (planning session), `codex exec` (CLI v0.128.0) failed to flush JSON to stdout despite emitting `task_complete`. Recovered from `~/.codex/sessions/.../rollout-*.jsonl` via `last_agent_message`. Implementation work in this session used Claude subagents only (no codex dispatches).

## 2026-05-06 — Decouple "Mark as mastered" from quiz exclusion

**Fixed:**
- Mastery numerator/denominator mismatch: user saw "Seen 70/80" (denominator: full pool) but "Questions available: 73" (denominator: pool minus manually-hidden). Root cause was `manual` flag conflating two operations: mastery marking and quiz exclusion.

**Changed:**
- Removed `manual` flag from mastery schema. `getMastery` no longer returns or reads it.
- Renamed `setManualMastery` → `setMastered` and `isManuallyMastered` → `isMastered`. The toggle now writes only `seen` + `correct` (unchecking deletes `correct`).
- Checkbox label: `"Mark as mastered and hide from future quizzes"` → `"Mark as mastered"`.
- Removed `mastered-note` span and its CSS rule (the label itself now fully describes the operation).

**Removed:**
- Deleted `getEligibleQuestions` dead code. All three call sites (`updateAvailableCount`, `startQuizBtn` handler, `loadRetryQuestions`) now use the raw pool — every question is eligible for selection. Mastered questions are deprioritized only by the weighted selector (weight 1 for `correct: true` vs. weight 10 for unseen).

**Notes:**
- Implicit localStorage migration: old `manual: {qX: true}` entries continue to work because every previously manually-mastered question already has `correct: true` set. On the next mastery write, the legacy `manual` field is pruned from storage.
- Numbers now always agree because they use the same denominator (full pool) and both the mastery banner and "Questions available" respect only the `correct` flag as the signal for deprioritization, not exclusion.

**Verification:**
- `npx playwright test`: 93 passed / 0 failed / 3 skipped (was 91 / 0 / 3 before this change; +2 new tests for legacy migration and retry-missed with mastered question). Two prior tests in section 21 ("Mastery Tracking") were rewritten in place to pin the new "flags correct, stays eligible" contract.

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
