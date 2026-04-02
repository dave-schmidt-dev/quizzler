# Quizzler

Zero-dependency quiz engine for exam prep. Single HTML file + JSON question packs.

## Quick Start

```bash
git clone https://github.com/dave-schmidt-dev/quizzler.git
cd quizzler
npm install        # Playwright (for tests only)
./start.sh         # Opens in browser
```

No build step required. The app is a static SPA served by Python's built-in HTTP server.

## Features

- **4 question types** — multiple choice, true/false, matching, scenario-based
- **Weighted selection** — unseen questions appear more often; mastered ones less
- **Mastery tracking** — mark questions you've nailed; they drop in priority
- **Readiness score** — coverage (30%) + mastery (30%) + recent accuracy (40%)
- **Session history** — 200-session log with chapter/topic breakdown
- **Retry missed** — replay only the questions you got wrong
- **Randomized order** — questions and answer options shuffled each session
- **Instant feedback** — explanation shown after every answer
- **Dark theme** — easy on the eyes during long study sessions
- **Offline-capable** — all data stored in localStorage

## Adding a Course

1. Create a folder under `question-packs/` (e.g., `question-packs/my-course/`)
2. Add JSON question pack files following `question-packs/pack-template.json`
3. Register the course in the `COURSES` array at the top of `app/index.html`

See [question-packs/AUTHORING.md](question-packs/AUTHORING.md) for the full authoring guide and schema.

## Testing

```bash
npm test              # Run all Playwright tests
npm run test:headed   # Run with visible browser
```

> **Note:** The included test suite exercises course-specific question packs that are not committed to the repo (they're `.gitignored`). A sample pack with generic questions is included so you can run the app out of the box. To run the full test suite locally, add your own question packs matching the expected structure.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — engine design and feature overview
- [Question Schema](docs/QUESTION_SCHEMA.md) — JSON pack format
- [Question Types](docs/QUESTION_TYPES.md) — when to use each type
- [Validation Rules](docs/VALIDATION_RULES.md) — 6-tier validation
- [Authoring Guide](docs/AUTHORING_GUIDE.md) — writing quality standards
- [Coverage Model](docs/COVERAGE_MODEL.md) — topic frequency tracking
- [Recent Memory Policy](docs/RECENT_MEMORY_POLICY.md) — 3-round repetition window

## License

[MIT](LICENSE)
