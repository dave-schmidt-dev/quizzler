# SRS Mode Decisions

Date: 2026-07-06
Status: decision record only. The full impulse-tier implementation plan was not completed in this session.

## Goal

Add a separate spaced-repetition mode to Quizzler for short, mobile-friendly review sessions throughout the day.

## Confirmed Direction

- SRS is a separate mode, not a replacement for normal quiz mode or retry-missed.
- Each question gets personal SRS state keyed by `(course_id, pack_id, question_id)`.
- The user-facing SRS state uses a 1-7 tier model.
- The UI should show each reviewed question's tier and when it is due next.
- The course screen should include a compact tier distribution bar graph and useful summary cues such as due now, overdue, new/unassigned, and total scheduled.
- SRS review should be mobile-first: one question at a time, large tap targets, minimal setup, and a sticky bottom action area.
- The default flow should support short batches, likely 5-10 due questions, with an option to continue reviewing after completion.
- SRS mode should prioritize overdue and due questions first, then optionally pull new or lower-tier questions if the due queue is too small.
- Normal quiz mode, retry-missed mode, and SRS mode must remain behaviorally distinct.

## Persistence Decision

Do not write live SRS progress back into question pack JSON by default.

Question packs are canonical course content. They may carry stable authoring metadata such as `srs_initial_tier`, `priority`, or `exam_weight`, but not personal runtime fields such as `tier`, `next_due_at`, or `last_reviewed_at`.

Personal SRS progress should live in versioned browser storage first, keyed by course, pack, and question. Because browser storage is not a durable backup strategy by itself, the first SRS plan should include import/export of SRS progress JSON in the same implementation scope.

IndexedDB is deferred unless the SRS state becomes large or query-heavy. The current app scale is small enough for versioned JSON storage plus explicit backup/export.

## Suggested State Shape

```json
{
  "schema_version": 1,
  "updated_at": "2026-07-06T00:00:00.000Z",
  "questions": {
    "samples::samples-demo::s1q1": {
      "tier": 1,
      "next_due_at": "2026-07-07T00:00:00.000Z",
      "last_reviewed_at": "2026-07-06T00:00:00.000Z",
      "review_count": 1,
      "lapse_count": 0,
      "last_result": "good"
    }
  }
}
```

The exact storage key and scheduling table should be chosen in the full plan.

## Planning Notes For Next Agent

- The project currently has no finished SRS implementation plan.
- The user requested impulse tier.
- The repo did not previously have `INVARIANTS.md`; it was bootstrapped from confirmed invariants in this session.
- There are unrelated dirty changes in validator-related files. Do not revert them.
- The full plan should still run the required self-contrarian and external contrarian review before being treated as implementation-ready.
- The plan should include tests for mobile viewport behavior, SRS tier transitions, due ordering, export/import round trip, and mode separation from normal quiz and retry-missed.
