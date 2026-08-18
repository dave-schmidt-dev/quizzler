# Progress-source inventory

Before native progress sync is enabled, record which existing study-progress
sources are authoritative. The inventory has one terminal path: `one_source`,
`multi_source`, or `new_start`.

`new_start` is an explicit disposition that no progress was recovered. It is
not a migration claim, and it must include affirmative approval plus zero
recovered sources and records. Approval must also carry an opaque
`local_session_ref` attestation: a 64-hex reference recorded during the
attended confirmation. The tracked example contains a placeholder and is
deliberately rejected as an inventory; copying it cannot manufacture
attended approval. For this project, the only active pack is `cissp`, and it
has not been started.

The example is the tracked schema reference, not an approval record. The
concrete inventory is local and gitignored; it contains counts, pack scope,
and the opaque attestation only, never raw study data, identities, device
details, or browser details.

## Task 3.4 boundary

The approved `new_start` path is a terminal, zero-source decision. The
exporter refuses to inspect a source for this path and the migration planner
emits only a local, hash-bound description of a revision-0 conditional
`ProgressSnapshot/current` create. It contains no import claim and performs no
CloudKit operation. The native client owns the eventual conditional write.

For a source path, exports are read-only and fenced by before/after source
hashes. A changed source, an empty/near-empty source, a missing pack identity,
an incompatible schema, or an irreconcilable duplicate blocks the plan. Plans
and receipts are dry-run, idempotent, resumable, and rollback-capable; source
stores remain untouched throughout.

### Export verification precedes any plan

No import or new-start claim may be derived from an unverified export. Before
`scripts/migrate_progress.py` builds a conditional import plan,
`scripts/reconcile_progress.py` verifies every envelope against the evidence it
carries about itself and against the attended inventory:

- envelope shape, `schema_version`, `kind`, source kind, epoch, and pack scope;
- the recorded `source_export_hash` against the document's canonical hash;
- the recorded `counts` against the document's measured semantic counts;
- the inventory's `counts.sources` against the number of verified exports, and
  its `counts.records` against the reconciled (deduplicated) record total —
  reconciled rather than per-source sum, because deduplication is what an
  import actually writes;
- one shared migration epoch, with no source exported twice.

Every rejection names the exact measured discrepancy (recorded versus
measured), so an operator can see what an export claims against what it
contains. A plan adopts the exports' epoch rather than minting a new one; an
explicitly supplied epoch must agree. A `new_start` inventory refuses to carry
a source export at all, so an unverified source cannot ride along with a
zero-source claim. The resulting plan records `verified_exports`, binding it to
the exact evidence that passed.
