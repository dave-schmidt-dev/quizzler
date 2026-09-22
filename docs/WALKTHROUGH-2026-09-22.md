# Quizzler iOS — screen-by-screen walkthrough

- **Date:** 2026-09-22
- **Candidate:** local Debug build of scheme `Quizzler` at `git describe` **7e2cd95** (clean
  tree for `app/`). Version 1.0.0, build 30. Not a TestFlight build, release candidate, or
  deployment.
- **Device:** iPhone 17 Pro simulator (`51A493D9-E9C9-4314-90E6-35D3EB4EB1C8`), 402x874 pt.
- **Content in this build:** CISSP (203 questions), CySA+ (190), General Knowledge (6).
  Packs are external build inputs; a clean checkout would carry only the 6-question sample.
- **Status:** implementation walkthrough, not release-owner approval or deployment approval.

This report is required before an attended deployment trigger. It supports, and never
replaces, automated, device, account, security, or production evidence.

---

## Coverage limits of this pass — read before relying on it

1. **Accessibility inspection was unavailable** in this session (`inspect` returned "not
   available right now"). Every observation below comes from rendered screenshots, so this
   pass reports what is *drawn*, not what a screen reader announces. Accessibility labels,
   traits, and the identifiers the UI test suite asserts on were **not** verified. A pass
   with `inspect` working is still owed before deployment.
2. **No iCloud account is signed in on the simulator.** Every CloudKit-backed state that
   requires an account is therefore unreachable here. Specifically **not covered**:
   `progress synced`, the account-changed recovery path, and any real sync conflict. The
   app spent the entire walkthrough in the local/`sync pending` branch.
3. **Session completion was not reached.** Working through 203 questions to observe the
   end-of-session/results screen was out of proportion to this pass. The completion screen
   and any "continue review" loop are **uncovered**.
4. **`true_false` and `matching` renderers were not exercised** — the CISSP pack led with
   `multiple choice` and those types are prohibited for new packs (L26). Unchanged from the
   2026-08-18 walkthrough.
5. **Mac Catalyst was not built or run in this pass.** iOS simulator only.

---

## Screens

### 1. Launch / Today

Entry screen, no onboarding of any kind — the app opens straight into study with a course
already selected. There is no first-run explanation, no account prompt, and no pack chooser
on the way in.

- Header: wordmark `Quizzler`, a monospaced sync-status string, and a gear.
- `TODAY · CISSP` eyebrow, headline "A focused review, ready when you are."
- Card: "Continue review", "Question 1 of 203", a `0/0` counter, and **Start review**.
- **View progress** button below the card.
- Tab bar: study (sun), progress (chart), settings (gear).

**Defect — header status is truncated.** The status reads `progress saved here · syn…`.
The full string is `progress saved here · sync pending`. The one piece of state the header
exists to show is cut off mid-word at the default width on a current-generation iPhone.

**Observation — `0/0` is unexplained.** No label says what the numerator and denominator
are. Adjacent to "Question 1 of 203" it reads as a contradiction.

**Observation — redundant navigation.** "View progress" and the progress tab go to the same
place.

### 2. Question

- Course eyebrow `CISSP`, topic slug `d1-cia-triad`, and a **Report** button.
- Metadata row: `qid: cissp-core::d1q01` and `multiple choice`.
- Prompt, four radio options, **Check Answer** (disabled until a selection is made).

**Defect — internal identifiers are shown to the user.** `qid: cissp-core::d1q01` and the
raw topic slug `d1-cia-triad` are authoring artifacts. A studying user has no use for
either, and the slug occupies the position a human-readable topic name should hold.

**Observation — the monospaced treatment of metadata reinforces a developer-console read.**

### 3. Feedback (same screen, after Check Answer)

- Options lock and dim; the chosen option keeps a filled radio and an accent border.
- Result card: green check, "Correct", then a multi-paragraph explanation that also explains
  why each distractor is wrong. **The explanation quality is the strongest part of the app.**
- **Next question** at the bottom.
- Header status changes to `answer checked · progress…` — **truncated again**, same defect.

**Defect — the explanation requires scrolling and gives no signal that it should be.** The
result card runs past the fold with no affordance; "Next question" is only reachable by
scrolling. A user who does not scroll sees a dead end.

### 4. Report Question (sheet)

Reached from **Report** on the question screen.

- **Close** button, title "Report Question".
- "Preview" section with the honest privacy line: "Reports include question context only.
  Progress history is excluded."
- Read-only preview: QID, TYPE, COURSE, APP VERSION, BUILD, SELECTED RESPONSE.
- A category picker defaulting to **Other**, an **Optional note** field, **Queue Issue**.

After tapping Queue Issue the button becomes the disabled label "Issue queued locally" and
the same sentence "Issue queued locally." appears below it.

**Defect — the confirmation is duplicated verbatim** in the button label and the status line.

**Defect — queued issues are a dead end for the user.** Nothing in the app lists queued
issues, says how many are pending, or indicates whether they will ever leave the device.
"Locally" is accurate but terminal: there is no visible path from a user's report to anyone
who could act on it. (Whether one exists in code is the subject of the separate
Mac/iOS interop audit.)

**Observation — the default category is "Other",** the least useful bucket, which biases
every report toward uncategorized.

### 5. Progress

Titled "Your study history".

- Two rows: **Answered 1**, **Correct 1**.
- Status line: "Progress is saved on this device. iCloud needs another try."
- **Retry sync** button.

**Defect — this screen does not deliver what its title promises.** Two integers is not a
study history. There is no per-topic or per-domain mastery, nothing due for review, no
session list, no coverage against the 203-question pack, and no record of which questions
were missed. This is the single largest gap between the app and its purpose.

**Observation — the recovery copy here is good.** "iCloud needs another try" plus an
explicit Retry is clear, honest, and actionable — notably better than the truncated
header.

### 6. Settings

Opened by the gear or the settings tab. Header shows `Quizzler settings` and a close X.

- **Study**: Course (picker, `CISSP · 203 questions`), App version `1.0.0`,
  Progress `Saved here · sync pending`, **Retry sync**.
- **About**: "Question packs and your selected course stay on this device. Progress syncs
  through your iCloud account. Reports include question context only."

**Course picker** lists `CISSP · 203 questions` (checked), `CySA+ · 190 questions`,
`General Knowledge · 6 questions`. No IT 540 pack, consistent with it not being built.

**Observation — Settings shows the full sync string** (`Saved here · sync pending`) that the
header truncates, confirming the truncation is a layout defect and not a content limit.

**Observation — build number is absent here** but present in the report preview (build 30).

---

## Defects found, ordered by user impact

| # | Screen | Defect | Severity |
|---|---|---|---|
| 1 | Progress | "Your study history" shows only two counters; no mastery, no due-for-review, no session history, no coverage, no missed-question record | High |
| 2 | Report | Queued issues have no list, no count, and no visible path off the device | High |
| 3 | Header (all) | Sync status truncated mid-word (`syn…`, `progress…`) at default width | Medium |
| 4 | Feedback | Explanation runs past the fold with no scroll affordance; **Next question** is below it | Medium |
| 5 | Question | Internal `qid` and raw topic slug shown to the user | Medium |
| 6 | Report | Confirmation duplicated in button label and status line | Low |
| 7 | Report | Category defaults to "Other" | Low |
| 8 | Launch | `0/0` counter is unlabeled and reads as contradicting "Question 1 of 203" | Low |
| 9 | Launch | "View progress" duplicates the progress tab | Low |

## What works well

- Explanation quality on the feedback screen, including why each distractor is wrong.
- The report sheet's privacy disclosure is specific and honest, and the preview shows
  exactly what will be sent.
- Recovery copy on the Progress screen names the problem and offers the action.
- Dark, neutral, restrained palette consistent with the house style.
