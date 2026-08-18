"""Prepare a resumable, idempotent migration without mutating source stores.

This module intentionally stops at a local, hash-bound CloudKit operation
plan.  The native client performs the eventual conditional writes; Python
never proxies private CloudKit or claims that a dry run imported data.
"""

from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path
from typing import Any, Iterable

try:
    from export_progress import ExportError, load_inventory, validate_inventory
    from reconcile_progress import ReconciliationError, build_new_start_baseline, canonical_hash, reconcile_exports
except ModuleNotFoundError:  # pragma: no cover - package import in test runners
    from scripts.export_progress import ExportError, load_inventory, validate_inventory
    from scripts.reconcile_progress import ReconciliationError, build_new_start_baseline, canonical_hash, reconcile_exports


class MigrationError(ReconciliationError):
    """The migration cannot safely advance its durable local checkpoint."""


def build_plan(
    inventory: dict[str, Any],
    exports: Iterable[dict[str, Any]] = (),
    *,
    migration_epoch: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic plan; no source or CloudKit writes occur."""
    inventory = validate_inventory(inventory)
    epoch = migration_epoch or str(uuid.uuid4())
    export_list = list(exports)
    if inventory.get("path") == "new_start":
        if export_list:
            # A new start claims no history. Silently discarding a supplied
            # export would let an unverified source ride along with that claim.
            raise MigrationError(
                f"new_start forbids source exports (received {len(export_list)})"
            )
        baseline = build_new_start_baseline(inventory, epoch)
        return {
            "schema_version": 1,
            "kind": "migration_plan",
            "migration_epoch": epoch,
            "path": "new_start",
            "source_snapshot_hash": baseline["source_snapshot_hash"],
            "source_export_hash": None,
            "counts": {"sources": 0, "records": 0},
            "baseline": baseline,
            "cloudkit_operations": [
                {
                    "kind": "conditional_create_snapshot",
                    "expected_revision": 0,
                    "operation_id": "baseline",
                    "semantic_hash": baseline["cloudkit_baseline"]["semantic_hash"],
                    "import_claim": False,
                }
            ],
            "source_mutation": "forbidden",
        }
    if not export_list:
        raise MigrationError("a non-new-start path requires at least one verified export")
    # Verify every export against its own recorded evidence and against the
    # attended inventory before any plan derives an import from it.
    reconciled = reconcile_exports(export_list, inventory=inventory)
    # The epoch is established when the sources are exported, so a plan adopts
    # it rather than minting a new one; an explicit epoch must agree.
    if migration_epoch is None:
        epoch = reconciled["migration_epoch"]
    elif reconciled["migration_epoch"] != epoch:
        raise MigrationError(
            "exports were taken under a different migration epoch "
            f"(plan={epoch}, exports={reconciled['migration_epoch']})"
        )
    return {
        "schema_version": 1,
        "kind": "migration_plan",
        "migration_epoch": epoch,
        "path": inventory["path"],
        "source_snapshot_hashes": reconciled["source_snapshot_hashes"],
        "verified_exports": reconciled["verified_exports"],
        "source_export_hash": reconciled["semantic_hash"],
        "counts": {"sources": len(export_list), "records": reconciled["counts"]["records"]},
        "document": reconciled["document"],
        "cloudkit_operations": [
            {
                "kind": "conditional_import",
                "expected_revision": 0,
                "operation_id": f"migration/{epoch}",
                "semantic_hash": reconciled["semantic_hash"],
                "import_claim": True,
            }
        ],
        "source_mutation": "forbidden",
    }


def _state_for(plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "migration_state",
        "migration_epoch": plan["migration_epoch"],
        "plan_hash": canonical_hash(plan),
        "phase": "planned" if dry_run else "prepared",
        "dry_run": dry_run,
        "cloudkit_operations": copy.deepcopy(plan["cloudkit_operations"]),
        "source_mutation": "forbidden",
        "rollback": {"available": True, "source_restored": True, "cloudkit_mutation": "none"},
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_migration(
    plan: dict[str, Any],
    state_path: str | Path,
    *,
    dry_run: bool = True,
    resume: bool = False,
    rollback: bool = False,
) -> dict[str, Any]:
    """Advance or resume a local receipt; never performs a source/CloudKit write."""
    destination = Path(state_path)
    plan_hash = canonical_hash(plan)
    existing: dict[str, Any] | None = None
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MigrationError("migration checkpoint is unreadable") from error
        if existing.get("plan_hash") != plan_hash:
            raise MigrationError("resume checkpoint does not match the frozen plan")
    if rollback:
        if existing is None:
            raise MigrationError("cannot roll back without a migration checkpoint")
        rolled_back = copy.deepcopy(existing)
        rolled_back["phase"] = "rolled_back"
        rolled_back["rollback"] = {"available": True, "source_restored": True, "cloudkit_mutation": "none"}
        _write_json(destination, rolled_back)
        return rolled_back
    if existing is not None and resume:
        return existing
    if existing is not None and not resume:
        raise MigrationError("migration checkpoint already exists; use --resume or --rollback")
    state = _state_for(plan, dry_run=dry_run)
    _write_json(destination, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--export", action="append", default=[])
    parser.add_argument("--migration-epoch")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        exports = []
        for path in args.export:
            exports.append(json.loads(Path(path).read_text(encoding="utf-8")))
        plan = build_plan(inventory, exports, migration_epoch=args.migration_epoch)
        result = run_migration(
            plan,
            args.state,
            dry_run=args.dry_run,
            resume=args.resume,
            rollback=args.rollback,
        )
        print(json.dumps(result, sort_keys=True))
    except (OSError, json.JSONDecodeError, ExportError, MigrationError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
