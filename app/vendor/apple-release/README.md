# Hash-bound Apple release runtime

`app/scripts/sync_release_tool.py` stages the reviewed central
`iterative_release.py`, its lock dependency, package initializer, and release
fixtures into tracked `runtime/`. `sync-manifest.json` binds every byte to
`app/design-authority-manifest.json`. The authority manifest uses portable
relative references: the central checkout is resolved from Quizzler's
repository root, and design reports are resolved inside that checkout. Only a
direct sibling reference such as `../apple_developer` may leave the Quizzler
repository; traversal, absolute paths, and symlink substitution fail closed.

Run the sync before credential-free release or hosted-CI work:

```sh
python3 app/scripts/sync_release_tool.py
python3 app/scripts/sync_release_tool.py --verify-only
```

Both operations fail closed if the central revision, a tool/fixture byte, either
design-authority report, the generated manifest, or a staged file drifts. No
Apple service, credential broker, or network operation is performed.

The Quizzler adapter consumes only format-v2 candidate directories: each
candidate owns `manifest.json`, `artifact-attestation.json`,
`transitions.jsonl`, and readiness observations. Legacy v1 manifests and shared
ledgers are rejected. The first candidate freezes the standard lane and
prebuild requirement set before archive creation; ASC upload resumes only the
candidate-attested IPA.
