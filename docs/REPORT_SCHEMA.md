# Report Schema

## Purpose

Define the structured format for quiz session results, mastery tracking, SRS
state, and privacy-minimal native issue reports. Browser-local mode remains
backward compatible; shared/native mode follows the versioned
[progress protocol](PROGRESS_PROTOCOL.md).

## Sessions Array

Each completed quiz appends a session object to the `sessions` array, stored in localStorage as `quizzler_sessions` (browser-local) or in SQLite as part of the normalized document (shared-progress). Max 200 sessions retained.

```json
{
  "sessions": [
    {
      "quiz_id": "round-4",
      "course": "itn260",
      "title": "ITN 260 — Network Security",
      "score": { "correct": 18, "total": 20 },
      "started_at": "2026-03-23T16:30:00-04:00",
      "completed_at": "2026-03-23T16:48:05-04:00",
      "duration_ms": 1085000,
      "modules_used": ["ch9", "ch10"],
      "retry_mode": false,
      "missed_topics": ["4nf"],
      "missed_chapters": ["ch9"],
      "topic_summary": {
        "4nf": { "correct": 2, "total": 3 }
      },
      "chapter_summary": {
        "ch9": { "correct": 10, "total": 11 }
      },
      "missed_questions": [
        {
          "pack_id": "final-review-ch9-15",
          "question_id": "r4q13",
          "exam_area": null,
          "topic": "4nf",
          "chapter": "ch9",
          "difficulty": "medium",
          "picked": "Join dependencies",
          "correct_answer": "Multivalued dependencies",
          "response_ms": 4200
        }
      ],
      "answers": [
        {
          "pack_id": "final-review-ch9-15",
          "question_id": "r4q13",
          "exam_area": null,
          "topic": "4nf",
          "chapter": "ch9",
          "difficulty": "medium",
          "correct": false,
          "response_ms": 4200
        }
      ]
    }
  ]
}
```

Fields:
- `quiz_id` — identifier for this quiz run (e.g. `round-4`, `retry-2026-03-23T...`)
- `course` — course ID from `_course.json`
- `title` — human-readable course name
- `score.correct` / `score.total` — grading results
- `started_at` / `completed_at` — ISO 8601 timestamps
- `duration_ms` — wall-clock duration
- `modules_used` — source module names for this session
- `retry_mode` — whether this session retries missed questions
- `missed_topics` / `missed_chapters` — dimensions containing misses
- `topic_summary` / `chapter_summary` — aggregate accuracy by topic/chapter
- `missed_questions` — per-question detail for wrong answers
- `answers` — per-question result rows for every question

### Result-row fields

Browser-generated `missed_questions` and `answers` rows carry `pack_id` and
`question_id`, plus `exam_area`, `topic`, `chapter`, and `difficulty`.
`missed_questions` also carries `picked`, `correct_answer`, and `response_ms`;
`answers` also carries `correct` and `response_ms`. The browser's parent
session carries `course`; the native v1 protocol additionally requires the
explicit `course_id` in each identity tuple.

The native tuple is mandatory for new v1 writes, including sessions assembled
from multiple packs. Legacy browser rows that lack `course_id` remain readable
but are not eligible for native migration until their source is explicitly
reconciled.

`exam_area` is `null` when the source pack omits it. Rows written before this field was added carry no `exam_area`; consumers must treat that absence as unknown, not as a distinct area.

## Mastery Tracking

Pack-scoped mastery state stored in localStorage under `quizzler_mastery_{courseId}__{packId}` (browser-local) or in SQLite under `mastery[{courseId}][{packId}]` (shared-progress).

```json
{
  "quizzler_mastery_itn260__final-review-ch9-15": {
    "seen": {
      "c9q1": true,
      "c9q2": true
    },
    "correct": {
      "c9q1": true
    },
    "consecutive": {
      "c9q1": 3,
      "c9q2": 1
    }
  }
}
```

- `seen` — every question ID the learner has attempted
- `correct` — every question ID answered correctly at least once
- `consecutive` — streak of consecutive correct answers (resets to 0 on wrong)

Updated at the end of each completed quiz. Cleared when session history is cleared.

The engine uses mastery data for:
1. **Readiness score** — `coverage × 0.3 + mastery × 0.3 + recentAccuracy × 0.4`
2. **Progress bars** on the Quiz Config screen
3. **Weighted question selection** — unseen 10×, seen-but-wrong 5×, mastered excluded

## SRS State

Per-course spaced-repetition state stored under `quizzler_srs_state_v1::<course_id>` in localStorage (browser-local) or `srs[<course_id>]` in SQLite (shared-progress).

```json
{
  "quizzler_srs_state_v1::itn260": {
    "schema_version": 1,
    "updated_at": "2026-03-23T16:48:05-04:00",
    "questions": {
      "itn260::final-review-ch9-15::c9q1": {
        "tier": 4,
        "next_due_at": "2026-04-06T16:48:05-04:00",
        "last_reviewed_at": "2026-03-23T16:48:05-04:00",
        "interval_days": 14,
        "review_count": 3
      }
    }
  }
}
```

- Question key format: `{courseId}::{packId}::{questionId}`
- Tiers 1–7 with intervals: 1d, 3d, 7d, 14d, 30d, 60d, 120d
- `again` drops tier by 2 (min 1); `hard` keeps tier at 0.75× interval; `good` advances +1 at 1.0×; `easy` advances +2 at 1.25×
- Missed/wrong answers always treated as `again`

## Normalized Document (Shared Progress)

In shared-progress mode, a single SQLite row stores the authoritative normalized document:

```json
{
  "schema_version": 1,
  "sessions": [ ... ],
  "mastery": {
    "itn260": {
      "final-review-ch9-15": { "seen": {...}, "correct": {...}, "consecutive": {...} }
    }
  },
  "srs": {
    "itn260": {
      "schema_version": 1,
      "updated_at": "...",
      "questions": { ... }
    }
  }
}
```

The browser-local adapter reads/writes the same shape; the shared adapter communicates it via the REST API with operation-level idempotency keys.

## Native issue reports (schema v1)

An issue report is an independent immutable record, queued separately from
progress:

```json
{
  "schema_version": 1,
  "issue_id": "issue-018f2c0e",
  "course_id": "itn260",
  "pack_id": "final-review-ch9-15",
  "question_id": "r4q13",
  "question_type": "multiple_choice",
  "app_version": "1.0.0",
  "build": "100",
  "selected_response": "B",
  "description": "The keyed answer appears inconsistent with the explanation."
}
```

`issue_id` is generated once and makes retries exactly-once. `selected_response`
is optional and must be the selected value, not a copy of the answer bank.
Reports deliberately exclude question text, explanations, full session
history, mastery, SRS, unrelated progress, account/device identifiers, file
paths, and credentials. User descriptions are treated as untrusted text and
are length-limited by the implementation. CloudKit record names and retention
are defined in `NATIVE_ARCHITECTURE.md`.
