# Report Schema

## Purpose

Define the structured format for quiz session results, mastery tracking, and SRS state — used by both browser-local (localStorage) and shared-progress (server-authoritative SQLite) modes.

## Sessions Array

Each completed quiz appends a session object to the `sessions` array, stored in localStorage as `quizzler_sessions` (browser-local) or in SQLite as part of the normalized document (shared-progress). Max 200 sessions retained.

```json
{
  "sessions": [
    {
      "quiz_id": "round-4",
      "course": "itn260",
      "course_name": "ITN 260 — Network Security",
      "pack_id": "final-review-ch9-15",
      "score": 18,
      "total": 20,
      "percentage": 90,
      "started_at": "2026-03-23T16:30:00-04:00",
      "completed_at": "2026-03-23T16:48:05-04:00",
      "duration_ms": 1085000,
      "mode": "normal",
      "modules_used": ["ch9", "ch10"],
      "per_topic": {
        "4nf": { "correct": 2, "total": 3 }
      },
      "per_chapter": {
        "ch9": { "correct": 10, "total": 11 }
      },
      "missed_questions": [
        {
          "question_id": "r4q13",
          "topic": "4nf",
          "chapter": "ch9",
          "picked": "Join dependencies",
          "correct": "Multivalued dependencies"
        }
      ]
    }
  ]
}
```

Fields:
- `quiz_id` — identifier for this quiz run (e.g. `round-4`, `retry-2026-03-23T...`)
- `course` — course ID from `_course.json`
- `course_name` — human-readable course name
- `pack_id` — source pack identifier
- `score` / `total` / `percentage` — grading results
- `started_at` / `completed_at` — ISO 8601 timestamps
- `duration_ms` — wall-clock duration
- `mode` — `normal` or `srs` (spaced repetition)
- `modules_used` — source module names for this session
- `per_topic` / `per_chapter` — aggregate accuracy by topic/chapter
- `missed_questions` — per-question detail for wrong answers

### Result-row fields

Both `missed_questions` and `answers` rows carry `question_id`, `pack_id`, `exam_area`, `topic`, `chapter`, and `difficulty`. `missed_questions` also carries `picked`, `correct_answer`, and `response_ms`; `answers` also carries `correct` and `response_ms`.

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
