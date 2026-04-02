# Quizzler Architecture

## Purpose

A multi-course quiz engine for exam prep. Runs as a single HTML file + JSON question packs served by a local Python HTTP server. No build step, no dependencies beyond Playwright for testing.

## Quick Start

```
./start.sh
```

Starts a local HTTP server, opens the browser, and waits for Enter to stop.

## Architecture

### Project Structure

```text
quizzler/
  start.sh                          # Launcher script (aliased to quiz-start)
  app/index.html                    # The entire engine (v3.0)
  question-packs/
    pack-template.json              # Template for authoring new packs
    AUTHORING.md                    # How to create packs and add courses
    samples/                        # Generic demo pack (committed to repo)
    <course>/                       # Course packs (.gitignored)
  tests/
    quizzler.spec.js                # Playwright test suite
  docs/                             # Architecture, schema, and authoring docs
  playwright.config.js
  package.json                      # npm test → Playwright
```

### Engine (app/index.html)

Single-file SPA. All HTML, CSS, and JavaScript in one file.

**Screens:**
1. **Home** — course selector cards
2. **Quiz Config** — module checkboxes, question count picker, exam readiness banner, retry missed tab
3. **Quiz** — question cards with sticky progress strip, results bar
4. **History** — past sessions with chapter breakdowns

**Course Registry:** `COURSES` array at top of `<script>`. Each course lists its module JSON files.

**Question Types:**
- `multiple_choice` — radio buttons, 3-4 options
- `true_false` — True/False buttons, boolean answer
- `matching` — left terms + right dropdowns, correctPairs array
- `scenario_multiple_choice` — same as MC with scenario context

**Features:**
- Module selector with select all/none and per-module question counts
- User-chosen quiz size from the currently eligible pool
- Weighted question selection — unseen questions (10x), seen-but-never-correct (5x), mastered (1x)
- Randomized question order and option order every session
- Sticky progress strip with live stats (answered, correct, wrong, accuracy %)
- Question ID visible on every card (ghost-colored, top-right)
- Immediate feedback with explanation modal on every answer (correct or wrong)
- Per-question manual mastery checkbox to hide known questions from future quizzes
- localStorage persistence (last 200 sessions) with structured session data (topic/chapter summaries)
- Session history with per-chapter breakdown and weak module highlighting
- Retry missed mode from any past session
- Mastery tracking — per-question "seen" and "correct at least once" flags persisted across sessions
- Quiz footer action returns to the selection screen instead of resetting the current quiz
- Readiness score — weighted formula: coverage 30% + mastery 30% + recent accuracy 40%, with qualitative labels (Just getting started / Building foundation / Strong progress / Nearly ready / Exam ready)

### Question Packs

JSON files following the schema in `QUESTION_SCHEMA.md`. Each pack has:
- `pack_id`, `subject`, `title`, `version`, `questions[]`
- Each question: `id`, `type`, `topic`, `difficulty`, `prompt`, `chapter`, `explanation`, plus type-specific fields

Question IDs are human-readable: `m3q15` = module 3, question 15. `r4q1` = round 4, question 1.

### Testing

```
npm test              # 78 Playwright tests (3 skipped), ~8s
npm run test:headed   # Same but with visible browser
```

**Coverage:** Navigation, module selection, quiz sizing, module filtering, question ID display, progress strip, all 4 question types (including matching index-0 edge case), completion/scoring, localStorage, session history with chapter tracking, retry missed, randomization, quiz footer navigation, explanation modal, tab switching, mastery tracking, weighted selection, readiness score.
Coverage includes the quiz footer return action and manual mastery exclusion behavior.

## Active Courses

### ITD 256 — Database Concepts & Design
- **128 questions** across 7 round-based packs
- Extracted from original single-file HTML quizzes used for midterm prep
- Topics: ERD, crow's foot notation, normalization (1NF-DKNF), keys, integrity, data dictionaries

### BCCCCE — Blockchain Council Certified Cryptocurrency Expert
- **413 questions** across 9 module packs
- Generated from course materials (summaries, transcripts, PDFs)
- Generated from external course materials (not included in this repo)

| Module | Questions | Focus |
|--------|-----------|-------|
| 1 | 35 | Crypto fundamentals, key definitions, history |
| 2 | 45 | Blockchain types, mining, consensus mechanisms |
| 3 | 35 | Bitcoin origins, store of value, forks |
| 4 | 50 | Ethereum, smart contracts, altcoins, ICOs |
| 5 | 45 | Wallets, exchanges, trading strategies |
| 6 | 60 | Technical/fundamental analysis, risk management |
| 7 | 50 | DeFi, lending, staking, yield farming, stablecoins |
| 8 | 45 | NFTs, minting, marketplaces, legal |
| 9 | 48 | Security, scams, KYC/AML compliance |

**Type distribution:** 246 MC, 72 TF, 55 matching, 40 scenario MC.

## Documentation Set

- `QUESTION_SCHEMA.md` — pack and question JSON schema
- `QUESTION_TYPES.md` — when to use each type, mix recommendations
- `VALIDATION_RULES.md` — 6-tier validation (schema → answer → visual → pedagogical → repetition → coverage)
- `AUTHORING_GUIDE.md` — question writing rules, distractor quality, difficulty guidelines
- `COVERAGE_MODEL.md` — topic frequency tracking, high-performance mode rules
- `RECENT_MEMORY_POLICY.md` — 3-round repetition window, 5-round coverage window
- `REPORT_SCHEMA.md` — session result JSON format
- `ADAPTIVE_GENERATION_PLAN.md` — three-source hybrid generation, weakness scoring
- `GENERATION_PROMPT_TEMPLATE.md` — reusable LLM prompt for generating packs

## Design Decisions

- **Explanations show on every answer** (correct or wrong) — user requested this for reinforcement even when guessing correctly
- **No timed mode** — user deferred this
- **Module-based packs, not monolithic** — allows selective study by module while keeping files manageable
- **Question ID always visible** — enables user to report specific bad questions by ID
- **Sticky progress strip** — follows user while scrolling through long quizzes
- **Mastery tracking** — tracks per-question "seen", "gotten right at least once", and an explicit manual mastery flag for questions the user wants hidden from future quizzes. The config screen reflects the eligible question pool after manual mastery filtering. Cleared when history is cleared. Stored in `localStorage` as `quizEngine_mastery_{courseId}`
- **Quiz footer navigation** — because grading happens per answer, the bottom action returns to quiz selection rather than pretending there is a final submit step
- **Readiness score** — composite formula displayed prominently on the config screen: `readiness = coverage × 0.3 + mastery × 0.3 + recentAccuracy × 0.4`. Recent accuracy uses the last 3 sessions. Qualitative labels: < 40% "Just getting started", 40–69% "Building foundation", 70–84% "Strong progress", 85–94% "Nearly ready", 95%+ "Exam ready"
- **Weighted selection** — quiz questions are not purely random. Unseen questions get 10x weight, seen-but-never-correct get 5x, mastered get 1x. Questions manually marked mastered are excluded entirely until unmarked

## Known Issues Resolved

- **Matching index-0 bug** — `parseInt("0") || -1` treated 0 as falsy, causing any match to right-side index 0 to always grade wrong. Fixed with explicit empty-string check.
- **Double-toggle checkbox bug** — `<label>` wrapping checkbox caused native + JS handler to cancel out. Fixed by using `<div>` instead.
- **Screen visibility bug** — `showScreen` used `display: ""` which didn't override CSS `display: none`. Fixed with `display: "block"`.

## What's Not Built Yet

- Adaptive generation script (`generate_followup_pack.py`)
- Timed mode
- Short answer / free response question type
- Cross-device sync

