# Quizzler native UI, second pass: one next step

- **Date:** 2026-09-22
- **Approved by:** David, in chat: "i like it, build it and install it and ship it", then
  "the mac app should match the layout".
- **Supersedes:** the screen designs in `docs/UI-REDESIGN-2026-09-22.md` and
  `docs/UI-REDESIGN-PLAN-2026-09-22.md` (their engine work, `StudySessionPlan`, stays). The
  2026-08-08 launchpad and question-type reports are no longer the visual reference.
  `app/design-authority-manifest.json` is a release-tool byte pin, not a design record, so
  it is not edited (see the 2026-09-22 plan).
- **Mockup:** six screens, iPhone width. A copy of the approved markup is kept outside the repo
  in the plan directory, `ui-redesign-run/mockup-quizzler_recommended_ui_flow.html`.

## Screens

### Today

- The date line is muted. The title reads "Ready when you are".
- The hero card leads with the course name and a **Change** link, which opens Courses. It then
  gives one recommendation:
  - When questions are due: "N questions due", "About M minutes", and the **Start review**
    button, which starts an SRS session.
  - When nothing is due but new questions remain: "Nothing due", then "Learn K new questions ·
    about M minutes", and the **Start learning** button, which starts a pack-order session.
  - When everything has been seen and nothing is due: "All caught up" and the **Keep
    practicing** button, which starts a pack-order session.
- A quiet list follows, three rows with a trailing count and a chevron:
  - **Learn new questions**, with the unseen count. It starts a pack-order session.
  - **Retry missed**, with the recent-miss count. It is disabled at 0.
  - **Session length**, with the current length. It opens a menu of the same choices Settings
    offers.
- The status line has two halves. The left reads "X of Y right so far" for the selected
  course. The right shows the persistence status, verbatim (see C2).
- The tab bar stays: Today, Progress, Settings.

### Courses

- Pushed from **Change**, with a back button labeled "Today". The title reads "Your courses".
- There is one card per installed pack:
  - The title is the pack's subject.
  - The trailing badge reads "Studying" for the selected pack, and "N due" or "Nothing due"
    for the others.
  - A progress bar shows seen divided by total.
  - A caption reads "S of T seen · N due".
- Tapping a card selects that course and returns to Today.
- Settings keeps its course picker.

### Question

- The tab bar is hidden during a session.
- The header row has three parts: an **X** button labeled "End session", a thin progress bar,
  and the "3/10" count.
- Below it come the topic caption (muted), the prompt, and the options.
- The screen shows no course header, no qid row, and no question-type label.
- Single-answer types answer on tap: multiple choice, scenario multiple choice, and
  true/false. Multiple select and matching keep a **Check Answer** button.
- The bottom row has **Report** (flag icon) on the left and **Skip** on the right.

### Answered

- The same header as the Question screen.
- Option rows are labeled with words: "your answer" and "correct". This labeling already
  exists in `QuestionRenderer`.
- The explanation card.
- The bottom row holds a square flag button (Report) and the full-width **Next question**
  button.

### Session done

- The eyebrow reads "IT 540 · Session done". The heading reads "7 of 10 right".
- Two stat cards: "new learned" and "to retry".
- A "Missed" list shows one truncated line per missed prompt.
- The primary button reads "Retry the N missed" when there are misses, and "Next session"
  when there are none. The secondary button reads **Done**, which returns to Today.
- The existing save and sync notices stay.

### Report sheet

- The top bar has **Cancel** and the title "Report question".
- A quoted card shows the question prompt.
- Four category chips. The chip labels map onto the existing `QuestionIssueCategory` cases
  (C5):

  | Chip | Category |
  |---|---|
  | Marked answer is wrong | `.incorrectAnswer` |
  | Confusing or ambiguous | `.other` |
  | Typo or wording | `.typo` |
  | Something else | `.other` |

- **Marked answer is wrong** reveals a "You think it's" option picker, drawn from the
  question's options. True/false offers True and False. Matching gets no picker.
- The sheet also holds an optional detail field and the **Send report** button.
- The qid, build, and version still travel in the report and are not shown.

## Mac (Catalyst) matches the phone

The Mac runs the same views. Three changes make it look like the phone rather than a stretched
iPad:

1. **The window is phone-shaped.** It is at least 380×600 and at most 560 wide. Height is free.
   This is set through `UIWindowScene.sizeRestrictions`, under
   `#if targetEnvironment(macCatalyst)`.
2. **The tab bar sits at the bottom.** The root window gets
   `traitOverrides.horizontalSizeClass = .compact` on Catalyst, so the tab bar and the layout
   take their iPhone form.
3. **Content never stretches.** The content of every tab is capped at 560 pt and centered.
   This is a no-op on a phone and keeps the iPad readable.

The Mac-only "Question reports" section in Settings is unchanged.

## Contract decisions

These are the decisions that deliberately change asserted behavior. The UI tests are updated
to the new contract in the same change.

- **C1, the qid leaves the screen but stays reachable (INV-2).**
  - The Report button (`question-report`) carries `accessibilityValue("Question ID <qid>")`.
  - UI tests read the pack-scoped id from that value, using the same
    `^Question ID [^:]+::[^:]+$` check.
  - The `question-qid` static text is removed.
- **C2, the sync strings are kept verbatim (was D1).**
  - The right half of Today's status line shows the eight `persistenceStatus` strings
    unchanged, and "Retry save" still appears on `.saveFailed`.
  - The mockup's short "Synced" is not used, because three UI tests assert the long strings.
  - The status appears on Today only, not during a session. Tests reach it by ending the
    session.
- **C3, the pack-order position moves off the screen and stays asserted (was D2).**
  - The "Learn new questions" row (`today-learn-new`) carries
    `accessibilityValue("Question N of M")`, which is the next pack-order position. It
    advances by one per pack-order answer and wraps.
  - The visible trailing count is the unseen count.
  - `today-score` reads "X of Y right so far".
  - Tests start pack-order sessions from `today-learn-new`, not from the hero. The hero's
    button depends on what is due.
- **C4, answering on tap.**
  - Selecting an option of a single-answer type checks it immediately.
  - "Check Answer" exists only for multiple select and matching.
  - `session-position` keeps its accessibility label "Question 3 of 10 in this session". The
    visible text becomes "3/10".
- **C5, no CloudKit schema change.**
  - "Confusing or ambiguous" and "You think it's" are encoded in the report's `description`:
    `"<chip label>[ · you think it's: <option>][: <detail>]"`.
  - The chip is always present in the text. The picked option appears only on the wrong-answer
    chip. The detail appears only when one is given.
  - A new enum case or field would be a record change that older builds and the Mac reader
    cannot read, and it would need a Production schema deploy (Red, TASKS Task 16).
- **C6, ending and skipping.**
  - **X** ends the session and returns to Today. When pressed from the Answered screen, it
    first does the same bookkeeping as Next: save, and for pack-order sessions advance the
    resume position. It does not open the summary.
  - **Skip** records nothing. For pack-order sessions it advances the resume position, so a
    relaunch does not re-serve the skipped question. It moves to the next question, or to the
    summary after the last one.
  - The summary's "N of M right" counts answered questions only.
- **C7, due review is a batch.** An SRS session takes at most the session length. More due
  questions stay due for the next session (INV-3 and INV-6: the cap is on the batch, not the
  queue). The time estimate is 45 s per question in the batch, rounded up, with a minimum of 1
  minute.
- **C8, "new learned".** `ActiveSession` records which identities had no mastery entry when the
  session started. "New learned" counts correct answers among those identities, and "to retry"
  counts wrong answers.
- **C9, summary buttons.**
  - `session-complete-heading` becomes "N of M right".
  - The accessibility labels stay:
    - "Return to Today" for Done;
    - "Continue to next session" for Next session;
    - "Retry the N missed" for the retry button.
- **C10, tabs.** The labels stay "Today", "Progress", and "Settings" (was D3). The "View
  progress" button on Today is removed, and tests use the Progress tab.

## Out of scope

- TestFlight and App Store (TASKS Task 16, Red).
- A "this week" statistic. It would need time-windowed history that operation compaction does
  not keep.
- Showing filed reports on course cards.
