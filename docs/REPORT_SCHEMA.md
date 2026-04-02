# Report Schema

## Purpose

Define the structured format for quiz session results stored in `localStorage`.

## Session Report JSON

```json
{
  "quiz_id": "round-4",
  "completed_at": "2026-03-23T16:30:00-04:00",
  "score": {
    "correct": 18,
    "total": 20
  },
  "missed_topics": [
    "4nf",
    "dknf"
  ],
  "missed_questions": [
    {
      "question_id": "r4q13",
      "topic": "4nf",
      "picked": "Join dependencies",
      "correct": "Multivalued dependencies"
    }
  ]
}
```

## Mastery Tracking Storage

Alongside per-session reports, the engine maintains a cumulative mastery record in `localStorage` under the key `quizEngine_mastery_{courseId}`.

```json
{
  "seen": {
    "r1q1": true,
    "r1q2": true,
    "m3q15": true
  },
  "correct": {
    "r1q1": true,
    "m3q15": true
  }
}
```

- `seen` — every question ID the learner has attempted across all sessions
- `correct` — every question ID the learner has answered correctly at least once

Updated at the end of each completed quiz. Cleared when session history is cleared.

The engine uses this data for three purposes:

1. **Readiness score** — composite formula: `readiness = coverage × 0.3 + mastery × 0.3 + recentAccuracy × 0.4`. Coverage = seen/total, mastery = correct/total, recent accuracy = average score from last 3 sessions. Displayed as a percentage with qualitative labels (Just getting started / Building foundation / Strong progress / Nearly ready / Exam ready).
2. **Progress bars** on the Quiz Config screen — shows seen count and correct count vs total questions
3. **Weighted question selection** — unseen questions get 10x weight, seen-but-never-correct get 5x, mastered get 1x when building a quiz

## Requirement

Future helper scripts should read and write the JSON form, not parse the human-facing text block. Mastery data can be read directly from `localStorage` using the key pattern above.
