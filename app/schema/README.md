# Captured CloudKit schemas

Raw Development and Production captures are local release evidence and are not
committed. Each capture must be a JSON object with `formatVersion`,
`containerIdentifier`, `environment`, `capturedAt`, and `recordTypes`.

Compare captures offline with the disposition reviewed in
`app/release-config.toml`:

```sh
python3 app/scripts/cloudkit_schema_compatibility.py \
  app/releases/evidence/development-schema.json \
  app/releases/evidence/production-schema.json \
  --disposition new-container
```

The comparator permits only additive record types, optional fields, and
indexes. Removed or changed fields/indexes and newly required fields fail.
Different container identifiers require the explicit `new-container`
disposition; the comparator never promotes or resets a schema.

Physical-device release proof is a separate candidate-local document:
`app/release-device-evidence.schema.json`. It requires exactly one physical
device and a signed preflight build with Production entitlements for the
configured private Production container. The final IPA remains the subject of
the separate Production schema attestation.
