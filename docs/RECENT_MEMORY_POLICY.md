# Recent Round Memory Policy

## Purpose

Define how much recent history should influence repetition avoidance and topic selection.

## Recommended Window

Use the last `3` rounds as the default memory window for:

- topic repetition checks
- question pattern repetition checks
- visual layout repetition checks

Use the last `5` rounds for:

- under-covered topic detection
- broad subject coverage checks

## Why

Three rounds is enough to avoid obvious repetition.
Five rounds is enough to notice coverage holes without overfitting to stale history.

## Exceptions

Focused remediation mode may intentionally repeat a weak topic sooner, but should still vary:

- prompt wording
- question type
- diagram style

## What's Already Implemented

The engine now uses **weighted question selection** based on cumulative mastery tracking. Unseen questions get 10x weight, seen-but-never-correct get 5x, and mastered questions get 1x. This means the engine naturally avoids over-repeating mastered questions and prioritizes coverage gaps without needing round-window logic.

The round-window policies below remain relevant for **future adaptive generation** (the helper/LLM layer), where prompt pattern and topic diversity across consecutive generated packs still matters.

## Hard Rule

Do not reuse near-identical prompt patterns from the last 2 rounds unless the user explicitly asks for retry-only behavior.
