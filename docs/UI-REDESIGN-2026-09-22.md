# Quizzler iOS — study-and-visibility redesign proposal

- **Date:** 2026-09-22
- **Status:** proposal. Not approved, not started.
- **Supersedes:** `app/design-authority-manifest.json` pins the 2026-08-08 launchpad flow
  report as the design authority. **Adopting this proposal is an explicit supersession of
  that authority and is the owner's call, not an implementation detail.**

## The finding that reframes this

This is not primarily a visual problem. The app already contains a study engine it never
uses. Verified directly against source:

1. **There is no session end.** `state` is assigned only `.question`, `.feedback`, `.today`
   and `.progress` (`LaunchpadView.swift:489,494,509,539,580,589,595,617`). Nothing ever
   assigns `.results`. `finishQuestion` (`:598-618`) always returns to `.question`, wrapping
   `% questionCount` forever. `ResultsView` (`:736-790`) is **dead code**. There is no
   results moment, no "here is what you just missed", and no natural stopping point.
2. **The SRS ladder is computed on every answer and then thrown away.**
   `ProgressEnvelope.applying` maintains a full 7-tier schedule per question
   (`ProgressRepository.swift:176-205,219-228`). The string `srs` appears **nowhere** in
   `app/QuizzleriOS/`.
3. **A question-selection contract exists and is never called.** `SelectionMode
   {normal, retryMissed, srs}` and `SelectionRequest` have **zero** references outside
   `SelectionMode.swift`.

So the app knows what is due for review and what you got wrong, and shows you neither. The
redesign is mostly a matter of surfacing data that is already being maintained — through M4
below, nothing beneath `LaunchpadProgressModel` has to change.

Two further measurement defects worth fixing while in here:

- **`sessionsTotal` counts answers, not sessions.** `recordAndSave` persists one
  `SessionDetail` per answer (`LaunchpadView.swift:124-127`) and `applying` increments per
  save (`ProgressRepository.swift:208`). The 200-entry window is the last ~200 *answers*.
- **`today-score` is attempts, not coverage.** `aggregate(courseID:packID:)` sums
  `mastery.answered` (`LaunchpadView.swift:83-97`), so it grows past the pack size while
  reading as if it were "N of M questions seen".

## Three constraints fixed up front

- **D1 — sync status becomes a chip; all eight `persistenceStatus` strings stay verbatim.**
  The UI tests assert `"local progress saved"`, `"progress synced"` and
  `"progress saved here · sync pending"` as static texts. Keeping the strings and moving
  them out of the header into a caption chip breaks no test and stops status competing with
  content. The `"answer checked · "` prefix is asserted by nothing and is dropped.
- **D2 — "Continue" stays pack-order.** A queue-driven home button would break
  `today-position`'s asserted `^Question \d+ of \d+$` advance-by-one contract. Due-review
  and retry-missed get their own rows and their own identifiers.
- **D3 — tab labels stay "Today", "Progress", "Settings".** Asserted in
  `AccessibilityUITests:28-34`.

## Information architecture

```
TabView (real SwiftUI TabView, labelled)
├── Today       → Continue · Due for review (N) · Retry missed (N) · Weakest area · sync chip
├── Progress    → Readiness · coverage/mastery · area breakdown · review schedule ·
│                 recently missed · study history
└── Settings    → course picker · sync detail · packs not loaded · about
Session = full-screen cover: Question → Feedback → … → Summary → Done | Retry missed
```

Session length defaults to 10 (configurable 10/20/40/whole pack). A fixed length is what
turns an endless loop into a study habit.

## The highest-value single change

On a wrong answer the app currently never shows which option was right — the explanation
has to carry it. Marking the correct option inline costs one parameter on
`QuestionRenderer`.

## Owner decision, 2026-09-22: area is an engine input, not a label

David: *"I don't personally care about what question area something comes from, but the
engine should track it to know where my weaknesses are."*

This settles the scope and redirects the design:

- **M5 is dropped.** It existed to bundle pretty area names and weights from `_course.json`
  and to store `exam_objective`. None of that is needed to find weaknesses. `examArea` is
  already a **required, non-blank** field on every question (`Question.swift:28,45,49`) and
  reaches the app today. Nothing beneath `LaunchpadProgressModel` has to change for any of
  this work. (`examObjective` appears only in `CodingKeys` — decoded and discarded, never
  stored. Leave it that way.)
- **There is no "Area breakdown" screen.** No slug lists, no per-area bars, no area names in
  the UI at all.
- **Weak areas drive question selection instead.** Area accuracy becomes an input to the
  session queue: questions from the user's weakest areas surface more often. The user sees
  "you're weak on these" expressed as *which questions they get*, not as a chart.
- Where weakness must be named in words, use the question's own `topic` — it is concrete
  ("d1-cia-triad" formats to something a human recognizes) and does not require the syllabus.

This raises M4's importance: selection is now where the value lands, not display.

## What the data actually supports

Available today, no model change: coverage, mastery ("correct at least once"), per-question
accuracy, **per-area accuracy via `metadata.examArea`** (as a selection weight, not a
screen), due/overdue/new counts, tier distribution, last-seen, recently-missed and the retry
queue (within the ~200-answer window), and a 14-day study strip.

Group weakness by `examArea` (4-8 per pack), **not** `topic` — CISSP has 203 distinct topics
for 203 questions, so per-topic accuracy is a list of singletons with no statistical weight.
Topic is for naming one question; area is for scoring a weakness.

Not available, and therefore not to be faked:

- **Lifetime streak.** Bounded by the 200-answer window — show a 14-day strip, never a
  lifetime number that would silently truncate.
- **Confidence ratings.** `SRSRating.hard`/`.easy` are unreachable; `SessionAnswer` carries
  only `correct`.
- **Weighted readiness.** Needs `syllabus.areas[].weight`, which is not bundled. Readiness
  is unweighted; label it so.

## Migration path

| Step | Scope | Breaks |
|---|---|---|
| **M1** Expose the envelope; add `StudyInsights` pure functions + tests | model only, no UI | nothing |
| **M2** TabView/NavigationStack, Today, Progress, sync chip, palette | UI | `AccessibilityUITests:52` (`staticTexts["PROGRESS"]` → navigation bar) |
| **M3** Session boundary + summary + retry-missed; makes `.results` reachable | UI + state | `QuestionShellTests:22,26` (both pin the current state/destination shape) **and the design-authority manifest** |
| **M4** Due-review mode **+ weak-area weighting** | selection | nothing, if D2 holds. Must satisfy INV-3/INV-6: cap the batch, never the queue |
| ~~**M5**~~ | **dropped 2026-09-22** — see the owner decision above; not needed to find weaknesses | — |

Palette: keep `#141419` and the card ramp; replace `primaryCyan #00FFFF` with a desaturated
teal (~`#3DBFB8`); keep success/warning/danger; system `.caption` for chrome, monospace only
for the qid. No gradients, no animation beyond system default. The console reading comes
from the cyan, the monospace and the exposed qid — not from the layout.

## Unverified

- Whether a `.popover`-hosted `question-qid` stays addressable by XCUITest (its identifier
  and `"Question ID \(qid)"` label are asserted). Fallback: demote to a muted caption line.
- Mac Catalyst behavior of `TabView` + `fullScreenCover`.
- A "Reset progress" settings action would need a repository delete capability that does not
  exist on `LaunchpadProgressRepository`. Defer or drop — do not fake it.
