# Captured CloudKit schemas

Raw Development and Production captures are local release evidence and are not
committed. `cktool export-schema` produces CloudKit Schema Language text, so
normalize each raw export locally before comparison:

```sh
python3 app/scripts/cloudkit_schema_compatibility.py normalize \
  app/releases/evidence/development-schema.ckdb \
  --output app/releases/evidence/development-schema.json \
  --container-id iCloud.com.zerodelta.quizzler.dev \
  --environment Development \
  --captured-at 2026-08-14T12:00:00Z
```

The normalizer is offline and verify-only: it never invokes CloudKit tooling.
It binds the exact raw export bytes as `sourceSha256` and fails closed for
unsupported schema grammar. Each normalized capture is a JSON object with
`formatVersion`, `containerIdentifier`, `environment`, `capturedAt`,
`sourceSha256`, and `recordTypes`. CloudKit's implicit system fields are
required; custom fields are optional because the schema language has no custom
required-field marker.

Compare captures offline with the disposition reviewed in
`app/release-config.toml`:

```sh
python3 app/scripts/cloudkit_schema_compatibility.py \
  app/releases/evidence/development-schema.json \
  app/releases/evidence/production-schema.json \
  --disposition same-container
```

The comparator permits only additive record types, optional fields, and
indexes. Removed or changed fields/indexes and newly required fields fail.
The two captures use the same container with distinct CloudKit environments, so
the required disposition is `same-container`; a different container requires
the explicit `new-container` disposition. The comparator never promotes or
resets a schema.

Physical-device release proof is a separate candidate-local document:
`app/release-device-evidence.schema.json`. It requires exactly one physical
device and a signed preflight build with Production entitlements for the
configured private Production container. The final IPA remains the subject of
the separate Production schema attestation.
