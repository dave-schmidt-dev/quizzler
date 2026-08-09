# Progress protocol v1

The browser/server emulator and native client share this versioned contract.
Implementations compare semantic fields before producing a canonical evidence
digest; JSON member order alone is never meaningful.

## Envelope

```json
{
  "protocol": "quizzler-progress",
  "schema_version": 1,
  "document_revision": 42,
  "actor_id": "device-a",
  "operation_id": "018f2c0e-7d7a-7b1a-9b12-0c2d8f5e4a11",
  "created_at": "2026-08-08T12:00:00.000Z",
  "session_details": [],
  "aggregate": { "sessions_total": 0, "correct": 0, "answered": 0 },
  "mastery": {},
  "srs": {},
  "compaction": { "version": 1, "watermark_revision": 0 }
}
```

`operation_id` is generated once for a user intent, persisted before sending,
and reused for every retry of the byte-for-byte/semantically identical payload.
The server applies an operation at most once by that ID. A replay within the
retention window returns the original result and does not advance the document
twice. A refused operation has no effect: it may reuse its ID only when the
retry payload is byte-for-byte/semantically identical; any changed payload or
new user intent must receive a fresh operation ID.

The document has one monotonic global revision. The canonical reduction order
is `(server_assigned_global_revision, operation_id)`; equal revisions are
ordered by the UTF-8 lexicographic operation ID. Client timestamps are
advisory metadata only. Clock skew cannot change ordering, conflict results,
or the reduced state.

## Canonical representation and comparison

* IDs and enum values are Unicode NFC strings encoded as UTF-8; empty IDs are
  invalid.
* Counts, revisions, durations, and timestamps' millisecond components use
  signed/unsigned integers with no decimal or exponent notation. Percentages
  use integer basis points (`0`–`10000`), never binary floating point.
* Timestamps are UTC RFC 3339 with exactly three fractional digits. They are
  retained for display/audit but are not ordering inputs.
* Arrays whose order is meaningful (sessions, answers, operation order) retain
  that order. Set-like arrays and object keys are sorted by canonical UTF-8
  bytes. Missing optional fields are normalized to their documented defaults;
  `null` is distinct from a present value.
* Semantic comparison first normalizes these representations and compares
  every declared field. Only an equal semantic value is serialized as
  canonical JSON (sorted keys, compact separators, UTF-8) and hashed with
  SHA-256 for evidence. A digest never substitutes for field comparison.

## Retention, snapshots, and rebase

`session_details` contains the most recent 200 completed sessions. Before a
detail is pruned, its facts are folded into the durable `aggregate`, `mastery`,
and `srs` snapshots. Session-detail retention is independent of operation
retention: a session may leave the detail window while its aggregate remains
durable.

At most 4,096 operation records and 30 days of operation history are retained
(the earlier boundary wins). Pruning requires snapshot coverage, excludes
pending operations, and advances `compaction.watermark_revision` only after
the snapshot is durably published. The process is resumable across crashes.

If a base revision or change token is stale/expired, the server returns the
current snapshot and a `rebase_required` status. The client replaces its
working document from that snapshot, reapplies still-pending operations in
canonical order, and sends them with fresh expected revision. There is no
silent last-writer-wins path.

Before any mutation, the complete encoded envelope is measured against the
negotiated maximum. If it exceeds the limit, the operation is refused with
`encoded_size_refused`; no revision, snapshot, or operation record changes and
the UI must keep the mutation pending/failed rather than displaying success.

## Versioning and failures

`schema_version: 1` is the only accepted native version in this phase. Unknown
required fields or incompatible versions fail visibly with
`incompatible_version` and do not mutate the source. Version negotiation is
explicit; an old browser/server pair continues using its existing protocol or
reports an actionable incompatibility.

The observable mutation states are `pending`, `applied`, `conflict`,
`rebase_required`, `encoded_size_refused`, `offline`, `corrupt_state`, and
`failed`. A failed or refused operation is never rendered as durable success.
