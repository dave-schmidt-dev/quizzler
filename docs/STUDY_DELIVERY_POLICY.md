# Study Delivery Policy

**Date:** 2026-09-14
**Owner-approved process update:** policy transcription only; no runtime or algorithm changes.

## 1) Study-ready outcome and starter scope (must be first)

For every new course, begin with a **single useful study session** as the first outcome.

- Start with 20–30 source-grounded, independently checked questions.
- Target only the next needed module/topic, with explicit statement that coverage is incomplete.
- Do not design or generate the full course before study starts.
- After study begins, expand scope using the whole-course process as needed.

For each starter milestone, record in the course `BUILD_NOTES.md`:

- `source`: exact grounding artifact(s)
- `scope`: explicit topic/module boundary
- `usable_count`: number of checked/usable questions
- `remaining_coverage`: what is left intentionally un-included
- `private_study_readiness`: yes/no with evidence
- `first_session_available`: launch date or status

## 2) Two readiness states (do not collapse)

- **Private-study readiness** requires:
  - Correct, decodable content
  - Independent fact-check of every included question against the source
  - Explicit limited scope
  - No known `wrong-answer`, high-confidence `misleading-explanation`, or multi-defensible ambiguity
- **Whole-course/release readiness** still requires full-course coverage and the active INV-8 objective review + human attestation flow.
- `hybrid_verify.py --certify-campaign` is deterministic stamp output, not self-certification authority.

## 3) Evidence lifecycle and campaign inheritance

- Preserve valid exact-question evidence across campaigns when the question/content is unchanged.
- Any changed question, changed shared source/context/contract, or invalidated context requires fresh evidence checks.
- Missing, stale, incomplete, or unapproved evidence blocks readiness.
- Question hash metadata alone is not proof of certification.
- Cross-campaign evidence inheritance (depth 1) is supported via `certification_campaign.py init --inherit-from-ledger <prior-ledger>` when anchored on a certified prior v2 ledger. The prior ledger must carry an issuance receipt matching its frontier, must not have been inherited or quarantined, and must match the new base snapshot on every frozen non-question input (pack identity, subject, source_directive, waivers, grounding, verifier profile/model).
- Per question, evidence inherits only if the pack stamp equals the prior receipt's stamp and the question's content hash, and its full-dict hash equals the prior frontier's hash. Uncovered questions require a tool-computed `inheritance-recheck` round.
- Stamping via `--certify-campaign` reruns Layer A only, with no reviewer call. Legacy (snapshot v1) ledgers cannot be inherited; their next campaign pays one census.

## 4) Defect handling and quarantine

- Block on factual defects and ambiguity.
- Structural and integrity failures still block unsafe content.
- Treat style/difficulty/distractor quality as advisories; route to backlog and avoid repeated perfection loops.
- Quarantine individual questionable questions when needed via `scripts/pack_quarantine.py quarantine --pack P --qid X --reason "..."`. This moves questions to `question-packs/<course>/_quarantine/<pack>.json` and records a top-level `partial_install` marker.
- `scripts/certification_campaign.py begin-quarantine --ledger L --pack P` freezes the reduced frontier; the retained subset must be re-certified via `--certify-campaign`.
- Partial packs install only in Debug builds (`--allow-partial`); Release builds and TestFlight/snapshot paths refuse them.
- The app labels an installed partial pack: `"Partial: N of M reviewed questions installed; K held for review"`.
- User progress for held questions is kept in the progress store, not counted toward active coverage, and returns automatically on restore (`scripts/pack_quarantine.py restore`).
- Even under `--allow-partial`, the retained subset must pass all quality bars: lint criticals (including L23 blueprint coverage), course-level distribution aggregates, and fresh certification over the retained questions.
- Never forge narrowed official syllabus metadata, and never use non-strict preview mode as study-ready evidence.

## 5) Campaign cadence and reviews

- Default process: one concise plan + one independent review.
- Run fixes in one batch pass, then targeted rechecks of changed/dependent content.
- Further review must answer a concrete unresolved correctness question; advisory polish does not reopen the cycle.
- Do not launch endless full-bank rediscovery.
- Existing mandatory, distinct review roles are not waived; if conflicts exist, record them and do not restart a frozen plan.
- No roster changes are introduced by this process update.

## 6) Operational boundary

- Collect human spot-check feedback during actual private study and resolve reported questions in small correction batches. This does not imply release attestation has occurred.
- Infrastructure work must remove a demonstrated obstacle to the next study session using the smallest scoped fix. A pipeline overhaul is not a prerequisite.
- Apply these process/scheduling changes immediately.
- Scoped private-study installation via quarantine (`pack_quarantine.py`), Debug partial install (`--allow-partial`), and receipt-bound cross-campaign evidence inheritance (`certification_campaign.py init --inherit-from-ledger`) are implemented and enforced by the install gate. Release and distribution gates remain strictly whole-pack.
- Existing CySA private-study permission is **course-specific** only; it is not a global exception.
