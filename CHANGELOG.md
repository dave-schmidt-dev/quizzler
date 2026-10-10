# Changelog

Noteworthy user-facing changes are recorded here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A local diagnostics pilot records private sync status and lifecycle facts without study content.
- A native SwiftUI CS0-004 synthetic investigation case, separately validated from quiz packs and labeled as practice rather than exam readiness.
- Resume session: leaving a session or relaunching the app keeps your place, and Today offers "Resume session · N of M". Questions answered elsewhere in the meantime are skipped, never recorded twice.
- Keyboard shortcuts in a session: 1-9 choose an answer and S skips; Return (check or continue) and Esc (end the session) are bound, with Mac verification at the next release milestone.
- The investigation lab keeps your phase, answers, pins, response and note when you leave and come back; Replay or submitting the handoff clears it.
- The session summary opens each missed question with its answer and explanation, and Next session sits beside Retry.
- Progress starts due reviews and missed-question retries directly; Today and Progress support pull to refresh.
- Mac: a Session menu (check or continue, skip, end session) that follows the current question, and the Mac tab bar hides inside a session like the phone's.
- Debug builds can install a partial pack with questions held for review; Courses and Progress label it "Partial: X of Y reviewed questions installed; Z held for review". Release builds refuse partial packs.
- Certification campaigns can set questions aside: a quarantine frontier certifies the remaining subset, and `scripts/pack_quarantine.py` moves the held questions out of the installed pack and restores them.
- Every campaign certification records an issuance receipt in its ledger.
- A new campaign can inherit a prior certified campaign's census (`certification_campaign.py init --inherit-from-ledger`), so only new or changed questions need a reviewer recheck. Any change to the subject, source, waivers, grounding, verifier or pack identity refuses inheritance.
- `scripts/review_captures.sh` saves a named screenshot of every primary screen under `.logs/captures/` for design review.

### Changed

- The cold-launch Zero Delta sting now holds its finished lockup for 600 ms and fades
  out over 180 ms instead of cutting away the instant the animation settles.
  The completion fail-safe moves from 2 s to 3 s to cover the hold.
- The investigation lab appears on Today only while the CySA+ course is selected.
- Question feedback shows the verdict first, then the explanation, then the Leitner card; right and wrong choices are marked by colour, icon and caption. Check Answer stays pinned at the bottom.
- Learn new serves only questions you have not answered yet.
- Lowering the maximum Leitner level asks for confirmation and shows saving, failure and retry.
- Reporting a question closes the sheet on send with a saved confirmation, keeps Send above the keyboard, and asks before discarding a draft.
- The investigation lab confirms Replay, pins the handoff checklist, and unlocks phases in order.
- Explanatory notes on Progress, Today and the lab open from info buttons, and Settings explanations are section footers. How reviews work sits below the Today card.
- Pending sync shows as a warning rather than an error; labels and terms are used once and consistently ("Scheduled review", sentence case).
- A `difficulty-miscalibration` reviewer finding is advisory and no longer blocks pack certification.
- One shared rule (`scripts/pack_discovery.py`) now decides which question-pack files are installable, replacing seven copies in the manifest builder, pack linter, asset bundler, both git hooks and tests. `_`/`.`-prefixed files are never packs; a `manifest.json` inside a course directory is now linted and freshness-checked like any pack, because the native bundler ships it.
- Pack content digests use CryptoKit SHA-256 instead of a hand-written implementation; digest output is unchanged.
- QuizzlerKit keeps one asset-manifest type (`NativePackAssetManifest`); the duplicate `QuestionAssetManifest` wrapper and the unused `PackQuestionID`/`QuestionKey` typealiases are removed. The wire format is unchanged.

### Fixed

- Edge-swipe back works again on Your courses.
- A legacy question at Leitner level 6 or 7 records its true prior level on its first review under the level-5 cap.
- An issue-report send failure no longer forces a spurious progress rebase.
- The app's pack bundling step now runs the full install gate (coverage blueprint, L23/L27, lint criticals and course distribution) and bundles the exact bytes it checked; it previously checked only the native contract and certification freshness.
- Campaign certification reads the pack once, holds a per-pack lock, and refuses if the pack or grounding changes before the stamp is written.
- A remediation round can no longer change a pack's subject, source directive or identity while re-grading only the edited questions. Campaigns started before this change must restart; existing certifications are unaffected.
- A pack rejected for both lint findings and a stale certification now reports every reason, not only the certification.

### Removed

- The retired browser application, HTTP/shared-progress runtime, and browser test runner. iOS and Mac Catalyst are the supported Quizzler clients.
- `scripts/recert_sweep.py`, the always-failing retired live-certification stub, and its tests.
- `scripts/lint_hook.py`, an unwired stdin lint adapter, its tests, and the `zz-hooktest-` course-directory carve-out in the manifest builder, asset bundler, lint runner and git hooks.
- The retired `certifying` argument of `hybrid_verify.run_hybrid`, which only raised, and the `_hybrid_certifier` hand-off to `verify_pack.main` that hybrid always passed as `None`.
- The unreachable live-stamping branch of `verify_pack.main`, its `_hybrid_certifier` parameter, and the multi-provider panel plumbing in `verify_pack` (`_run_layer_c_panel`, `_adapt_panel`). A review pass never certified; its output and exit codes are unchanged.
- `app/scripts/build-native-pack-assets.py`, a superseded pack bundler with no caller (the Xcode phase runs `scripts/build_pack_assets.py`).
- `scripts/hooks/pre-commit` and `scripts/hooks/pre-push`, wrappers that only exec `.githooks/`. Run `scripts/hooks/install.sh` in each clone to set `core.hooksPath` to `.githooks`.
- The unused `true_false` and `matching` native renderers (`TrueFalseQuestion`, `MatchingQuestion`, their `Question` cases, answer selection and shell branches, and preview fixtures) and the `PackLoader` legacy digest allowlist that only admitted them. A pack using either type is still rejected at load; `QuestionType` keeps both raw values so existing progress and issue records decode.
- `scripts/security_plus_consolidation.py`, the finished staging validator for the retired Security+ course, and its staging-topology tests (`tests/test_security_plus_final_review.py`). The three certification guard tests it carried now live in `tests/test_install_gate.py`; archived and staged question files are untouched.
