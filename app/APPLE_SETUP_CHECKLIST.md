# Quizzler Apple setup checklist

Attended, human-owned checklist for native plan Task 1.4. Complete this in one
reviewed batch. This document does not upload to TestFlight, promote a CloudKit
schema, or change production data.

The native iOS foundation and its local contract gates are implemented. The
checkboxes below are human-owned Apple signing, account, device, and release
readiness evidence; an unchecked item is an outstanding release gate, not an
unimplemented native feature. The approved `new_start` decision applies only
to pre-native quiz-progress migration; it does not reset or invalidate native
plan evidence.

## Where each action happens

| Where | Do these actions | Do not do these actions here |
| --- | --- | --- |
| [Apple Developer Account](https://developer.apple.com/account/) | Verify Team ID; configure the App ID, iCloud container association, iCloud/CloudKit and push capabilities; register the two physical devices. | Do not create a duplicate App ID or manually delete certificates, profiles, containers, or devices. |
| [App Store Connect](https://appstoreconnect.apple.com/) | Verify the existing app record and bundle ID; check **Users and Access**; confirm/create the intended **TestFlight → Internal Testing** group and add the tester. | Do not upload a build or assign a release candidate yet. |
| [CloudKit Console](https://icloud.developer.apple.com/) | Select `iCloud.com.zerodelta.quizzler.dev`; verify Development and Production are enabled and that Development has a selectable private database. | Do not reset Production or promote a schema. That is Task 5.1 in [PROMOTION.md](PROMOTION.md). |
| Xcode and the Mac | Run the credential-free preflight, then the attended signing bootstrap after the Apple-side checks pass; install/pair the signed Development build. | Do not paste private keys, profiles, JWTs, or credentials into this checklist. |
| Two physical iPhones | Sign into the intended iCloud account, enable iCloud for the app, install the signed build, and later run the two-device Production acceptance. | Do not substitute a simulator or an unsigned/Debug-only result for release evidence. |

## 1. Apple Developer Account — identifiers, capabilities, and devices

Open [developer.apple.com/account](https://developer.apple.com/account/) and
use **Certificates, Identifiers & Profiles**.

- [ ] Verify Team ID `4CJ49V6QHW`.
- [ ] Under **Identifiers**, locate App ID `com.zerodelta.quizzler`; do not
      create a second one.
- [ ] Confirm the App ID has iCloud/CloudKit and push-notification capability
      enabled, and is associated with
      `iCloud.com.zerodelta.quizzler.dev`.
- [ ] Confirm the container is the one existing container; do not create a
      replacement container.
- [ ] Under **Devices**, register both physical test devices. Record only each
      model and a SHA-256 hash of its UDID; never paste a full UDID here.
- [ ] Record the exact permission failure and stop if the account lacks an
      action. Do not change unrelated identifiers to work around it.

## 2. App Store Connect — app record, role, and internal testers

Open [appstoreconnect.apple.com](https://appstoreconnect.apple.com/).

- [ ] Under **Apps**, verify the existing Quizzler app record and bundle ID
      `com.zerodelta.quizzler`; do not create a duplicate app.
- [ ] Under **Users and Access**, verify the current user can manage this app
      and its internal TestFlight testers. Record the role and app ID only.
- [ ] Under **TestFlight → Internal Testing**, confirm or create the intended
      internal group and add the intended tester. Record only the group ID/name
      and tester status.
- [ ] Do not upload, process, assign, or distribute the build during Task 1.4.

## 3. CloudKit Console — environment availability only

Open [icloud.developer.apple.com](https://icloud.developer.apple.com/) and
select `iCloud.com.zerodelta.quizzler.dev`.

- [ ] Verify both **Development** and **Production** environments are enabled.
- [ ] In **Development**, select **Private Database** and verify it is
      available for the signed probe. The probe temporarily creates the exact
      zone `QuizzlerDevelopmentProbe-v1`, exercises it, and deletes it during
      cleanup; no persistent custom zone is expected afterward.
- [ ] Do not manually create `QuizzlerDevelopmentProbe-v1` or the production
      sync zone `QuizzlerProgress-v1`. The latter is created by the signed
      production acceptance path in Task 5.2.
- [ ] Leave **Production** unchanged. Schema capture and promotion are a later
      attended checkpoint, documented in [PROMOTION.md](PROMOTION.md).

Current preflight note (2026-08-19): no installed mobileprovision profiles and
no connected physical device were found. This is an incomplete gate, not a
failure of App Store Connect provisioning.

## 4. Xcode/macOS — signing handoff

- [ ] After the Apple-side checks pass, run the secret-free plan first:
      `python3 app/scripts/provision_signing.py --dry-run`.
- [ ] At an attended terminal, use only the fixed
      `quizzler-asc-provision` BWS consumer to authorize Task 1.2's signing
      bootstrap. It creates the local Keychain CSR/private key, obtains or
      reuses the distribution certificate/profile, and installs the profile.
      The command is:
      `bws-secret-exec quizzler-asc-provision -- python3 app/scripts/provision_signing.py --execute --approve --evidence-path app/releases/evidence/signing-bootstrap.json`
- [ ] Record only public request/result IDs, certificate/profile IDs, team and
      bundle IDs, and SHA-256 hashes. Never record credentials, private keys,
      profile contents, or secret values.
- [ ] Confirm local manual signing/profile resolution with the credential-free
      preflight. Preserve the generated evidence for the release owner.
- [ ] In Xcode's **Window → Devices and Simulators**, pair the two registered
      iPhones and install the resolved signed Development build.
- [ ] Hand off the signed Development build and public evidence to Task 1.3
      for the disposable private-zone CloudKit probe.

## 5. Credential-free local preflight

Run from the repository root after the Apple-side checks. These commands are
read-only and must not print private keys, API keys, JWTs, or profile contents:

```sh
python3 app/scripts/toolchain_capabilities.py --check
xcrun xctrace list devices
security find-identity -v -p codesigning
find "$HOME/Library/MobileDevice/Provisioning Profiles" -type f -name '*.mobileprovision' -print
```

The capability probe validates host tooling only; it does not validate signing
profiles or device availability. Current blockers are recorded in the active
plan; an unchecked item remains an open gate.

Task 1.4 is complete only when every checkbox above has human evidence and the
Task 1.3 handoff is explicit. No TestFlight upload, production schema
promotion, production database write, or broad device/profile cleanup belongs
in this checklist.
