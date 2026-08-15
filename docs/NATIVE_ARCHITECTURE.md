# Native architecture contract

This document is the architecture boundary for the native iOS client. It does
not replace the browser app: browser-local progress remains the default and
the browser must remain static, offline-capable, and secret-free (INV-5).

## Modules and authority

`QuizzlerKit` owns pack decoding, question identity, selection/SRS, progress
operations, issue reports, local persistence, and the CloudKit mapping. The
SwiftUI target owns presentation and accessibility. A local repository may
queue an operation while offline, but CloudKit is the shared authority once a
native client is paired. There is one progress document revision per user;
there are no per-pack revisions.

The client proposes `current revision + 1` only inside a conditional atomic
write of `ProgressSnapshot/current`. The write includes the record's current
CloudKit change tag and uses `.ifServerRecordUnchanged`; CloudKit serializes
successful proposals. CloudKit does not provide a separate revision allocator.
A condition failure is fail-visible and requires a full snapshot fetch and
rebase before retrying. Operation ID breaks same-revision recovery ordering
only; it cannot make a competing write succeed. The browser never proxies
private CloudKit access.

Question packs are immutable, validated JSON assets shipped with the app. They
never enter CloudKit. CloudKit stores only progress operations, one bounded
snapshot, and issue reports in the user's private database.

## Question-type boundary

The browser's five real schema types are exactly:

| Type | New-pack install | Native renderer |
| --- | --- | --- |
| `multiple_choice` | allowed | single choice |
| `scenario_multiple_choice` | allowed | scenario + single choice |
| `multiple_select` | allowed | select all |
| `true_false` | compatibility only | legacy renderer |
| `matching` | compatibility only | legacy renderer |

Only the first three rows are installable in a newly gated pack. The final two
rows are compatibility renderers for audited legacy content.

`true_false` and `matching` are accepted only when the complete, pre-native
pack digest is present in a checked-in legacy allowlist. They are release-
excluded compatibility fixtures, not an escape hatch for a new or modified
pack. Unknown types and changed legacy digests fail closed. This preserves the
existing INV-7 prohibition on installing new `true_false`/`matching` content.

## Identity and privacy

Mastery and SRS entries are keyed by the tuple
`(courseID, packID, questionID)`. A session is course-scoped, but every answer
stores its own `packID` and `questionID` (and the course ID), so a combined
session cannot collapse answers from different packs. Question IDs are never
interpreted without their pack tuple.

The issue-report record contains only the question tuple, question type,
application/build version, optional selected response, and the user's
description. It excludes question text, answer explanations, full session
history, mastery, SRS state, unrelated progress, account identifiers, device
paths, and credentials. Status events contain state and counts only, never
question content or identifiers.

## CloudKit record and zone contract

Native sync uses the private database and one versioned custom zone,
`QuizzlerProgress-v1`, owned by the current iCloud account. The zone's change
subscription is versioned with the protocol. Record names are stable and
opaque:

* `ProgressOperation/<operationID>` — immutable operation, retained for at
  most 4,096 records and 30 days;
* `ProgressSnapshot/current` — the bounded document and compaction watermark;
* `QuestionIssue/<issueID>` — immutable, privacy-minimal report.

`CKSyncEngine` state and the zone change token are persisted atomically with
the local repository. Missing or corrupt state/token is recoverable: discard
only the unreadable engine state, fetch the full `ProgressSnapshot/current`,
rebase pending operations, and visibly report `rebasing`/`recovery_required`.
Never treat corruption as an empty document and never silently overwrite the
server snapshot. Account/container changes clear the token and require the
same full-snapshot recovery path.

An explicit full authoritative fetch that proves the custom zone has no
`ProgressSnapshot/current` record is the separate empty-zone recovery case.
Reset only the zone-local cursor to revision `0`, clear stale operation and
issue acknowledgements, retain local facts, and replay them by conditional
create. A concurrent creator conflict must fetch the full snapshot, rebase,
and retry; it must not overwrite the newly created snapshot.

Operations are pruned only after a published snapshot covers them, they are
outside both retention limits, and they are not pending in persisted
`CKSyncEngine` state. Deletes are batched below service limits and are
crash-resumable. A stale client or expired token must rebase from the full
snapshot before sending new operations.

See [PROGRESS_PROTOCOL.md](PROGRESS_PROTOCOL.md) for the wire envelope,
canonicalization, ordering, and refusal rules. See [REPORT_SCHEMA.md](REPORT_SCHEMA.md)
for session and issue-report fields.
