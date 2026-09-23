# Quizzler native UI, second pass: screen-by-screen walkthrough

- **Date:** 2026-09-23
- **Build:** Debug, `com.zerodelta.quizzler`, iPhone 17 Pro simulator
  (`51A493D9-E9C9-4314-90E6-35D3EB4EB1C8`), built from the working tree on top of `1edd410`.
- **Design:** `docs/UI-REDESIGN-V2-2026-09-22.md`. Contract decisions C1–C10 are cited below.
- **Content:** CISSP (203), CySA+ (190), IT 540 (56), and the General Knowledge sample (6).
  Simulator progress at the start: CISSP 12 seen, 3 of 14 right, 9 recently missed.
- **Supersedes:** `WALKTHROUGH-2026-09-22-REDESIGN.md` for every screen named here.

This report is a release-owner review artifact. It supports, and does not replace, the
automated gate (`bash app/test-gate.sh --phase native`: 206 tests, including 15 UI tests).

## Screens

### Today

- The muted date line reads "Wednesday morning". The title reads "Ready when you are".
- **Hero card:**
  - The course name and **Change ›** sit on one row.
  - Nothing was due, so the card read "Nothing due", then the learn line, then **Start learning**.
- **Quiet list:**
  - **Learn new questions** showed 191.
  - **Retry missed** showed 9. At 0 (General Knowledge) the row is dimmed and disabled.
  - **Session length** showed 20 questions. It opens a menu with 10 / 20 / 40 / Whole pack.
    Choosing 10 changed the hero's estimate from 15 to 8 minutes.
- **Status line:** "3 of 14 right so far", then the verbatim persistence status (C2).
- The tab bar is Today / Progress / Settings (C10).

### Courses

- Pushed from **Change**. The title reads "Your courses".
- There is one card per pack: the subject, a trailing badge, a progress bar, and
  "S of T seen · N due".
- The selected pack shows a cyan border and the word **Studying**, so selection is not shown
  by colour alone. Other packs show "Nothing due".
- Tapping CISSP selected it and returned to Today, which then read CISSP.

### Question

- The tab bar is hidden.
- The header has **X**, a thin progress bar, and "1/10".
- Below it come the topic caption (formatted, e.g. "BCP Team Composition"), the prompt, and
  the options.
- The screen shows no course header, qid, or type label (C1).
- The bottom row holds the flag (**Report**) and **→ Skip**.
- Single-answer types answer on tap (C4). Multiple select ("Select all that apply") keeps a
  **Check Answer** button, disabled until something is selected.

### Answered

- The same header. The chosen wrong option reads `your answer` and the right one reads
  `correct`.
- An amber "Review this answer" card holds the explanation. A correct answer shows a green
  "Correct" card instead.
- The bottom row holds a square flag button and a full-width **Next question**.

### Report sheet

- **Cancel**, the title "Report question", a quoted prompt card, and four chips.
- **Send report** is disabled until a chip is chosen.
- **Marked answer is wrong** reveals "You think it's" with the question's four options.
- An optional detail field sits above **Send report**.
- Cancel returned to the Answered screen with nothing sent.

### Session done

The session was 10 questions: 4 answered (2 right, 2 wrong) and 6 skipped.

- The eyebrow reads "CISSP · Session done". The heading reads **2 of 4 right**, counting
  answered questions only (C6).
- The stat cards read 2 new learned and 2 to retry (C8).
- The Missed list shows both prompts, truncated to one line each.
- The existing save notice and **Retry sync** stay.
- **Retry the 2 missed** is primary and **Done** is secondary.
- Done returned to Today:
  - The score read 5 of 18.
  - Retry missed read 10, because one of the two misses was already in the list.
  - Learn new questions read 188, because one of the answered questions had been seen before.

### Ending a session

- **X** on a question returned straight to Today with no summary (C6). The sample pack's
  Today was unchanged, because nothing had been answered.

### Progress and Settings

- Unchanged from the 2026-09-22 walkthrough.
- **Progress:** coverage 15 of 203, review schedule 0 / 15 / 188, 10 recently missed, and
  the 14-day activity strip.
- **Settings:** the course picker, session length, app version, progress status, and
  **Retry sync**.

## Findings

Ordered by how much they cost a learner.

1. **The hero's learn line counts the whole pack.** With a 10-question session it read "Learn
   191 new questions · about 8 minutes". The minutes are for the batch; the count was not.
   **Fixed:** the line counts the session batch ("Learn 10 new questions · about 8 minutes";
   the sample pack reads "Learn 6 new questions · about 5 minutes"). Unit tests cover the
   capped, short-pack, and full cases.
2. **Today's sync status truncates on a phone.** It read "progress saved here · sync pendi…".
   **Fixed:** the score and the status sit on one row when they fit and stack when they do
   not; the status text wraps instead of truncating. Rechecked on the simulator.
3. **The report sheet's Cancel is clipped to "Cance".** A 44 pt frame on the toolbar button
   fixes the iOS 26 glass bubble at 44 pt.
   **Fixed:** the frame is removed; the system toolbar button already meets the touch target.
4. **Multi-line options touch the row edges.** Option rows have horizontal padding only.
   **Fixed:** option rows carry 10 pt vertical padding. The UI tests now scroll **Check
   Answer** clear of the bottom bar before tapping it, since the taller rows push it lower.
5. **A selected report chip or proposed answer differs only by colour.** This is review
   finding UI-2, and it was seen here too.
   **Fixed:** each chip and proposed answer shows a checkmark when selected and an empty
   circle otherwise, and keeps the VoiceOver selected trait.

### Minor, not changed

- On the last question the button still reads **Next question**, and it leads to Session done.
  The UI tests drive the full session through that label.
- The Report button on the question screen is a flag icon with no text. Its VoiceOver label
  is "Report".

## Mac

- **Build:** Debug Mac Catalyst, installed by `python3 app/scripts/install_mac_app.py` to
  `/Applications/Quizzler.app`. `open -b com.zerodelta.quizzler` launched that path and no
  other.
- **Window:** 431 × 768 pt at launch. It resizes between 380 and 560 pt wide, and its
  height is free.
- **Today:** the same layout as the phone. The CySA+ card read "6 questions due", "About 5
  minutes", and **Start review**. The quiet list read 184, 2, and 10 questions. The status
  line read "4 of 6 right so far" and "progress synced".
- **Tab bar:** Today / Progress / Settings in a floating bar at the bottom, as on the phone.
  It is hidden inside a session, as on the phone.
- **Not clicked on the Mac:** switching tabs and running a session. The agent's shell has no
  permission to post clicks. The Mac bar sets the same selection the phone's tab bar sets.

### Finding

6. **The first Mac install showed the tabs as a "Today" popup in the title bar.** Catalyst
   hosts `TabView`'s tabs in the window toolbar and collapses them when the window is
   phone-width. `.tabViewStyle(.tabBarOnly)` did not move them.
   **Fixed:** on the Mac only, the system tab bar is hidden and the app draws the phone's
   floating bottom bar. The iOS source is unchanged.
