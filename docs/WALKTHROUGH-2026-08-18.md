# Quizzler iOS — screen-by-screen walkthrough

- **Date:** 2026-08-18 (revised, supersedes the same-day draft at `cdf05ec`)
- **Candidate:** marketing 1.0.0, build 16, HEAD `2ae94c5`
- **Configuration walked:** Release (`CODE_SIGNING_ALLOWED=NO` product built by the
  artifact-metadata gate leg) plus the Debug simulator build used by the XCUITest legs.
- **Content in this build:** the CISSP pack (`cissp-core`, 203 questions) and the
  sample pack (`samples-demo`, 6 questions), bundled from this machine.
- **Status:** draft for release-owner review. Not deployment approval.

This report is required before an attended deployment trigger. It supports, and never
replaces, automated, device, account, security, or production evidence.

---

## What changed since the previous revision

The earlier revision of this document recorded two blocking findings. Both are closed;
the evidence is recorded here rather than in the findings section so a reviewer can
check the claim rather than take it.

**Finding 1 — the app contained three questions, not the course.** Closed. The app now
loads packs through `PackCatalog`. In the unsigned Release product,
`nm -u QuizzleriOS.app/QuizzleriOS` lists 32 undefined `QuizzlerKit` symbols (was 26),
now including `PackCatalog.load(bundle:)`, `PackCatalog.primaryPack`, and
`InstalledPack.identity(for:)` — the app resolves those from the framework at load, so
it genuinely calls the decoder. The Release bundle carries `question-assets.json` and
`Packs/cissp/cissp-core.json` with all 203 questions. No `SeededStudyData`,
`UITestFixture`, or `FailureInjection` symbol is present in the Release binary.

**Finding 2 — the Today screen showed fabricated counters.** Closed. The position and the
score are both read from the progress repository, so both persist across launches.
`TodayCounterSourceTests` fails if the old literals reappear in `LaunchpadView.swift`, and
`StudyPositionTests` fails if the position moves back into view state. Persistence itself is
observed rather than asserted: the relaunch case in `QuizWorkflowUITests` answers one
question on the real Launchpad, terminates the app, relaunches it, and requires the score
and the position to have advanced by one.

**Root cause worth recording.** The packs were never wired in *and* would not have
loaded if they had been: `cissp-core.json` declared `generation_mode: "llm-assisted"`,
which `PackManifest.validate()` has never accepted, so the decoder would have refused
all 203 questions. Lint rule L29 now mirrors the native contract and is non-waivable.

---

## Open items a reviewer should weigh

These are not defects in the build; they are properties of it.

1. **Build content depends on the building machine.** `question-packs/*/` is gitignored
   except `samples/`, so the bundling step runs over locally installed packs. A build
   produced from a clean checkout would contain only the 6-question sample pack. The
   per-pack `content_digest` in `question-assets.json` is what identifies which content
   a given build actually carries; check it rather than assuming.
2. **`true_false` and `matching` still have no Release rendering evidence.** Both types
   are prohibited for new packs (L26) and neither appears in the CISSP pack, so no
   Release screen exercises those two renderers. They remain covered only by the Debug
   preview fixture and the snapshot suite.
3. **A session is still one question long.** Answer → Feedback → Results, then
   "Continue review" advances to the next question in pack order. That is unchanged
   from the previous revision and is not a content defect, but a tester expecting a
   multi-question session will not get one. Working through 203 questions therefore takes
   203 taps of "Continue review", though the position is durable across launches so it
   need not be done in one sitting.
4. **INV-8's external content review for the 203-question CISSP course remains open.**
   The build now displays that content, which raises the stakes of that open item
   rather than lowering them.

---

## Screen-by-screen

Every Launchpad screen is one case of `LaunchpadState`. The shell is a persistent console
header, a content region, and a bottom navigation bar; there is no `NavigationStack` in the
Launchpad itself, so there is no system back gesture between these states.

### Console header (persistent, every screen)

| Element | Behaviour |
| --- | --- |
| `Quizzler` wordmark | Static. |
| Status text | Derived from `persistenceState`: `loading local progress`, `local progress saved`, `saving progress locally`, `local save failed · retry required`. On Feedback it is prefixed `answer checked · `. On Settings it reads `settings`. |
| Gear / close button | Toggles Settings. Accessibility label switches between `Open settings` and `Close settings`. 44-point target. |

The status text is the app's only always-visible progress surface (INV-1). It is honest: a
failed save reads `local save failed · retry required` and does not revert to a success
state on its own.

### Bottom navigation bar (persistent, every screen)

Icon buttons for the primary states. Each carries an accessibility label (the state title),
an accessibility value of `Selected` / `Not selected`, and the `.isSelected` trait. While in
Question, Feedback, or Results the bar highlights **Today**, because `selectedNavigationState`
folds those three back to `.today`.

### 0. Pack loading (transient, on launch)

While the bundled packs are decoded the content region shows `Loading question packs…`
with accessibility label "Loading question packs" and identifier `pack-loading`. On this
candidate it is brief; it is present so the wait is never silent (INV-1).

### 1. Today

- Eyebrow `TODAY · CISSP` — the course name is the installed pack's `subject`, not a
  constant. A build carrying only the sample pack reads `TODAY · SAMPLES`.
- Card: "Continue review"; `Question <n> of 203` (identifier `today-position`), followed
  by `<correct>/<answered>` (identifier `today-score`, accessibility label
  "<correct> correct of <answered> answered"). **Both numbers come from the progress
  repository, so both survive a relaunch** — *n* is `answered % 203 + 1`, so a tester who
  quits after question 40 returns to question 41 rather than to question 1. On a fresh
  install this reads `Question 1 of 203` and `0/0`, which is accurate.
- `Start review` (filled, cyan on black, 48pt) → Question. Resets `selection` to `.none`.
- `View progress` (bordered, 44pt) → Progress.

### 1b. No questions available (empty state)

Reached when no pack loads. Identifier `no-pack-installed`.

- Headline "No questions available".
- The reason, in the danger colour, identifier `no-pack-reason`. It names the cause:
  no packs installed, a build produced without the bundling step, or the specific pack
  and failure ("`cissp/cissp-core.json` — content digest … does not match the bundled …").
- "Question packs are added when the app is built. Install a pack and build again."
- `View progress` remains available.

**This screen never shows sample or preview questions.** An install with no content
looks empty, which is the distinction the previous revision's finding 1 was about.

### 2. Question

- Report control top-right: `Report` label, identifier `question-report`, hint "Report a
  problem with this question", 44pt minimum. Reachable in both Question and Feedback.
- Course eyebrow and topic come from the pack; `qid: <packID>::<questionID>` metadata
  line, selectable text, identifier `question-qid`, accessibility label
  "Question ID <id>". Question type shown to its right with underscores replaced by spaces.
- Prompt carries the `.isHeader` trait.
- `QuestionRenderer` switches on the five schema types. Choice controls use identifiers
  `question-choice-<index>`; true/false uses `question-true` and `question-false`; matching
  uses `question-match-<index>`. Multi-select shows "Select all that apply"; matching shows
  "Match each item". In this candidate only the first three appear (see open item 2).
- `Check Answer` (48pt) is **disabled until a selection exists** and records the answer
  through the repository before advancing. The recorded identity is the pack's own
  `(courseID, packID, questionID)`.

### 3. Feedback

Same shell as Question with the renderer disabled, plus `FeedbackView` showing correctness
and the question's explanation. The identifier changes to `question-shell-feedback`. The
primary button becomes `Finish Session`, which saves the session and moves to Results. The
recorded answer is itself what advances the position, so the next question follows from saved
progress rather than from view state. The report control remains reachable.

### 4. Results

- "Session complete", then "<correct> correct · <answered> answered" from the repository.
- While saving: a `Saving progress locally…` label with accessibility label
  "Saving progress locally".
- On failure: "Progress was not saved. Retry before continuing." in the danger colour plus a
  `Retry save` button. **Retry is required — the screen does not auto-retry and does not
  present a saved state it has not achieved.**
- `Continue review` starts another session; `View progress` opens Progress.

### 5. Progress

- Two stat rows, `Answered` and `Correct`, both from the repository.
- Disclosure: "Progress is stored locally on this device. Cloud sharing remains unavailable
  until Production qualification." This is accurate for this candidate: no CloudKit sync is
  active in Production.
- Reachable with no pack installed, since it describes the install rather than a course.

### 6. Settings

A `Form` with two or three sections, read-only.

- **Study** — `Course` (the installed pack's subject, `CISSP` here, or `No pack installed`),
  `App version` (from `CFBundleShortVersionString`), `Progress` (fixed value `Local only`).
- **Packs not loaded** — present only when a bundled pack was refused. One row per failure
  giving the pack path and the reason. A course that disappears therefore has a stated cause
  on a screen the tester can reach.
- **About** — "Question packs stay on this device. Reports include question context only."

### 7. Report Question (system-owned sheet)

Presented as a `.sheet` from the question shell, wrapped in its own `NavigationStack` with
title `Report Question` and a `Close` toolbar button.

- "Preview" heading and the disclosure "Reports include question context only. Progress
  history is excluded."
- Context card previewing exactly what will be queued: identity, qid, question type, course,
  app version, build, and the tester's selected response. Course and identity come from the
  pack, so a report names the course the tester actually studied.
- `Issue type` menu picker over `QuestionIssueCategory`, 44pt minimum.
- `Optional note` multi-line field, 3–6 lines, accessibility label "Optional report note".
- Submit states, all visible: `Queueing issue locally…` (accessibility label "Queueing issue
  locally"), then `Issue queued locally.` or `Issue was not queued. Try again.` The pending
  issue ID is retained across a retry, so a retry does not mint a second issue.

Dismissal is the standard sheet drag or `Close`; both return to the question shell.

### 8. Development probe (Debug only, not in this candidate's Release path)

`DevelopmentProbeView` in `QuizzlerApp.swift` exposes `run` and `recover` against the
Development CloudKit container, with named failure-injection cases surfaced as visible
status. `DevelopmentProbeFailureInjection` and its environment key are excluded from Release
sources, and the gate now scans the built Release product to prove those symbols are absent.

---

## Roles, permissions, and system sheets

- **No accounts, no roles, no sign-in.** The app has one anonymous local user. There is no
  role or permission difference to walk.
- **No iCloud account prompt in Release.** Production does not attach to CloudKit, so the app
  never enters the system iCloud sign-in sheet. The only system-owned surface is the Report
  sheet above.
- **No permission prompts** — no camera, notifications, location, or tracking requests.

## Disabled and recovery states

| State | Where | Recovery |
| --- | --- | --- |
| `Check Answer` disabled | Question, until a selection exists | Make a selection |
| Renderer disabled | Feedback | `Finish Session` |
| `local save failed · retry required` | Header, any screen | `Retry save` on Results |
| "Progress was not saved" | Results | `Retry save` |
| "Issue was not queued. Try again." | Report sheet | Press submit again; the issue ID is reused |
| `loading local progress` | Header, on launch | Resolves when the repository loads |
| `Loading question packs…` | Content region, on launch | Resolves when the catalog loads |
| "No questions available" | Today | Install a pack and rebuild; no in-app recovery |

Every one of these is visible rather than silent, which is the INV-1 requirement. The empty
state has no in-app recovery by design — the app cannot install content at runtime, and
offering a retry that cannot succeed would be worse than saying so.

## Accessibility

Covered by `AccessibilityUITests` and `QuizWorkflowUITests` on both iPhone and iPad in the
`--phase native` gate: VoiceOver labels, focus order, Dynamic Type, 44-point touch targets,
and rotation. Snapshot baselines cover a 320-point width, dark mode, and accessibility text
sizes. Dark mode is forced (`preferredColorScheme(.dark)`); there is no light appearance to
review. Note that most of both UI suites drive `UITestFixtureView`, not the Launchpad, so
they do not cover the new empty state or the Today bindings; those are covered by
`StudyCatalogTests`, `TodayCounterSourceTests`, and `StudyPositionTests` at the unit level,
plus `QuizWorkflowUITests`, which asserts the Today card's shape on the real Launchpad
without asserting any particular course or question, and drives the answer-terminate-relaunch
path that proves the counters are durable.

## What this walkthrough cannot cover

- No signed physical-device pass (Task 5.2).
- No CloudKit Production behaviour (Task 5.1); the app has none in this configuration.
- No TestFlight install, compliance, group assignment, or receipt (Task 5.3).
- The 203-question CISSP external content review (INV-8) remains open, and this build now
  displays that content to a tester.

## Recommendation

The two blocking findings from the previous revision are closed with product-level
evidence. The remaining decision is a content one, not an engineering one: this candidate
would put 203 CISSP questions in front of a tester while INV-8's independent content and
objective-alignment review for that course is still open. Either complete that review
before triggering a deployment, or scope the TestFlight group to testers who are told
explicitly that the question content is unreviewed. The build-machine dependency in open
item 1 should also be settled before anyone but you produces a candidate.
