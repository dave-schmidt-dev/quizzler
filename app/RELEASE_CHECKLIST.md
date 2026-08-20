# TestFlight release checklist

- Confirm the v2 candidate source identity is immutable and current. The
  attended workflow creates and binds the signed production archive/IPA and
  candidate-local artifact attestation before it uploads. Physical-device and
  CloudKit Production checks are independent attended QA activities; neither is
  required before the TestFlight upload.
- Review the candidate marketing version and build number. A resume must retain
  both values and the candidate-local final signed IPA attestation. Legacy v1
  manifests and shared ledgers are not resumable.
- Confirm the existing Internal Testers group has reviewed, checked evidence at
  `app/releases/evidence/testflight-internal-group.json`. The release does not
  create groups or select them by name.
- Confirm the uploaded build's `usesNonExemptEncryption` state. The app declares
  the standard-encryption exemption; when ASC reports `false`, no declaration is
  needed. A `true` build requires checked approved-declaration evidence.
- Run `app/deploy-testflight --attended` from this checkout while a release owner
  is present. The first invocation re-enters only through the fixed
  `quizzler-testflight-upload` BWS consumer. Status is emitted on stderr.
- If it stops before `upload-bound`, fix local evidence or signing and rerun. No
  App Store Connect mutation has occurred.
- If it stops after `upload-bound`, rerun the same command. It will poll and
  finish the bound ASC build; it must not increment or upload another build.
- Verify the exact version/build appears for the intended Internal Testers group in
  App Store Connect and in the user-visible TestFlight client.

The workflow never accepts credentials through arguments, files, or stdout. Its
provider uses the broker-injected ASC JWT only in memory and sends only the
typed, fixed ASC requests. It refuses malformed or incomplete server upload
operations before sending IPA bytes.
