# Quizzler 1.0.0 (8) release walkthrough

Prepared 2026-08-14 for the frozen candidate `1.0.0-8`. Reviewed against the
signed physical-device preflight.

| Route or state | Visible controls and expected behavior |
| --- | --- |
| Today | `Start review`, `View progress`, bottom Today/Progress/Settings tabs, and the Settings gear. |
| Question | Course/topic, question ID/type, answer controls, `Report`, and `Check Answer`; checking is disabled until a valid answer exists. |
| Answer controls | Single choice, scenario, multiple select, true/false, and matching. Matching stays incomplete until every pair is set. |
| Feedback and results | Correct/review feedback plus explanation and `Finish Session`; results show counts, `Continue review`, and `View progress`. |
| Progress and settings | Answered/correct counts, `Shared progress` toggle, course/version values, and local/shared status copy. |
| Report sheet | Context preview, issue-type menu, optional note, queue/retry states, and Close. It states that progress history is excluded. |
| Disabled/recovery | Empty/incomplete answers disable checking; queued/saving reports disable queueing; a failed queue offers retry. |

Release-owner result: reviewed during the candidate-8 signed preflight.
