# Quizzler 1.0.0 (7) release walkthrough

Prepared 2026-08-14 for the frozen candidate `1.0.0-7`. Review on the signed
physical-device preflight before upload.

| Route or state | Visible controls and expected behavior |
| --- | --- |
| Today | `Start review`, `View progress`, bottom Today/Progress/Settings tabs, and the Settings gear. Today is selected initially. |
| Question | Course/topic, question ID and type, answer controls, `Report`, and `Check Answer`. Check Answer is disabled until a valid selection exists. |
| Answer controls | Single-choice and scenario choices select one answer; multiple-select toggles choices; true/false selects one value; matching opens a per-item menu and remains incomplete until every pair is set. |
| Feedback | Correct or review status plus explanation; answer controls are disabled; `Finish Session` and `Report` remain available. |
| Results | Correct/answered counts, `Continue review`, and `View progress`. |
| Progress | Answered/correct counts and `Shared progress` toggle. Copy states whether progress is local or shared. |
| Settings | `Shared progress` toggle, Course and App version values, and pack/report privacy copy. The header control closes Settings. |
| Report sheet | Preview of question context, issue-type menu, optional note, `Queue Issue`, queued/retry states, and `Close`. Progress history is explicitly excluded. |
| Recovery/disabled states | Empty answer and incomplete matching disable Check Answer. A queued or saving report disables its queue action; a failed queue exposes Retry Queue Issue. |

Release-owner result: pending review.
