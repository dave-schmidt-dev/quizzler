# Quizzler iOS and Mac Catalyst — screen-by-screen walkthrough

- **Date:** 2026-09-15
- **Candidate:** uncommitted Debug development worktree for iOS and Mac Catalyst. This is
  not a TestFlight build, release candidate, or deployment.
- **Configuration covered:** local Debug build; Mac Catalyst is available where the host
  and target support it.
- **Content in this build:** every valid question pack discovered and bundled at build time;
  this walkthrough does not assert a particular course or question count.
- **Status:** implementation walkthrough, not release-owner approval or deployment approval.

This report is required before an attended deployment trigger. It supports, and never
replaces, automated, device, account, security, or production evidence.

---

## Candidate boundary

This document describes the current source and its user-visible states. Packs are external
build inputs, so the installed course list and question counts depend on the packs present
when the Debug build is produced. No TestFlight, signed deployment, production, or physical
device acceptance is implied.

---

## Open items a reviewer should weigh

These are not defects in the build; they are properties of it.

1. **Build content depends on the building machine.** `question-packs/*/` is gitignored
   except `samples/`, so the bundling step runs over locally installed packs. A build
   produced from a clean checkout would contain only the 6-question sample pack. The
   per-pack `content_digest` in `question-assets.json` is what identifies which content
   a given build actually carries; check it rather than assuming.
2. **`true_false` and `matching` still have no candidate rendering evidence.** Both types
   are prohibited for new packs (L26), so this walkthrough does not claim that an installed
   pack exercises those renderers. They remain covered only by the Debug preview fixture and
   the snapshot suite.
3. **Review sessions are continuous.** Answer → Feedback → Next question advances through
   the installed pack in order while saving each answer locally. The position remains durable
   across launches, so a tester can stop and resume without working through the pack in one
   sitting.
4. **External content review is pack-specific.** The current source does not establish a
   particular course or question count for this candidate; any content review must name the
   exact bundled pack set separately.

---

## Screen-by-screen

Every Launchpad screen is one case of `LaunchpadState`. The shell is a persistent console
header, a content region, and a bottom navigation bar; there is no `NavigationStack` in the
Launchpad itself, so there is no system back gesture between these states.

### Console header (persistent, every screen)

| Element | Behaviour |
| --- | --- |
| `Quizzler` wordmark | Static. |
| Status text | Derived from `persistenceState`: `loading local progress`, `local progress saved`, `saving progress locally`, `syncing progress`, `progress synced`, `progress saved here · sync pending`, `local save failed · retry required`. On Feedback it is prefixed `answer checked · `. On Settings it reads `settings`. |
| Gear / close button | Toggles Settings. Accessibility label switches between `Open settings` and `Close settings`. 44-point target. |

The status text is the app's only always-visible progress surface (INV-1). Answers first
reach the on-device CloudKit checkpoint. A CloudKit failure reads `progress saved here ·
sync pending`; it never pretends the other device received the change.

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

- Eyebrow `TODAY · <COURSE>` — the course name is the selected installed pack's `subject`,
  not a constant. Changing the course in Settings returns here and resets only the
  in-progress question selection.
- Card: "Continue review"; `Question <n> of <pack total>` (identifier `today-position`), followed
  by `<correct>/<answered>` (identifier `today-score`, accessibility label
  "<correct> correct of <answered> answered"). The score comes from shared progress; the
  position comes from this device's locally stored selected-pack resume position. Advancing a
  question stores the next position locally, so answers on another device do not skip this
  review queue. On a fresh install this reads `Question 1 of <pack total>` and `0/0`.
- `Start review` (filled, cyan on black, 48pt) → Question. Resets `selection` to `.none`.
- `View progress` (bordered, 44pt) → Progress.

### 1b. No questions available (empty state)

Reached when no pack loads. Identifier `no-pack-installed`.

- Headline "No questions available".
- The reason, in the danger colour, identifier `no-pack-reason`. It names the cause:
  no packs installed, a build produced without the bundling step, or the specific pack
  and failure (for example, a pack content digest that does not match the bundled asset).
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
  "Match each item". Renderer availability depends on which validated packs are bundled
  (see open item 2).
- `Check Answer` (48pt) is **disabled until a selection exists** and records the answer
  through the repository before showing feedback. The recorded identity is the pack's own
  `(courseID, packID, questionID)`.

### 3. Feedback

Same shell as Question with the renderer disabled, plus `FeedbackView` showing correctness
and the question's explanation. The identifier changes to `question-shell-feedback`. The
primary button becomes `Next question`, which saves the answer, clears the selection, and
advances to the next pack question while keeping the review session active. The current question
stays pinned until the transition, so an asynchronous local save cannot replace the feedback item.
The report control remains reachable.

### 4. Results

- Results is retained as a session state for future explicit completion flows; continuous review
  does not transition here after each answer.
- While saving: a `Saving progress locally…` label with accessibility label
  "Saving progress locally".
- When CloudKit is pending: "Progress is saved on this device. iCloud has not updated yet." plus
  a `Retry sync` button. A local-save failure remains "Progress was not saved. Retry before
  continuing." with `Retry save`.
- An account change says "This device has a different iCloud account. Your study history is safe
  on this device. Sign in to the original account to resume syncing." It is not retryable.
- `Continue review` starts another session; `View progress` opens Progress.

### 5. Progress

- Two stat rows, `Answered` and `Correct`, both from the repository.
- Disclosure identifies `Progress is synced through iCloud.`, `Syncing progress with iCloud…`,
  `Progress is saved on this device. iCloud needs another try.`, or `Progress is stored on this
  device.` The pending state exposes `Retry sync`; an account change directs the learner to sign
  in to the original account while local history remains safe.
- Reachable with no pack installed, since it describes the install rather than a course.

### 6. Settings

A `Form` with two or three sections.

- **Study** — `Course` picker (`course-selector`) exposes every valid bundled pack and its
  question count. The selected course is stored on this device only; it is not conflated with
  shared study progress. `App version` comes from `CFBundleShortVersionString`; `Progress`
  exposes the current iCloud status and offers `Retry sync` when pending.
- **Packs not loaded** — present only when a bundled pack was refused. One row per failure
  giving the pack path and the reason. A course that disappears therefore has a stated cause
  on a screen the tester can reach.
- **About** — "Question packs and your selected course stay on this device. Progress syncs
  through your iCloud account. Reports include question context only."

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

### 8. Development probe (Debug only)

`DevelopmentProbeView` remains a separate fault-injection surface. Normal app launch now uses
the entitlement-selected CloudKit container, restores CKSyncEngine state from the same atomic
checkpoint as progress, then explicitly fetches and sends. This walkthrough does not assert a
Production container result.

---

## Roles, permissions, and system sheets

- **No accounts, no roles, no in-app sign-in.** There is no role or permission difference to
  walk.
- **iCloud account state is system-owned.** If iCloud is unavailable, the app preserves the
  local checkpoint and shows a retryable sync-pending state. The app does not claim a remote
  save it cannot verify.
- **Account/container recovery is fail-visible.** After an account or container change, the
  app keeps local progress isolated and does not send or overwrite the current account. A genuine
  account change directs the learner to sign in to the original iCloud account instead of offering
  a retry that cannot succeed.
- **No permission prompts** — no camera, notifications, location, or tracking requests.

## Disabled and recovery states

| State | Where | Recovery |
| --- | --- | --- |
| `Check Answer` disabled | Question, until a selection exists | Make a selection |
| Renderer disabled | Feedback | `Next question` |
| `local save failed · retry required` | Header, any screen | Header `Retry save` |
| "Progress was not saved" | Results | `Retry save` |
| `progress saved here · sync pending` | Header, Results, Progress, Settings | `Retry sync` in Results, Progress, or Settings |
| `syncing progress` | Header, Progress | Wait for the explicit fetch/send attempt to finish |
| `iCloud account changed · local history kept safe` | Header, Progress, Settings | Sign in to the original iCloud account; local history is preserved |
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
- No signed physical-device iCloud pass; simulator coverage cannot prove account or private-zone
  behavior on David's devices.
- No TestFlight install, compliance, group assignment, or receipt (Task 5.3).
- External content review for any installed pack; the pack set is a build-time input and is
  not established by this document.
