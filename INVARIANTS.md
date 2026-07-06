# Invariants - quizzler

> System contract. The harvest tool reads `area:` globs to map HISTORY bug entries
> to invariants. Per-project convention is declared in this project's README.

### INV-1 - Progress storage must stay pack-scoped and resilient
area: ["app/index.html", "tests/quizzler.spec.js", "docs/REPORT_SCHEMA.md", "docs/ARCHITECTURE.md"]
gate_test: tests/quizzler.spec.js
threshold: 3
rationale: Prevents cross-pack progress contamination, corrupt localStorage crashes, and full-storage writes from silently losing study progress.

### INV-2 - Question identity must stay pack-scoped end to end
area: ["app/index.html", "tests/quizzler.spec.js", "question-packs/**/*.json", "docs/QUESTION_SCHEMA.md"]
gate_test: tests/quizzler.spec.js
threshold: 3
rationale: Prevents duplicate question IDs across packs from corrupting selection, rendering, history, retry, mastery, or SRS state.

### INV-3 - Quiz selection modes must preserve their explicit learning contracts
area: ["app/index.html", "tests/quizzler.spec.js", "tests/srs-gates.spec.js", "docs/ARCHITECTURE.md", "docs/RECENT_MEMORY_POLICY.md"]
gate_test: tests/srs-gates.spec.js
threshold: 3
rationale: Prevents retry-missed, normal quizzes, and future SRS due-review mode from silently hiding questions that the selected mode promises to show.

### INV-4 - Pack validation and test gates must stay wired into the main runner
area: ["tests/**/*.js", "tests/**/*.py", "scripts/**/*.py", "playwright.config.js", "package.json"]
gate_test: tests/python-suites.spec.js
threshold: 3
rationale: Prevents Python suites, pack validators, and new regression tests from existing outside the authoritative `npm test` gate.

### INV-5 - The browser app must remain static, offline-capable, and secret-free
area: ["app/index.html", "start.sh", "scripts/**/*.py", "README.md", "docs/**/*.md"]
gate_test: tests/quizzler.spec.js
threshold: 3
rationale: Prevents the browser runtime from gaining shell access, secret handling, or external service dependencies that violate the zero-runtime-dependency study-tool boundary.

### INV-6 - SRS due state must not make reviewable questions disappear
area: ["app/index.html", "tests/quizzler.spec.js", "tests/srs-gates.spec.js", "docs/SRS_MODE_DECISIONS.md"]
gate_test: tests/srs-gates.spec.js
threshold: 3
rationale: Future SRS scheduling can delay questions, but due or overdue questions must remain visible in SRS mode unless the user resets progress or explicitly changes their state.
