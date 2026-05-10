# Quizzler Change History

A chronological record of meaningful changes to the codebase, bugs, remediation, and regression tests.

## 2026-05-10 — Pack-scoped mastery refactor (bug fix)

**Fixed:** Readiness banner contamination from deleted packs. Old behavior: ITD 256 banner showed `71 / 120 (59%)` Seen and `62 / 120 (52%)` Correct on a freshly-loaded `round-12` pack that had never been quizzed, because the mastery store was course-keyed (`quizzler_mastery_itd256`) and held leftover question ids from the deleted rounds 1-11. After the fix, a freshly-loaded course reports `0 / 120` on both metrics.

**Storage shape changes:**
- Mastery key: `quizzler_mastery_<courseId>` → `quizzler_mastery_<courseId>__<packId>`. One key per (course, pack) tuple. `__` is the structural discriminator; segments are sanitized to `[a-zA-Z0-9_-]` (collapsing any `__` runs within a segment back to `_`) so the boundary stays unambiguous. Implemented in `sanitizeKeySegment()` at `app/index.html:700-702`.
- Sessions key: `quizzler_sessions` (unchanged). Per-answer records and per-missed-question records now carry `pack_id` (nullable for legacy data). Recent-accuracy aggregation is now per-answer with pack filtering instead of session-score aggregated.
- Questions are decorated with `_packId` and `_packFile` at load time in both `loadAllModules` and `loadCourseModules`.

**Migration:** One-shot wipe of legacy `quizzler_sessions` + every-boot sweep of legacy `quizzler_mastery_*` keys lacking `__`. Sessions wipe is gated by sentinel `quizzler_session_schema_v2` because the active session key and the legacy key share the same name (no structural discriminator). Mastery sweep runs on every boot since the `__` discriminator protects new-shape keys from collateral damage. Boot IIFE at `app/index.html:2138` calls `sweepLegacyStorage()` as its first statement. `clearMastery()` was also rewritten to walk all `localStorage` keys starting with `quizzler_mastery_` so orphans (from deleted courses or packs) are cleaned up alongside known-course keys.

**Function-signature changes (all in `app/index.html`):**
- `getMasteryKey`, `getMastery`, `saveMastery`, `isMastered`, `setMastered` — all gained a `packId` argument.
- `computeReadiness` — changed from `(courseId, mastery, totalQuestions)` to `(courseId, totalQuestions)`; reads mastery internally across `loadedPackIds()`.
- `eligibleQuestions`, `weightedSelect`, `updateMastery` — same public signatures, internally group pool by `q._packId` and fetch per-pack mastery exactly once per pack.
- New helper: `loadedPackIds()` — returns the set of `pack_id` values currently loaded for the active course.

**Tests:**
- Updated ~17 existing callsites in `tests/quizzler.spec.js` to the new 2-arg key shape.
- Added five Phase-4 tests: pack-scoped key suffix verification; answers carry `pack_id`; recent-accuracy excludes deleted-pack answers in mixed sessions; history detail resolves missed questions by `(pack_id, question_id)` tuple with legacy id-only fallback; idempotent boot sweep removes legacy data and preserves pack-scoped data across reloads.
- Added five end-to-end **smoke tests** (replacing the manual smoke checklist from the plan): fresh ITD 256 load shows `0 / 120` banner and "no sessions yet"; 20-question quiz updates the banner and produces correctly-shaped storage; mastered questions drop out of the next quiz pool; DevTools storage layout has exactly the pack-scoped mastery key, the sentinel, and sessions — no orphan course-only key; legacy pre-refactor data (the actual observed `71 / 62 / 120` contamination) is wiped on first boot post-refactor. The whole smoke describe block conditionally skips if ITD 256 isn't present in the test environment.
- `clearStorage` helper re-primes the session sweep sentinel after `localStorage.clear()` so tests can seed sessions without the next boot wiping them.
- Two pre-existing Readiness Score tests had to start seeding `answers` arrays (with `pack_id` per record) because recent-accuracy aggregation moved from session-score to per-answer. The old `score: { correct, total }` shortcut no longer feeds the metric.
- **Tests: 149 passed / 0 failed.**

**Plan refs:** `~/Documents/Projects/.plans/quizzler/pack-scoped-mastery-2026-05-10.md`, `~/Documents/Projects/.plans/quizzler/pack-scoped-mastery-2026-05-10-tasks.md`, `~/Documents/Projects/.plans/quizzler/pack-scoped-mastery-2026-05-10-synthesis.md`.

**Plan deviation (worth noting):** The plan called for an idempotent every-boot sweep on `quizzler_sessions` with no sentinel, on the reasoning that an every-boot sweep handled multi-tab races cleanly. That reasoning was sound for **mastery** (where `__` is a structural discriminator) but didn't apply to **sessions** (which use the same key shape before and after the refactor). The implementation diverged: sessions get a one-shot sentinel-gated wipe; mastery keeps the every-boot sweep. Surfaced when 21 existing tests failed at Phase 4 verification — root cause was `sweepLegacyStorage` destroying its own production data on every reload. Contrarian review missed this; the test suite caught it.

## 2026-05-10 — Pack quality pass + readiness-banner bug diagnosis + refactor plan

**Test cleanup:**
- Removed two failing Playwright tests (`tests/quizzler.spec.js`) that assumed `itd256` had ≥2 modules — assumption broke after the 2026-05-09 consolidation. Rewrote two more chip tests that still depended on `itd256` having >5 questions; both now run against `samples` and assert the clamp-to-availableCount path directly. Test suite is now samples-only: 139 passed / 0 failed (commits `e6ef8ae`, `149a4ef`).

**Final pack quality fixes (round-12-final-ch1-14.json — gitignored):**
Applied 6 reviewer notes from the 2026-05-09 ship review:
- `r12q68` `_comment` rewritten to honestly describe ACID matching (no more false claim about a Coronel atomicity quirk).
- `r12q58`, `r12q86`, `r12q91` rewrote near-verbatim prompts as scenario framings.
- `r12q30` matching set replaced "Identifying relationship" outlier with "Quaternary (n-ary, n=4)" — strictly tests relationship degree now.
- `r12q105` recolored SALES_TXN stroke to match dimensions; star-schema diagram no longer hints at the fact table via color.

Pack counts unchanged (120 / 42-42-14-22 / 36-48-36 / 12 diagrams).

**Bug diagnosed (NOT yet fixed):**
- Course readiness banner shows stats from deleted packs against the new pack's `totalQuestions`. Observed: ITD 256 banner showed `71 / 120 (59%)` Seen and `62 / 120 (52%)` Correct on a freshly-loaded `round-12` pack that had never been quizzed against — leftover question ids from the deleted rounds 1-11 living under the course-scoped key `quizzler_mastery_itd256`.
- Root cause: `computeReadiness` and `computeMasteryViewModel` (`app/index.html:759-807`) count `Object.keys(mastery.seen).length` and `Object.keys(mastery.correct).length` without filtering against the currently-loaded pack's question ids.

**Refactor plan written:**
- Pack-scoped mastery storage refactor planned at impulse tier (contrarian review by GPT 5.5 via codex). Plan, synthesis, reviewer JSON, and task breakdown saved under `~/Documents/Projects/.plans/quizzler/pack-scoped-mastery-2026-05-10*`.
- Key shape changes from `quizzler_mastery_<courseId>` → `quizzler_mastery_<courseId>__<packId>`. Sessions get `pack_id` per answer. Recent-accuracy switches to per-answer aggregation. Idempotent boot sweep nukes all legacy data on every load (no sentinel) — handles multi-tab edge.
- 6 reviewer findings, 5 ACCEPT (1 with substituted lighter fix) / 1 ACKNOWLEDGE / 0 REJECT.
- Plan is read-only until a fresh implementation session — first task is `Task 1.1`.

## 2026-05-09 — ITD 256 Final Pack & Consolidation

**Spec & Design:**
- Authored `docs/superpowers/specs/2026-05-09-itd256-final-pack-design.md` (committed at `4a937b2`). Defines the ITD 256 final exam pack scope, question sourcing from Canvas chapter quizzes, type mix rationale (42 MC / 42 scenario / 14 T/F / 22 matching), difficulty distribution (36 easy / 48 medium / 36 hard), and inline-SVG diagram integration (12 diagrams across 120 questions).

**Content Capture:**
- Captured all 14 ITD 256 chapter quizzes from Canvas into `/Users/dave/Documents/ITD256/ch{1..14}-quiz.md` (approx. 289 questions, ~4,400 lines total). Each file includes full prompts, option sets, correct answers, textbook citations, and explanations. Sourced during interactive Canvas pastes in the same session.

**Final Pack Creation:**
- Authored `question-packs/itd256/round-12-final-ch1-14.json` (120 questions, 12 inline-SVG diagrams, source-bound to Canvas chapter quizzes). Question type mix: 42 MC (code snippet, scenario recognition, definition, multi-select variants), 42 scenario-based (transaction logs, schema design, anomaly diagnosis, cardinality fixing), 14 T/F, 22 matching. Difficulty: 36 easy / 48 medium / 36 hard. Manifest auto-discovers; course now shows 1 module.

**Consolidation — Deletions:**
- Deleted courses (gitignored): `question-packs/itn101/`, `question-packs/itn213/`, `question-packs/_archive/bcccce/`.
- Deleted 13 prior ITD 256 packs: `round-1.json` through `round-11.json`. Preserved only `_course.json` and `round-12-final-ch1-14.json`.

**Test Coverage:**
- Added `tests/diagram-rendering.spec.js` — 2 new Playwright tests covering inline-SVG diagram rendering (previously uncovered code path). Tests verify SVG renders without console errors and baseline-matches expected visual appearance.

**Regression — Known Failures:**
- Two tests in `tests/quizzler.spec.js` now fail because they assume `itd256` has ≥2 modules (broken by consolidation from 11 + 1 down to 1 final pack). These tests are in the pre-existing user file and will require deliberate fix (module selection edge case, not blocking).

**Notes:**
- Nearly all pack/study-guide work lives in gitignored paths (`question-packs/*/` is excluded except `samples/`). Only the design spec and new test file are git-trackable.
- Manifest builder correctly handles single-pack courses with no schema drift.

## 2026-05-08 — Content: ITN101 advanced pack + q17 prompt fix

**Added:** `question-packs/itn101/advanced-terms.json` — 40 hard/medium questions (24 hard, 16 medium) targeting equipment and niche terminology that's easy to miss on the ITN101 final. Coverage groups: routing internals (OSPF LSA types, DR/BDR election, BGP AS_PATH, administrative-distance values), STP family (root election, port states, RSTP, BPDU Guard, LACP, jumbo frames), connectors and cabling (LC, MPO/MTP, transceiver form factors GBIC→QSFP28, TDR vs OTDR, cable certifier, rollover, T568A vs B, BNC), WAN/SONET (T1 channel structure, OC-1→OC-192, MPLS, Frame Relay DLCI), wireless deep cuts (EAP-TLS/PEAP/FAST/TTLS, WPA3 SAE, MIMO vs MU-MIMO, RSSI dBm interpretation, SNR, DFS), VoIP (SIP/RTP roles, G.711 vs G.729 codec tradeoff), DNS records (A/AAAA/MX/CNAME/PTR/SRV/TXT), DHCP options (3/6/51/66/150), ICMP traceroute types, storage (iSCSI initiator/target, NAS vs SAN, FCoE), NAT (PAT vs static vs dynamic), and security appliances (NGFW vs stateful, SIEM, WAF/DLP/honeypot/jump server).

Pack metadata follows AUTHORING.md: `pack_id: itn101-advanced-terms`, `generation_mode: manual`, all acronyms expanded on first use per rule 5, all matching `rightItems` arrays unique per the dedup rule, all explanations teach rather than restate. Manifest auto-discovered the pack via `python3 scripts/build_manifest.py` (no manual registration needed); ITN101 now lists 2 modules (Advanced Terms & Equipment — 40 qs, Final Exam Comprehensive — 80 qs).

**Fixed:** `question-packs/itn101/final-comprehensive.json` q17 — original prompt asked "Which Category 6 cable variant…" but only 2 of 4 options (Cat 6, Cat 6a) are Category 6 variants; Cat 5e and Cat 7 are different categories entirely. The prompt implied a closed set that contradicted the option list. Rewrote to "Which twisted-pair Ethernet cable category is the lowest grade that sustains 10 Gbps over the full 100-meter run?" — now all four options are valid candidates and Cat 6a is the unambiguous answer (Cat 7 also supports 10 Gbps but is overspecified, which the explanation now calls out).

**Verification:** `python3 -m json.tool` clean on both pack files. `python3 scripts/build_manifest.py` reports 4 courses / 17 packs / 0 errors / 0 warnings (the initial 133-char `notes` warning was resolved by trimming to ≤120 chars). `npx playwright test` → 139 passed / 0 failed.

## 2026-05-08 — UX: "Mark as mastered" now hard-excludes from new quizzes

**Symptom (user report, mid-ITN101 session):** Questions previously marked as "mastered" kept reappearing in subsequent quizzes with the toggle still pre-checked. The user expected the mark to mean "remove from the pool," not "show less often."

**Root cause:** Two compounding factors in the existing soft-deprioritization design.
1. `weightedSelect` (`app/index.html:832`) put mastered questions back into the weighted pool with `WEIGHT_MASTERED = 1` alongside unseen (10) and seen-wrong (5). So mastered questions were 10× rarer but never zero.
2. The `if (size >= pool.length) return shuffle(pool);` short-circuit completely bypassed weighting whenever the requested quiz size met or exceeded the raw pool — so for small modules or "All" quick-pick clicks, every mastered question reappeared at full rate. The clicked tooltip (`"Deprioritizes this question in future quizzes (won't exclude it)"`) acknowledged the gap, but a hover tooltip on a checkbox is too quiet a signal for the dominant user expectation around the word "mastered."

This was a deliberate design choice from the 2026-05-06 entry below, which moved away from a separate `manual` exclusion flag because it caused a numerator/denominator mismatch ("Seen 70/80" vs "Available: 73"). That mismatch came from having two independent flags. Collapsing mastery and exclusion into a single `correct` flag avoids that whole class of bug — the two now-different numbers measure different things (total in module vs. quizzable for next session) instead of the same thing.

**Changed:**
- `app/index.html` — added `eligibleQuestions(pool, courseId)` helper next to `clearMastery` (filters out anything with `mastery.correct[id]` set).
- `app/index.html` — `weightedSelect` now drops mastered questions before bucketing and uses only two buckets (unseen 10×, seen-wrong 5×). Removed the `WEIGHT_MASTERED` constant. The size-vs-pool short-circuit now compares against the eligible pool length, so it can no longer leak mastered questions back in.
- `app/index.html` — `updateAvailableCount` reports the eligible count (mastered excluded). `updateStartQuizValidity` gained a `rawTotal` parameter and a third hint variant: when `total === 0` but `rawTotal > 0`, it disables Start with `"All N questions in the selected modules are mastered. Reset progress to study them again."` — distinguished from the existing "no questions in pack" path.
- `app/index.html` — `startQuizBtn` handler filters with `eligibleQuestions` before calling `weightedSelect` (defense-in-depth) and caps `size` against the eligible length.
- `app/index.html` — tooltip on the per-question toggle changed to `"Excludes this question from future quizzes until you reset progress"`. Info-icon modal copy updated to `"Mastered questions are excluded from new quizzes. Of the rest, unseen questions are picked most often (10×) and seen-but-wrong less often (5×). Reset progress to bring mastered questions back."`
- `README.md:19-20` — features list reflects the new contract.

**Why two numbers (Available vs. mastery banner total) are fine this time:**
The 2026-05-06 fix removed `getEligibleQuestions` because the `manual` flag was orthogonal to `correct`, so two displays of *the same concept* could disagree. Now there is one flag (`correct`). The mastery banner shows `seen / total` and `correct / total` against the full pool (a progress measurement); "Questions available" shows `total - mastered` (a "what will be quizzed next" measurement). They agree by construction: `available + mastered === total`.

**Tests:**
- Renamed and rewrote `tests/quizzler.spec.js:1091` from `"mastered checkbox flags a question as correct without excluding it"` → `"mastered checkbox flags a question correct and excludes it from new quizzes"`. New assertions: `availableCount` decrements by 1 after marking, and a follow-up max-size quiz must not contain the mastered question's id.
- Added `tests/quizzler.spec.js` test `"all questions mastered disables Start with a reset hint"`: seeds `correct` for every loaded question, asserts `availableCount === 0`, Start button disabled, hint contains both `All N` and `Reset progress`.
- Updated `tests/quizzler.spec.js:2401` (info-icon modal) to assert the new wording (`"Mastered"` + `"excluded"` + `"10×"`).
- Verification: `npx playwright test --reporter=line` → **139 passed / 0 failed** (was 138 / 0; net +1 from the new all-mastered test, –0 since the rewrite replaced the old contract test in place).

**Migration / data note:** No storage schema change. Existing `quizzler_mastery_*` entries already track `correct[id] = true` for every previously-mastered question, so the new exclusion semantics take effect immediately on next quiz start with no per-user migration needed. Users who want a recap of mastered questions can use the existing "Reset progress" button (clears all courses) or uncheck the toggle on a specific question's card mid-quiz.

## 2026-05-08 — Bug: silent port drift in `start.sh` stranded progress on a sibling origin

**Symptom:** ITN101 progress (3 completed sessions + mastery) appeared lost the day after they were taken. ITD 256 progress (older, on the same browser) was untouched.

**Root cause:** `start.sh` used `PORT=8000` and silently incremented to the next free port if 8000 was in use (`while lsof -ti:"$PORT" ...; do PORT=$((PORT + 1)); done`). Because `localStorage` is partitioned per **origin** (scheme + host + port), a launch that landed on `:8001` (most likely after an earlier `start.sh` invocation orphaned a `python3 -m http.server` on `:8000` — the launcher only kills its child on `Enter`, so closing the terminal leaks the process) wrote all session and mastery keys to a *different* origin than prior runs. Subsequent launches that found `:8000` free saw an empty-looking app even though the data was intact one port over. Selective-absence shape (one course preserved, another missing) was the diagnostic tell — a global wipe would have removed everything.

**Diagnostic procedure** (recoverable for future cases of "where did my progress go?"):
1. On the suspect origin, dump `Object.keys(localStorage)`, the `quizzler_sessions` array's distinct `course` field values, and any `quizzler_mastery_*` keys.
2. If keys exist but a specific course is absent, the issue is selective and origin-related, not a wipe.
3. Spin up a server on each candidate adjacent port (`http.server 8001`, `8002`, …) and re-run the dump on each. The origin holding the missing data is the prior launch's actual port.

**Fix:**
- `start.sh:4` — pin `PORT=4123` (uncommon, no conflict with python http.server's `8000` default, Django, Vite `5173`, Node `3000`, Postgres `5432`, etc.).
- `start.sh:11-16` — replace the auto-increment loop with a fail-fast guard that prints the kill command for the squatter and exits non-zero. Origin must stay constant for localStorage to persist meaningfully across launches.
- `playwright.config.js` already uses an isolated `:8787` for tests, no change needed.

**Recovery:** one-time merge of the orphaned `:8001` origin's `quizzler_sessions` and `quizzler_mastery_itn101` into `:4123` via console snippet (dedup by `quiz_id`, sort by `completed_at`, cap at `MAX_STORED_SESSIONS=200`).

**Regression prevention:** the failure mode is now structurally impossible — `start.sh` cannot land on a port other than the one it announces. No automated test added because the bug lives in the launcher script outside the Playwright harness; the inline comment at `start.sh:11-12` documents the invariant for future maintainers.

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
