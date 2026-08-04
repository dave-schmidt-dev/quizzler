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
area: ["question-packs/**/*.json", "question-packs/**/BUILD_NOTES.md", "scripts/verify_pack.py", "scripts/pack_cert.py", "scripts/certify_codex_review.py", "docs/VALIDATION_RULES.md"]
gate_test: process; recorded in the course's BUILD_NOTES.md (independent review + human judgment cannot be a deterministic test; see rationale)
threshold: 3
rationale: INV-7 certifies each pack is well-formed, covered, and factually stamped by ONE automated critic run. A large, exam-stakes bank (a full multi-pack certification course, or any course whose questions a user will stake a real exam and fee on) carries impact that a single probabilistic critic does not fully de-risk. The normal ship-ready path MUST additionally pass, with the outcome recorded in the course's BUILD_NOTES.md: (1) Layer C `--strict` (re-graded against the generic standard, blocks on every finding); (2) an independent content + objective-alignment review by a SEPARATE model, covering accuracy of keyed answer + explanation vs the source, coverage/alignment vs the real exam objectives, retention of the strongest question per topic, and difficulty calibration; (3) a human spot-check. Exception: for a private local cutover only, David may explicitly authorize the narrowly scoped `codex-local-semantic-review` fallback when external reviewer capacity is unavailable and may explicitly waive the human spot-check. The pack must record that method, the waiver, the blind spots, and that no external certification is claimed; `scripts/certify_codex_review.py` still requires a fresh hash/stamp and the ordinary strict install gate. This exception is not an independent INV-8 review and does not redefine the normal ship-ready bar. Complements INV-7 (per-pack automated bar); this is the per-course elevated bar for high-impact banks.

### INV-9 - Shared progress must remain explicit, server-authoritative, and fail-visible
area: ["app/index.html", "app/progress-store.js", "app/shared-progress.js", "scripts/serve.py", "scripts/progress_store.py", "scripts/shared_progress.py", "start.sh", "tests/shared-progress.spec.js", "tests/test_progress_store.py", "tests/test_shared_progress_server.py", "playwright.shared.config.js", "docs/ARCHITECTURE.md", "docs/REPORT_SCHEMA.md"]
gate_test: tests/shared-progress.spec.js
threshold: 3
rationale: Browser-local progress remains the default. When shared mode is explicit, it must never silently fall back to local writes, accept stale revisions, lose an acknowledged mutation, flatten pack identity, or hide authentication, conflict, persistence, and network failures from the user.

### INV-10 - Course question volume must stay within the learner-workload budget
area: ["question-packs/**/*.json", "question-packs/**/_course.json", "scripts/build_manifest.py", "tests/test_build_manifest.py", "question-packs/AUTHORING.md", "docs/VALIDATION_RULES.md", "README.md", "start.sh", "tests/test_start_sh.py"]
gate_test: tests/test_build_manifest.py
threshold: 3
rationale: Per-pack quality gates do not control the total study burden. The manifest build warns above 200 questions per course and blocks installation above the fixed 240-question ceiling, preventing an exam course from silently growing into a wasteful 400–500-question bank. Course metadata may document a lower target but cannot raise the hard ceiling.
