# Quizzler Apple setup checklist

Attended, human-owned checklist for native plan Task 1.4. Complete this in one
reviewed batch. This document does not upload to TestFlight, promote a CloudKit
schema, or change production data.

## Public project values to verify

- [ ] App ID: `com.zerodelta.quizzler`; Team ID: `4CJ49V6QHW`.
- [ ] CloudKit container: `iCloud.com.zerodelta.quizzler.dev`.
- [ ] Its Development and Production environments are enabled for the bundle.
- [ ] The bundle ID is enabled for iCloud/CloudKit and push notifications.
- [ ] The container's Development environment has a private database/custom
      zone available; Production remains unchanged.
- [ ] App Store Connect contains the matching app and the current human has a
      role that can manage identifiers, capabilities, devices, profiles, and
      internal testers. Record the role and public app/bundle/container IDs.

## App Store Connect and device actions (human only)

- [ ] Confirm the matching bundle ID and its single CloudKit container; inspect
      CloudKit environments and push capability settings.
- [ ] Confirm or create the internal TestFlight group; add the intended tester
      and record only the group name/ID and tester status.
- [ ] Register one physical development device and record its model and UDID
      hash only. Do not paste a full UDID into evidence.
- [ ] If a permission fails, record the exact missing role/action and stop;
      do not retry by changing unrelated identifiers.

## Credential-free local preflight

Run from the repository root. These commands are read-only and must not print
private keys, API keys, JWTs, or profile contents:

```sh
python3 app/scripts/toolchain_capabilities.py --check
xcrun xctrace list devices
security find-identity -v -p codesigning
find "$HOME/Library/MobileDevice/Provisioning Profiles" -type f -name '*.mobileprovision' -print
```

The capability probe validates host tooling only; it does not validate signing
profiles or device availability.

Current preflight note (2026-08-12): no installed mobileprovision profiles and
no connected physical device were found. This is an incomplete gate, not a
failure of App Store Connect provisioning.

## Signing handoff

- [ ] After the Apple-side checks pass, the release owner explicitly authorizes
      Task 1.2's signing bootstrap to create the local Keychain CSR/private key,
      obtain the distribution certificate/profile, and import/install them.
- [ ] Record only public request/result IDs, certificate/profile IDs, team and
      bundle IDs, and SHA-256 hashes. Never record credentials, private keys,
      profile contents, or secret values.
- [ ] Confirm local manual signing/profile resolution with the credential-free
      preflight. Preserve the generated evidence for the release owner.
- [ ] Hand off the resolved signed Development build and public evidence to
      Task 1.3 for the disposable private-zone CloudKit probe.

Task 1.4 is complete only when every checkbox above has human evidence and the
Task 1.3 handoff is explicit. No TestFlight upload, production schema
promotion, production database write, or broad device/profile cleanup belongs
in this checklist.
