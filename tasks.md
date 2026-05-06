# Quizzler Task Tracking

Status key: pending | in progress | done | blocked

## [2026-05-06] — Manifest Auto-Discovery

### Task 1: Implement manifest-based auto-discovery
- **Status:** done
- **Description:** Replace hard-coded COURSES array with auto-generated manifest.json from question-packs folder walk.
- **Done when:**
  - `build_manifest.py` written and functional
  - `app/index.html` async-fetches manifest with fallback
  - `start.sh` and `playwright.config.js` run build before server start
  - All tests passing (78/78, 3 skipped)
  - Documentation updated

### Task 2: Add adaptive features and weak-topic round generation
- **Status:** pending
- **Description:** Revisit auto-rescan in dev mode, weak-topic round generation, and cross-pack mastery view per user discussion.
- **Blocked by:** none
- **Tests:** to be determined per feature design

### Task 3: Clean up ITD 256 packs after final exam
- **Status:** pending
- **Description:** After user's Monday final exam, consolidate or archive ITD 256 question packs per exam review and performance.
- **Blocked by:** user completing final exam
- **Tests:** manual verification of retained/archived content

### Task 4: Add dev/contributing section to README
- **Status:** pending
- **Description:** If missing, add section documenting `build_manifest.py` workflow and folder-based course registration for future contributors.
- **Tests:** manual verification that README covers dev setup and manifest regeneration
