# Release evidence

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

## Release physical-device evidence

`release-device-evidence.json` conforms to
`app/release-device-evidence.schema.json`. It records exactly one physical
device observation for the frozen candidate source/version/build, together
with SHA-256 attestations for the signed preflight bundle, its code-signature
evidence, and its entitlements evidence. The signed preflight bundle is not
the final App Store IPA: that IPA cannot be installed before TestFlight upload.

The verifier requires the candidate's configured bundle ID and team, the
Production CloudKit environment, and exactly the configured private Production
container. It stores only opaque hashes and public identifiers. Do not include
device serials, account IDs, certificate material, paths, logs, or pass flags.
The Production schema evidence remains separately bound to the final IPA after
archive creation.
