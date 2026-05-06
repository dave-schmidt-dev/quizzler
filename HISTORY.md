# Quizzler Change History

A chronological record of meaningful changes to the codebase, bugs, remediation, and regression tests.

## 2026-05-06 — Manifest-based Auto-Discovery for Question Packs

**Added:**
- `scripts/build_manifest.py` — walks `question-packs/` directory and generates `question-packs/manifest.json` with auto-discovered courses and packs.
- `_course.json` support in question-pack folders. Optional metadata file with `id`, `name`, `description`, and `sort_order`. Falls back to folder-name derivation if absent. Skips folders starting with `.` or `_`.
- New `_course.json` files for all four course folders (samples, itd256, bcccce, itn213). Samples committed; others gitignored per existing rules.
- ITN 213 comprehensive final exam pack at `question-packs/itn213/final-comprehensive.json` (80 questions, modules 1–11 + financial modeling).

**Changed:**
- `app/index.html` no longer has hard-coded `COURSES` array. Now async-fetches `question-packs/manifest.json` with fallback error handling.
- `start.sh` runs `build_manifest.py` before starting local server.
- `playwright.config.js` `webServer.command` runs build script before test server.
- `README.md` and `question-packs/AUTHORING.md` updated to reflect new folder-based workflow (removed manual COURSES registration step).

**Verification:**
- Test suite: 78 passed / 0 failed / 3 skipped.
- Readiness test fixed by adding `sort_order` to samples course to maintain first-place order.
