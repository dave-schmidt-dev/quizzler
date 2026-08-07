<p align="center">
  <img src="assets/logo.svg" alt="Quizzler logo" width="120" height="120">
</p>

<h1 align="center">Quizzler</h1>

<p align="center">
  Zero-dependency quiz engine for exam prep — single HTML file + JSON question packs.
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-22c55e">
  <img alt="Runtime dependencies: 0" src="https://img.shields.io/badge/runtime%20deps-0-22c55e">
  <img alt="Tests: Playwright" src="https://img.shields.io/badge/tests-Playwright-22c55e">
</p>

<p align="center">
  <img src="assets/screenshots/feedback.png" alt="Answering a question with instant feedback" width="90%">
  <br>
  <img src="assets/screenshots/config.png" alt="Quiz builder with readiness tracking" width="90%">
</p>

## Quick Start

```bash
git clone https://github.com/dave-schmidt-dev/quizzler.git
cd quizzler
npm install        # Playwright (for tests only)
./start.sh         # Opens in browser
```

No build step required. The app is a static SPA served by Python's built-in HTTP server. Requires `python3`. The launcher auto-detects your platform for opening the browser (macOS, Linux, or falls back to printing the URL).

### Launch Matrix

| Command | Scope | Browser Opens | Progress Store |
|---|---|---|---|
| `./start.sh` | loopback only | `/app/` | browser localStorage |
| `./start.sh --lan` | all IPv4 interfaces | `/app/` | browser localStorage |
| `./start.sh --shared-progress` | loopback only | `/pair` | browser localStorage until paired |
| `./start.sh --shared-progress --lan` | all IPv4 interfaces | `/pair` | browser localStorage until paired |
| `./start.sh --shared-progress --tailscale` | loopback + Tailscale IP | `/pair` | browser localStorage until paired |

The server **always** has shared-progress endpoints available — `--shared-progress` only controls whether the browser opens to `/pair` instead of `/app/`. Switch between local and shared progress at any time from the Settings panel (gear icon on the home screen) — no restart required.

### Settings Panel

A gear icon in the upper-right of the home screen opens Settings, which shows the current storage mode (local or server-backed). From here you can enable or disable shared progress, view server info, and pair with new devices. The panel also surfaces an expired-session banner with a direct path back to Settings for re-pairing.

### Shared Progress (Cross-Device Sync)

Server-authoritative persistence so multiple browsers share one progress store — study on a Mac and pick up on a phone with synced history, mastery, and SRS state.

**Pairing flow:**
1. Run `./start.sh --shared-progress` (add `--lan` for Wi-Fi or `--tailscale` for Tailscale).
2. On the Mac, the browser opens to `/pair` — it shows a pairing code.
3. Click "Pair this device" to auto-pair the local browser.
4. On the phone/tablet, open `http://<ip>:4123/app/`, then open Settings (gear icon) and enter the code from the Mac.
5. The phone is now paired with a session cookie (24h expiry). Both devices sync to the same SQLite store.

You can also enable shared progress without restarting: open Settings on an already-running app and the server is auto-detected. Switching back to local storage logs out and reverts to localStorage — server data is preserved.

**Data paths:**
- Database: `.data/quizzler.sqlite3`
- Logs: `.logs/quizzler.log`
- Backup: `.data/quizzler.sqlite3.backup` (before schema migrations)

**Recovery:** If a quiz-completion save fails (network blip), the browser offers a JSON download of the lost session. Re-import via the Extras tab (Import Progress Data).

**Stop:** Press Enter or Ctrl+C in `start.sh` — the server is cleanly killed (trap handler).

**Offline:** Default (non-shared) mode is fully offline-capable with localStorage. Shared-progress mode requires network access to the server on port 4123.

## Features

- **5 question types** — multiple choice, multiple select (choose all that apply), true/false, matching, scenario-based
- **Weighted selection** — unseen 10×, seen-but-wrong 5× (info icon explains it on the config screen)
- **Mastery tracking** — mark questions you've nailed; mastered questions drop out of new quizzes until you reset progress
- **Readiness score** — coverage (30%) + mastery (30%) + recent accuracy (40%), with a per-band next-step hint
- **Session history** — 200-session log; expand any row to see prompts, picked vs. correct, and explanations for missed questions
- **Retry missed** — three post-quiz actions: Retry missed, Start another (preserves selections), Back to Course; or replay missed from any past session
- **Randomized order** — questions and answer options shuffled each session
- **Instant feedback** — explanation shown after every answer
- **Quick-pick chips** — set quiz size to 10 / 20 / 50 / All without typing
- **Module grouping** — pack lists group by filename pattern (Original rounds / Chapter packs / Combined exams)
- **Keyboard-first** — every interactive element is reachable by Tab; styled `:focus-visible` outlines throughout
- **Dark theme + flat aesthetic** — no gradients, no blur, honors `prefers-reduced-motion`
- **Offline-capable** — all data stored in localStorage

## Adding a Course

1. Create a folder under `question-packs/` (e.g., `question-packs/my-course/`).
2. Drop a `_course.json` (id, name, description, optional `sort_order` and `question_budget.target`) and one or more pack JSON files following `question-packs/pack-template.json`.
3. Run `./start.sh` (or `python3 scripts/build_manifest.py`) — the manifest is rebuilt from disk and the new course shows up on the home screen.

Course sizing is gated at build time: over 200 questions is an advisory planning signal; over 240 blocks installation. A course budget cannot raise the hard ceiling, which keeps exam banks from growing into unnecessary 400–500-question collections. The explicit `--allow-course-size-preview` option is for local WIP/test servers only; ordinary `start.sh` remains strict.

No code edits to `app/index.html` required. The course list is auto-discovered from the folder layout. See [question-packs/AUTHORING.md](question-packs/AUTHORING.md) for the full authoring guide and schema.

> The live `question-packs/manifest.json` is gitignored — it is regenerated by `start.sh` and whenever Playwright starts its own server for the test suite. If you already have a local server running on the port, Playwright reuses it as-is. See `question-packs/manifest.example.json` for the structure.

## Question-Pack Validation

Pack quality is enforced at multiple boundaries (**INV-7** — see `INVARIANTS.md`):

- **Coverage blueprint (L23):** every installed pack must declare
  `coverage_blueprint`; missing blueprint or under-covered topics are CRITICAL.
- **Authoring-time gate**: `scripts/lint_hook.py` (PostToolUse hook) runs when packs
  are edited and reports findings. Configured in `.claude/settings.json`.
- **Readiness + certification**: `scripts/verify_pack.py` runs Layer A + Layer C;
  exit **0** stamps a `certification` block (content hash + version axes). See
  [Validation Rules](docs/VALIDATION_RULES.md) *Certification stamp*.
- **Multi-critic panel**: `verify_pack.py <pack> --panel deepseek,ollama=qwen3:8b,claude`
  grades the pack with several **independent** models and gates on the **union**
  of their findings. One model's one pass cannot distinguish "reviewed carefully,
  found nothing" from "did not really look"; several can. Union, never majority —
  one cheap model finding a wrong answer still refuses certification. See
  [Critic Providers](docs/CRITIC_PROVIDERS.md). API keys come from
  `bws-secret-exec` only.
- **Cheap review, not cheap certification.** Only two paths write a
  certification: the default critic on a single pass, or a `--panel` of **2+**
  distinct passes. Any other single provider (`--provider ollama …`) runs the
  full gate and exits **3** — `REVIEW PASSED`, pack unchanged — so a 1B local
  model cannot stamp the block the install gate trusts. A one-entry `--panel` is
  refused. Two cheap passes do certify.
- **No local self-certification.** There is no bypass for "external reviewer
  capacity unavailable". The former `certify_codex_review.py` /
  `codex-local-semantic-review` path is deleted: it wrote a certification from
  inside the same session that authored the pack, which let `sy0-701` ship 115
  criticals while the install gate reported a clean pass. A pack reviewed only by
  its own author is not certified, whatever flags were passed.
- **Git hooks** (`scripts/hooks/`, install via `./scripts/hooks/install.sh`):
  pre-commit lints staged packs and rejects missing/stale certification
  (and pack-wide L23 coverage waivers); pre-push runs `npm test`.
- **Suppress findings**: Add a `lint_waivers` array (top-level in pack JSON) with
  reasons. Do not waive L23 on installed packs.
- **Quiet startup**: `scripts/build_manifest.py` prints a one-line summary; full log
  in `/tmp/quizzler-lint.log`. Use `--verbose` for inline output. Strict by default
  (Layer-A criticals abort the build). **`QUIZZLER_LINT_STRICT=0`** /
  `--no-strict` is for **local WIP preview only** — not CI, pre-push, or ship.
- **Standalone linter**: `python3 scripts/lint_packs.py <pack.json>` or `--all`.
- **Factual critic (Layer C)**: `python3 scripts/factcheck_pack.py <pack.json>`
  runs an LLM over each question to catch factual errors the deterministic linter
  cannot see (structure vs. truth). On-demand, probabilistic — verify findings
  before acting. `--provider` selects the backend (`claude`, `deepseek`,
  `ollama`, or any OpenAI-compatible endpoint); `scripts/critic_panel.py` runs
  several at once and merges their findings. Neither script certifies anything —
  certification is `verify_pack.py`'s job alone.
- **Course-wide re-cert sweep**: `python3 scripts/recert_sweep.py <course-dir-or-pack.json...>`
  runs `verify_pack.py`'s readiness gate over every pack (imports it in-process, not a
  subprocess), one pack at a time — skipping any pack whose `certification` block is already
  fresh (idempotent resume; a re-run after a partial failure only re-spends quota on packs
  that actually failed). `--dry-run` previews the plan (no quota spent, critic never called).
  `--panel deepseek,claude` certifies the whole course with a multi-critic panel — the bulk
  path is exactly where a single-critic false negative does the most damage.
  **Run it outside an interactive Claude Code session** — a nested `claude -p` critic is
  forced to `--jobs 1` there and a long sweep can exhaust quota at the tail, producing false
  failures on the last few packs (see `question-packs/sy0-701/BUILD_NOTES.md` "Infra note
  (nested-Claude flakiness, not content)").

See [Validation Rules](docs/VALIDATION_RULES.md) for criteria.

## Testing

```bash
npm test              # Run all Playwright tests
npm run test:headed   # Run with visible browser
```

Tests are course-agnostic and dynamically discover whatever packs are available. The included sample pack is enough to run the full suite out of the box.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — engine design and feature overview
- [Question Schema](docs/QUESTION_SCHEMA.md) — JSON pack format
- [Question Types](docs/QUESTION_TYPES.md) — when to use each type
- [Validation Rules](docs/VALIDATION_RULES.md) — 6-tier validation
- [Critic Providers](docs/CRITIC_PROVIDERS.md) — multi-provider Layer-C panel, secret handling
- [Authoring Guide](docs/AUTHORING_GUIDE.md) — writing quality standards
- [Coverage Model](docs/COVERAGE_MODEL.md) — topic frequency tracking
- [Course Build Playbook](docs/COURSE_BUILD_PLAYBOOK.md) — building a whole course via parallel per-chapter agents, mechanical trim, elevated QA
- [Recent Memory Policy](docs/RECENT_MEMORY_POLICY.md) — 3-round repetition window

## License

[MIT](LICENSE)
