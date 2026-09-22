# Quizzler iOS redesign — implementation plan

- **Date:** 2026-09-22
- **Design source:** `docs/UI-REDESIGN-2026-09-22.md` (owner-approved 2026-09-22, including
  the supersession of `app/design-authority-manifest.json` and the dropping of M5).
- **Status:** M1 landed (`bf7178b`). M2–M4 below.

## Resequencing, and why

The design doc's M2/M3/M4 split is a reviewing order, not a build order. Taken literally it
rewrites `LaunchpadView.swift` three times: M2 restructures navigation, M3 adds the session
boundary, M4 changes what questions the session contains. M3's session code would be written
against pack-order and then rewritten by M4.

So the engine moves first:

| Phase | Scope | Surface | Verified by |
|---|---|---|---|
| **P1** (was M4) | `StudySession` queue builder in QuizzlerKit: fixed length, due-review, retry-missed, weak-area weighting | no UI | `swift test`, seconds |
| **P2** (was M2+M3) | TabView + Today + Progress + full-screen session + summary, consuming P1 | UI | native gate, ~5 min |

Two phases, one rewrite of the view, and the selection rules — the part with real behaviour
to get wrong — are pinned by fast tests before any UI depends on them.

## P1 — `StudySession`

New `app/QuizzlerKit/Sources/QuizzlerKit/Study/StudySession.swift` + tests. Pure: no I/O, no
implicit `Date()`, catalog and envelope passed in. The entry point takes the kit's existing
`SelectionRequest(mode:limit:)` — calling it is the point, since the design finding was that
this contract exists and is never invoked.

### Modes

`.normal` stays **pack order from the resume index**. This is not a style choice: 
`QuizWorkflowUITests.testAnsweringAQuestionMovesTheCourseForwardAcrossARelaunch` asserts that
answering one question advances `today-position` by exactly one across a relaunch, wrapping at
the end of the pack. D2 and that test are the same constraint.

Weak-area weighting therefore gets its **own** mode, `.weakAreas` (`"weak_areas"`).
`SelectionModeTests` pins only the three existing rawValues, so adding a case is additive.

- `.normal` — `length` questions in pack order starting at the resume index, wrapping.
- `.srs` — SRS-due (`nextDueAt <= now`), most overdue first. Empty when nothing is due; the
  caller decides what to offer instead of inventing a question.
- `.retryMissed` — `StudyInsights.missedQueue(limit:)`, already specified and tested.
- `.weakAreas` — **round-robin across areas, weakest area first**, not a strict sort. A strict
  sort by area accuracy would serve one area exclusively until it stopped being the weakest;
  the owner asked for weak questions to *surface more often*, not to crowd everything else
  out. Within an area: unseen questions first, then lowest accuracy, then
  `identity.description`. Deterministic throughout — a shuffled queue cannot be tested and
  cannot be resumed.

### Every mode

- The cap is on the batch, never on the queue. INV-3: *"Prevents retry-missed, normal quizzes,
  and future SRS due-review mode from silently hiding questions that the selected mode
  promises to show."* INV-6: *"due or overdue questions must remain visible in SRS mode unless
  the user resets progress or explicitly changes their state."* Both invariants are gated on
  the browser app (`tests/srs-gates.spec.js`); the principle governs here regardless. Taking
  10 of 40 due questions is a batch; dropping the other 30 from the queue is not allowed.
- Questions already answered in the live session are excluded from a fresh plan, so a retry
  cannot re-serve what the user just did.

Named check: `StudySessionTests` — pack-order wrap and resume, due ordering and the empty-due
case, retry delegation, weak-area round-robin (specifically that it does *not* serve one area
exclusively), the batch cap, pending exclusion, empty catalog, and `limit <= 0` throwing
through `SelectionRequest`.

## P2 — the view

Two dispatches: a full `LaunchpadView` rewrite plus new views plus UI-test updates plus the
manifest will not fit one 30-minute bounded task whose check has to run `xcodegen` and then
`xcodebuild build-for-testing`.

- **P2a** — TabView/NavigationStack, Today rows, Progress screen consuming `StudyInsights`,
  sync chip.
- **P2b** — session `fullScreenCover`, summary, retry-missed, making `.results` reachable and
  `ResultsView` live code.

Fixed constraints carried from the design doc:

- **D1** all eight `persistenceStatus` strings stay verbatim; status moves to a caption chip.
  The `"answer checked · "` prefix is dropped (asserted by nothing).
- **D2** "Continue" stays pack-order — see the relaunch test above.
- **D3** tab labels stay "Today", "Progress", "Settings".
- Exam areas never appear in the UI. Weakness shows up as *which questions are served*.

### Asserted contracts that must survive

From `QuizWorkflowUITests` and `AccessibilityUITests` against the real app (the
`QUIZZLER_UI_TEST_FIXTURE` tests drive a separate fixture screen and are unaffected):

| Contract | Shape |
|---|---|
| Today eyebrow | a static text beginning `"TODAY · "` with a non-empty course name |
| `today-position` | `^Question \d+ of \d+$`, advances by one across a relaunch, wraps |
| `today-score` | exists |
| `question-qid` | label `^Question ID [^:]+::[^:]+$` (INV-2, pack-scoped) |
| `question-shell` | reachable from "Start review" |
| buttons | "Start review", "View progress", "Settings", "Check Answer", "Next question", "Report" |
| sync strings | "local progress saved", "progress synced", "progress saved here · sync pending" |
| `course-selector` | reachable from Settings |

Tests this phase must update, deliberately, as part of the change:

- `AccessibilityUITests:52` — `staticTexts["PROGRESS"]` becomes a navigation title.
- `QuestionShellTests:22,26` — both pin the current six-state/three-destination shape.
**Not** `app/design-authority-manifest.json`. Reading `app/scripts/sync_release_tool.py`,
that manifest is a release-tool integrity pin, not a design decision record: `load_authority`
requires `designAuthorities` to be **exactly two** `{path, sha256}` entries resolved against
`centralSource.path` (`../apple_developer`), and `verify_central` hashes the real bytes there.
A markdown doc in this repo cannot satisfy that schema. The supersession is recorded where it
belongs, in the committed `docs/UI-REDESIGN-2026-09-22.md`.

Separately, and pre-existing: both pinned reports resolve to
`../apple_developer/2026-08-08-quizzler-*-report.html`, and neither file exists there (the
copies live under `../.plans/apple_developer/attic/`). `verify_central` would raise
`design-authority-drift` on the release path today, independent of this work. Filed, not
fixed here — it needs a decision about the sibling repository.

## Out of scope, stated

- Reset progress (no repository delete capability — do not fake it).
- Confidence ratings (`SRSRating.hard`/`.easy` are unreachable; `SessionAnswer` has only `correct`).
- Weighted readiness (`syllabus.areas[].weight` is not bundled).
- `sessionsTotal` counting answers rather than sessions, and `today-score` reading as coverage
  when it is attempts — both real, both recorded in the design doc, neither fixed here.
