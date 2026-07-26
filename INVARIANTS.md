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

### INV-7 - Every installed question pack must pass the full quality bar (coverage + accuracy)
area: ["question-packs/**/*.json", "scripts/lint_packs.py", "scripts/verify_pack.py", "scripts/factcheck_pack.py", "scripts/build_manifest.py", "scripts/pack_cert.py", "scripts/lint_hook.py", "scripts/hooks/**", "tests/test_install_gate.py"]
gate_test: tests/test_install_gate.py
threshold: 3
rationale: Prevents a pack lacking a coverage_blueprint, failing L23 coverage, or lacking a fresh factual certification from being built/installed into the app. Pack quality (accuracy, coverage, Q&A quality) is the project's top priority and must hold for every pack regardless of which agent or human authored it, enforced by project tooling (not any agent-specific hook).

### INV-8 - High-impact exam-course banks require elevated content review beyond the automated gate
area: ["question-packs/**/*.json", "question-packs/**/BUILD_NOTES.md", "scripts/verify_pack.py", "docs/VALIDATION_RULES.md"]
gate_test: process; recorded in the course's BUILD_NOTES.md (independent review + human judgment cannot be a deterministic test; see rationale)
threshold: 3
rationale: INV-7 certifies each pack is well-formed, covered, and factually stamped by ONE automated critic run. A large, exam-stakes bank (a full multi-pack certification course, or any course whose questions a user will stake a real exam and fee on) carries impact that a single probabilistic critic does not fully de-risk. Before such a course is called ship-ready it MUST additionally pass, with the outcome recorded in the course's BUILD_NOTES.md: (1) Layer C `--strict` (re-graded against the generic standard, blocks on every finding); (2) an independent content + objective-alignment review by a SEPARATE model, covering accuracy of keyed answer + explanation vs the source, coverage/alignment vs the real exam objectives, retention of the strongest question per topic, and difficulty calibration; (3) a human spot-check. Process-enforced today; a future automated component may assert the BUILD_NOTES review record exists before certification. Complements INV-7 (per-pack automated bar); this is the per-course elevated bar for high-impact banks.
