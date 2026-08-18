# Quizzler iOS — screen-by-screen walkthrough

- **Date:** 2026-08-18
- **Candidate:** marketing 1.0.0, build 16, HEAD `cdf05ec`
- **Configuration walked:** Release (`CODE_SIGNING_ALLOWED=NO` product built by the
  artifact-metadata gate leg) plus the Debug simulator build used by the XCUITest legs.
- **Status:** draft for release-owner review. Not deployment approval.

This report is required before an attended deployment trigger. It supports, and never
replaces, automated, device, account, security, or production evidence.

---

## Blocking findings

These are the reasons this candidate should not go to TestFlight as a study app.

### 1. The shipping app contains three questions, not the course

`LaunchpadView` reads `SeededStudyData` — a hardcoded array declared in the same file.
Release ships **three** questions (`q0042`, `q0043`, `q0044`); Debug adds two more behind
`#if DEBUG` (`preview-true-false`, `preview-matching`).

`QuizzlerKit`'s `PackLoader` — the decoder that reads real packs, enforces pack-scoped
question IDs, and rejects prohibited types — is **not referenced anywhere in the
`app/QuizzleriOS` target**. No installed pack reaches a screen. A tester who installs this
build sees a three-question demo loop, not Security+ and not the 203-question CISSP set.

Consequences a reviewer should weigh:
- The two prohibited-for-new-packs types (`true_false`, `matching`) are the two that appear
  only in Debug, so **no Release screen exercises the matching or true/false renderers**.
  Their production rendering is unproven by this build.
- Session content repeats: `questionIndex` advances modulo three, so "Continue review"
  cycles the same three questions forever.

### 2. The Today screen shows fabricated counters

`TodayView` renders the literal strings `"Question 1 of 12"` and `"3/12"`. Neither is bound
to `LaunchpadProgressModel`, the repository, or the seeded array (which holds three items,
not twelve). The number a tester reads on the first screen is decoration.

This matters beyond cosmetics: the Progress screen below it reports *real* counts from the
repository, so the two screens disagree with each other by design.

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

### 1. Today

- Eyebrow `TODAY · SECURITY+`, headline "A focused review, ready when you are."
- Card: "Continue review", "Question 1 of 12", trailing "3/12" — **all three are static text
  (finding 2)**.
- `Start review` (filled, cyan on black, 48pt) → Question. Resets `selection` to `.none`.
- `View progress` (bordered, 44pt) → Progress.

### 2. Question

- Report control top-right: `Report` label, identifier `question-report`, hint "Report a
  problem with this question", 44pt minimum. Reachable in both Question and Feedback.
- `qid: <id>` metadata line, selectable text, identifier `question-qid`, accessibility label
  "Question ID <id>". Question type shown to its right with underscores replaced by spaces.
- Prompt carries the `.isHeader` trait.
- `QuestionRenderer` switches on the five schema types. Choice controls use identifiers
  `question-choice-<index>`; true/false uses `question-true` and `question-false`; matching
  uses `question-match-<index>`. Multi-select shows "Select all that apply"; matching shows
  "Match each item".
- `Check Answer` (48pt) is **disabled until a selection exists** and records the answer
  through the repository before advancing.

### 3. Feedback

Same shell as Question with the renderer disabled, plus `FeedbackView` showing correctness
and the question's explanation. The identifier changes to `question-shell-feedback`. The
primary button becomes `Finish Session`, which advances `questionIndex`, saves the session,
and moves to Results. The report control remains reachable.

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

### 6. Settings

A `Form` with two sections.

- **Study** — `Course` (Security+), `App version` (from `CFBundleShortVersionString`),
  `Progress` (fixed value `Local only`).
- **About** — "Question packs stay on this device. Reports include question context only."

There are no interactive controls in Settings. It is read-only.

### 7. Report Question (system-owned sheet)

Presented as a `.sheet` from the question shell, wrapped in its own `NavigationStack` with
title `Report Question` and a `Close` toolbar button.

- "Preview" heading and the disclosure "Reports include question context only. Progress
  history is excluded."
- Context card previewing exactly what will be queued: identity, qid, question type, course,
  app version, build, and the tester's selected response.
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

Every one of these is visible rather than silent, which is the INV-1 requirement.

## Accessibility

Covered by `AccessibilityUITests` and `QuizWorkflowUITests` on both iPhone and iPad in the
`--phase native` gate: VoiceOver labels, focus order, Dynamic Type, 44-point touch targets,
and rotation. Snapshot baselines cover a 320-point width, dark mode, and accessibility text
sizes. Dark mode is forced (`preferredColorScheme(.dark)`); there is no light appearance to
review.

## What this walkthrough cannot cover

- No signed physical-device pass (Task 5.2).
- No CloudKit Production behaviour (Task 5.1); the app has none in this configuration.
- No TestFlight install, compliance, group assignment, or receipt (Task 5.3).
- The 203-question CISSP external content review (INV-8) remains open, and this build would
  not display that content in any case — see finding 1.

## Recommendation

Do not trigger a TestFlight deployment on this candidate as a study application. Findings 1
and 2 are content and correctness gaps a tester meets on the first two screens. Either wire
`PackLoader` into the Launchpad and bind the Today counters to the repository, or state
explicitly that this build is an internal shell-only preview and set tester expectations to
match.
