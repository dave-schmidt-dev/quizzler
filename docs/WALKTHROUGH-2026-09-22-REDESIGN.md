# Quizzler iOS — post-redesign screen-by-screen walkthrough

- **Date:** 2026-09-22
- **Build:** Debug, `com.zerodelta.quizzler`, installed to iPhone 17 Pro simulator
  (`51A493D9-E9C9-4314-90E6-35D3EB4EB1C8`) from `/tmp/quizzler-walkthrough`.
- **Candidate:** `b31b8a1` (P2b — session boundary and summary).
- **Content:** CISSP pack, 203 questions. Device progress at start: 1 question seen,
  1 attempt, resume position 11.
- **Supersedes:** `WALKTHROUGH-2026-09-22.md`, which covered the pre-redesign build.

This report is a release-owner review artifact. It supports, and does not replace, the
automated gate (`./app/test-gate.sh`, `--phase native`).

## Build note that matters for anyone repeating this

`CODE_SIGNING_ALLOWED=NO` produces a bundle that **traps on launch**: `QuizzlerApp.init` →
`QuizzlerProgressRepository.production()` → `CKContainer.init(identifier:)` → `EXC_BREAKPOINT`,
because the entitlement naming the CloudKit container is absent. Build the simulator app with
signing left on. The gate's own `build-for-testing` is unaffected because the UI tests set
`QUIZZLER_UI_TEST_LOCAL_PROGRESS`, which skips the CloudKit path entirely.

## Screens

### Today (tab 1)

Eyebrow `TODAY · CISSP`, headline, then three rows and a link:

| Element | State observed |
|---|---|
| Continue review | `Question 11 of 203`, score chip `1/1`, **Start review** (filled) |
| Due for review | `0` · "No questions due for review right now." — dimmed, disabled |
| Retry missed | `0` · "No recently missed questions." — dimmed, disabled |
| View progress | Secondary pill, navigates to Progress |
| Sync chip | `progress saved here · sync pending`, floating above the tab bar |

Both empty states render correctly disabled rather than tappable-and-inert.

### Progress (tab 2)

`Coverage` (Questions seen 1 of 203; Attempts 1 of 1, explicitly labelled "not distinct
questions"), `Review schedule` (Due now 0 / Upcoming 1 / Not yet scheduled 202),
`Recently missed` (0, with the "roughly the last 200 answers" bound stated), a 14-day
`Study activity` strip, and the sync detail with a **Retry sync** button.

No exam areas anywhere, as decided.

### Settings (tab 3)

Course picker (`CISSP · 203 questions`), App version 1.0.0, Progress status, Retry sync,
and the About paragraph on where data lives.

### Question

Header: course, topic, `qid: cissp-core::d1q11`, question type, **Report**. Then the prompt,
the answer controls, and **Check Answer** (disabled until something is selected).

All three seeded types render correctly: `multiple_choice` (radios),
`scenario_multiple_choice` (radios under a `Scenario response` caption), and
`multiple_select` (checkboxes under `Select all that apply`).

### Feedback

Correct: green check, `Correct`, explanation, **Next question**.
Incorrect: amber, `Review this answer`, explanation, **Next question**.

### Session summary — new in this candidate

Reached after the tenth answer. `RESULTS` / **Session complete** / `2 correct · 10 answered`,
the sync status, a **Missed** list of eight cards (truncated prompt + monospace pack-scoped
qid), then **Retry missed**, **Continue**, **Done**.

**Retry missed** was exercised: it re-served `cissp-core::d1q11`, the first missed question of
the session. The loop closes.

## Findings

Ordered by how much they cost a learner. Findings 1, 3, 4, and 5 were fixed in the
follow-up commit on the same day; their entries carry a **Fixed** line. Finding 9 is
confirmed intentional. The rest remain open.


1. **No position indicator inside a session.** Nothing on the question screen says which of the
   ten you are on. Today shows `Question 11 of 203` before you start and then the number
   disappears for the whole session. Add `n of 10`.
   **Fixed:** the question header now carries `n of 10` beside the qid (`session-position`),
   sourced from the active session rather than the pack.
2. **Content scrolls under the status bar.** The question scroll view has no top safe-area
   inset, so answer rows and headings pass behind the clock and the Dynamic Island while
   scrolling. Visible on every long question.
3. **A wrong answer still never names the right one.** The chosen wrong option keeps the cyan
   selected treatment and no option is marked correct; the explanation prose is the only
   source. This is the change the design doc called the highest-value single fix, and it
   remains deferred.
   **Fixed:** once an answer is checked, the right option reads `correct` and a chosen wrong
   option reads `your answer` — words, not colour, and both are in the VoiceOver value.
   Matching questions are excluded: they render as menus, with no row to caption.
4. **The sync chip floats over the bottom of the content.** On short questions it sits directly
   over **Check Answer** and swallowed a tap during this walkthrough. Either inset the scroll
   content by the chip's height or move the chip into the Progress/Settings surfaces only.
   **Fixed:** every scrolling surface now ends with `QuizzlerTheme.scrollBottomInset` (72pt) of
   bottom clearance, so the last control stays clear of the chip and the floating tab bar.
5. **The study-activity strip is occluded by the tab bar at rest.** Its `14 days ago` / `Today`
   captions sit behind the floating tab bar until you scroll. Same missing bottom inset.
   **Fixed:** same 72pt bottom inset, applied to the Progress scroll view.
6. **Summary buttons render hug-width.** `Retry missed` / `Continue` / `Done` are centered pills
   while every other primary button in the app is full-width, despite the
   `.frame(maxWidth: .infinity)` on each. The `.frame` is outside `.buttonStyle`, so the label
   does not expand.
7. **Topic slugs are shown raw** (`d1-separation-of-duties-two-person-control`). Formatting
   them was in scope for the redesign and has not been done.
8. **Settings "Retry sync" reads as a label.** It is the only row in that group that acts, and
   it carries no accent color, chevron, or other affordance.
9. **The tab bar stays live during a session.** You can leave a half-answered question by
   tapping Progress. The design called for a full-screen cover; this is a deliberate deviation
   worth confirming rather than a defect.
   **Confirmed intentional.** The tab bar stays live during a session.

### Deliberately not changed

- **Finding 2 (top safe area).** Scroll content passing under the status bar is stock iOS
  behaviour; the real complaint is legibility, which needs a scroll-edge material rather than
  a padding tweak. Left alone rather than half-fixed.
- **Findings 6, 7, 8.** Summary button width, raw topic slugs, and the Settings "Retry sync"
  affordance are still open.
