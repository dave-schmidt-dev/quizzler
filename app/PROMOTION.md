# CloudKit Production promotion

CloudKit schema promotion is a separate attended Apple Dashboard checkpoint. Do not
run it from `deploy-testflight`, and never reset Production. Capture the reviewed
Development and Production schemas and use the Task 4.3 offline comparator. This
is independent CloudKit QA and is not a pre-upload TestFlight requirement.

`deploy-testflight --attended` will not perform a schema mutation or require a
Production schema/device evidence packet. It prepares the candidate-local
archive/IPA attestation, then re-enters through the fixed
`quizzler-testflight-upload` BWS consumer, never accepts credentials or provider
commands from an operator, and retains only candidate-local 0600 state. Legacy v1
manifests and shared ledgers are rejected.

The existing internal group must be captured as checked evidence at
`app/releases/evidence/testflight-internal-group.json` before any assignment.
It must name the exact ASC app and group IDs, this bundle ID, and assert
`isInternalGroup: true`; group names are never used as identifiers.

The attended provider reads the exact build's export-compliance state. A `false`
standard-encryption exemption needs no declaration. A `true` state requires the
existing approved declaration captured at
`app/releases/evidence/testflight-compliance.json`. It creates only the documented
build-upload/file records, uploads only Apple-provided HTTPS ranges, and writes the
final receipt hash record after exact build/group checks.
