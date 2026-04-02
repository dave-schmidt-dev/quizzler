# Coverage Model

## Purpose

Track whether the engine is sampling enough of the subject instead of repeating a narrow slice.

## What to Track

- topic frequency across recent rounds
- question type frequency across recent rounds
- chapter coverage across recent rounds
- visual vs non-visual balance
- definition vs application balance

## What's Already Implemented

The engine now tracks per-question mastery (`quizEngine_mastery_{courseId}` in `localStorage`) and uses it for **weighted question selection** at quiz start:

- **Unseen questions** (never attempted): 10x weight
- **Seen but never correct**: 5x weight
- **Mastered** (correct at least once): 1x weight

This ensures coverage naturally improves over time — unseen questions are strongly prioritized, but mastered questions still appear for reinforcement. The **Exam Readiness banner** on the Quiz Config screen shows seen/correct progress vs total questions.

## Coverage Signals

Useful signals for future adaptive generation (not yet implemented at the helper layer):

- `times_seen_recently`
- `rounds_since_last_seen`
- `question_type_recent_usage`
- `chapter_recent_usage`

## High-Performance Rule

If the learner is consistently above 90 percent:

- prioritize under-covered topics
- reduce repeated prompt families
- add more definitions and distinction questions
- add less-common but in-scope concepts

## Minimum Breadth Expectation

A strong round should not be composed mostly of one narrow pattern unless it is explicitly a focused remediation round.
