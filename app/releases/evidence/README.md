# Release evidence

The native iOS foundation is implemented; these artifacts capture human-owned
release evidence rather than unfinished native implementation. Candidate 17 is
currently release-tooling-only. Its TestFlight upload was not completed and must
not be inferred from candidate preparation or local gate results.

Development probe evidence contains only public status values and stable
identifiers. Do not record CloudKit record fields, account identifiers,
certificate private material, tokens, or other secrets.

The signed Development CloudKit probe is human-attended and explicitly opt-in.
Its disposable private zone is `QuizzlerDevelopmentProbe-v1`; it must never be
run against the public database. A missing entitlement or iCloud account is a
visible failure, not a successful skipped probe.

Each CloudKit operation has an attended 30-second hard caller-return bound.
Timeout or cancellation publishes its terminal status before returning; it does
not wait for an uncooperative CloudKit request or begin a second cleanup
request. Use the separately opt-in exact-zone recovery probe on the next
attended attempt if cleanup remains necessary.

The Phase 1 contract gate requires the ignored
`development-cloudkit-probe.json`. After an attended signed Debug Development
device run, call `record_evidence` in
`app/scripts/development_probe_evidence.py` with the machine-readable terminal
result and signed app. Verification must receive the same two local source
artifacts explicitly (via `--xcresult`/`--signed-app`, or the corresponding
`QUIZZLER_DEVELOPMENT_PROBE_*` variables used by `test-gate.sh`). The live
XCTest is not coupled to a bundle digest. Verification checks the XCTest pass
and required probe identity, then re-checks signed entitlements. A schema-valid
JSON file alone is rejected.
The evidence file remains redacted:
no account, device, record, raw-log data, or source paths belong in it.

## Optional CloudKit/device qualification evidence

`release-device-evidence.json` records a signed physical-device observation
for CloudKit/device qualification, together with
SHA-256 attestations for the signed preflight bundle, its code-signature
evidence, and its entitlements evidence. The record binds its opaque device ID
to the candidate, source digest, Production container, and observed semantic
state SHA-256. Device evidence IDs are one-way SHA-256 values, never serial
numbers or UDIDs. The signed preflight bundle is not
the final App Store IPA: that IPA cannot be installed before TestFlight upload.

The verifier requires the candidate's configured bundle ID and team, the
Production CloudKit environment, and exactly the configured private Production
container. It stores only opaque hashes and public identifiers. Do not include
device serials, account IDs, certificate material, paths, logs, or pass flags.
Production schema evidence may be bound to a candidate for the independent
CloudKit promotion record. It is not required before TestFlight; the final IPA
remains separately bound by `artifact-attestation.json` after archive creation.

Native archive preparation enforces INV-8 directly: `scripts/build_pack_assets.py`
requires every installed pack's current certification metadata and content hash
to pass `pack_cert.certification_fresh()`. A separate candidate-bound
`inv8-certification.json` is not a TestFlight prerequisite.

Before an attended deployment trigger, add a dated, candidate-current
screen-by-screen walkthrough that covers every reachable screen and control,
disabled and recovery states, and system-owned sheets. Older walkthroughs are
historical evidence only; they do not qualify a later candidate.
