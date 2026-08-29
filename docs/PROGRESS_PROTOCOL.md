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
The bounded ledger is not an unbounded server-side tombstone. Once a caller no
longer retains an ID, it must not submit that ID as new work: an explicitly
supplied ID absent from the durable local ledger is refused. This prevents a
delayed local replay from being accepted as a new operation; callers create new
work only with a fresh ID.

The document has one monotonic global revision. A client may propose only the
next integer revision, and that proposal is accepted only by a conditional,
atomic write of the authoritative CloudKit snapshot: the write must carry the
current record change tag and use `.ifServerRecordUnchanged`. CloudKit's
record change tag therefore serializes successful revisions; there is no
server-side revision allocator. A failed condition is a visible conflict, not
an ordering result: the client must fetch the complete current snapshot,
rebase pending operations, and retry with a newly proposed next revision.
Operation ID is used only to break same-revision ordering during recovery (by
UTF-8 lexicographic order), never to make two competing writes successful.
Client timestamps are advisory metadata only. Clock skew cannot change
ordering, conflict results, or the reduced state.

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

If a base revision or change token is stale/expired, the client maps CloudKit's
conflict/token-expiry result to its application-level `rebase_required` state,
then explicitly fetches the current snapshot. It replaces its working document
from that snapshot, reapplies still-pending operations in canonical order, and
sends them with a fresh change tag and freshly proposed next revision. There is
no silent last-writer-wins path, and the browser never proxies private CloudKit
access.

If a full authoritative fetch proves that the custom zone has no
`ProgressSnapshot/current` record, this is an empty-zone recovery, not a
corrupt document. The client resets the zone-local cursor to revision `0`,
clears stale operation and issue acknowledgements, retains all local facts,
and replays those facts by conditional create. If another client creates the
snapshot concurrently, the create conflict is handled as an ordinary
conflict: fetch the full snapshot, rebase, and retry.

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

### Protocol negotiation and advertisement matrices

The server advertises its selected `protocol_version` and non-empty
`supported_protocol_versions` list on snapshot fetch (GET `/api/v1/progress`).
An internally inconsistent server advertisement is defined as a selected
`protocol_version` absent from its non-empty `supported_protocol_versions`.
Clients validate this advertisement upon startup/hydration before issuing any
mutation: if the advertisement is incompatible or internally inconsistent, the
client fails visibly (status `error`, code `refresh-failed`) and halts before
any mutation can be dispatched.

Every client mutation request carries the selected version in its payload
(`protocol_version: 1`) across all seven mutation endpoints (`import`,
`sessions`, `srs`, `quiz-completed`, `srs-rated`, `reset`, `cleanup-orphans`).

#### Server rejection and acceptance matrix

| Request `protocol_version` | Server Action | Result / Response | State Impact |
|---|---|---|---|
| Omitted (absent) | Accept (legacy omission default) | HTTP 200 / mutation applied | Revision advances |
| Integer `1` (supported) | Accept | HTTP 200 / mutation applied | Revision advances |
| Integer `!= 1` (e.g. `0`, `2`, `-1`) | Reject pre-write | HTTP 409 `incompatible_protocol` | Unchanged state |
| Malformed (e.g. `null`, `"1"`, `1.5`, `true`, `[]`, `{}`) | Reject pre-write | HTTP 409 `incompatible_protocol` | Unchanged state |
| Non-object request body | Reject pre-write | HTTP 400 `request body must be an object` | Unchanged state |

When the server rejects an incompatible or malformed mutation advertisement, it
returns HTTP 409 with:
```json
{
  "error": "incompatible_protocol",
  "protocol_version": 1,
  "supported_protocol_versions": [1]
}
```
No revision, snapshot, or operation record changes and the server state remains
strictly unchanged.

#### Browser rejection matrix

| Server Advertisement | Client Action | Visible State | Mutation Dispatch |
|---|---|---|---|
| `protocol_version: 1`, `supported: [1]` | Accept | Status `ready`, hydrated | Allowed |
| Omitted versions (legacy server) | Accept (defaults to `v1`) | Status `ready`, hydrated | Allowed (`protocol_version: 1`) |
| Inconsistent: selected absent from non-empty supported (e.g. `1` not in `[2]`, or `2` not in `[1, 3]`) | Reject on startup | Status `error`, `refresh-failed` | Blocked before mutation |
| Incompatible selected version (e.g. `2` with `[2]`) | Reject on startup | Status `error`, `refresh-failed` | Blocked before mutation |
| Mutation response 409 `incompatible_protocol` | Reject mutation | Status `error`, `incompatible-protocol` | Mutation fails |

The observable mutation states are `pending`, `applied`, `conflict`,
`rebase_required`, `encoded_size_refused`, `offline`, `corrupt_state`, and
`failed`. A failed or refused operation is never rendered as durable success.
