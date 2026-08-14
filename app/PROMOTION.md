# CloudKit Production promotion

CloudKit schema promotion is a separate attended Apple Dashboard checkpoint. Do not
run it from `deploy-testflight`, and never reset Production. Capture the reviewed
Development and Production schemas, use the Task 4.3 offline comparator, then bind
the resulting evidence to the immutable v2 candidate before TestFlight upload.

`deploy-testflight --attended` will not perform a schema mutation. It prepares the
candidate-local archive/IPA attestation, then accepts the Production schema bound
to that IPA and one signed physical-device *preflight-build* observation. The
preflight observation binds the frozen candidate to signed-build, signature, and
Production-entitlement hashes; it never claims the later App Store IPA was
installed. The command stops before upload if either evidence set is missing or
stale. It re-enters through the fixed
`quizzler-testflight-upload` BWS consumer, never accepts credentials or provider
commands from an operator, and retains only candidate-local 0600 state. Legacy v1
manifests and shared ledgers are rejected.

The existing internal group must be captured as checked evidence at
`app/releases/evidence/testflight-internal-group.json` before any assignment.
It must name the exact ASC app and group IDs, this bundle ID, and assert
`isInternalGroup: true`; group names are never used as identifiers.

The existing approved export-compliance declaration must likewise be captured at
`app/releases/evidence/testflight-compliance.json`. The attended provider creates
only the documented build-upload/file records, uploads only Apple-provided HTTPS
ranges, and writes the final receipt hash record after exact build/group checks.
