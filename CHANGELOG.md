# Changelog

Noteworthy user-facing changes are recorded here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A native SwiftUI CS0-004 synthetic investigation case, separately validated from quiz packs and labeled as practice rather than exam readiness.

### Changed

- The investigation lab appears on Today only while the CySA+ course is selected.
- One shared rule (`scripts/pack_discovery.py`) now decides which question-pack files are installable, replacing seven copies in the manifest builder, pack linter, asset bundler, both git hooks and tests. `_`/`.`-prefixed files are never packs; a `manifest.json` inside a course directory is now linted and freshness-checked like any pack, because the native bundler ships it.
- Pack content digests use CryptoKit SHA-256 instead of a hand-written implementation; digest output is unchanged.
- QuizzlerKit keeps one asset-manifest type (`NativePackAssetManifest`); the duplicate `QuestionAssetManifest` wrapper and the unused `PackQuestionID`/`QuestionKey` typealiases are removed. The wire format is unchanged.

### Removed

- The retired browser application, HTTP/shared-progress runtime, and browser test runner. iOS and Mac Catalyst are the supported Quizzler clients.
- `scripts/recert_sweep.py`, the always-failing retired live-certification stub, and its tests.
- `scripts/lint_hook.py`, an unwired stdin lint adapter, its tests, and the `zz-hooktest-` course-directory carve-out in the manifest builder, asset bundler, lint runner and git hooks.
- The retired `certifying` argument of `hybrid_verify.run_hybrid`, which only raised, and the `_hybrid_certifier` hand-off to `verify_pack.main` that hybrid always passed as `None`.
- The unreachable live-stamping branch of `verify_pack.main`, its `_hybrid_certifier` parameter, and the multi-provider panel plumbing in `verify_pack` (`_run_layer_c_panel`, `_adapt_panel`). A review pass never certified; its output and exit codes are unchanged.
