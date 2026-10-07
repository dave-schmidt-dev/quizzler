"""Issuance receipts: the ledger's record of each certification block it minted.

After ``campaign_finalize.certify_campaign`` writes a certification block, it
appends a receipt to ``ledger["issuance_receipts"]``.  The receipt pins the
block it wrote for one campaign frontier: a digest of the block without its
``question_stamps``, a copy of the stamps, and a digest of the whole block.

Trust model: ledgers and pack blocks stay unauthenticated JSON (no HMAC).  A
receipt is a consistency binding.  It proves the stamping tool, not a hand
edit or another campaign, wrote that block for that frontier, which blocks
pipeline mistakes and single-file forgery.  Someone who can hand-write both
the ledger and the pack can still forge the pair, exactly as anyone who can
run ``--certify-campaign`` on hand-written evidence can today.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign_snapshot import CampaignError, _digest  # noqa: E402

RECEIPT_FIELDS = (
    "campaign_snapshot_fingerprint",
    "certification_header_digest",
    "question_stamps",
    "certification_digest",
    "issued_at",
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def header_digest(block: dict) -> str:
    """Return the digest of a certification block without its stamp registry."""
    return _digest({key: value for key, value in block.items()
                    if key != "question_stamps"})


def build_receipt(block: dict, frontier_fingerprint: str) -> dict:
    """Return the receipt for one certification block as written.

    Args:
        block: The certification block exactly as written to the pack.
        frontier_fingerprint: The campaign frontier the block certifies.

    Returns:
        The receipt to append to ``ledger["issuance_receipts"]``.
    """
    return {
        "campaign_snapshot_fingerprint": frontier_fingerprint,
        "certification_header_digest": header_digest(block),
        "question_stamps": dict(block["question_stamps"]),
        "certification_digest": _digest(block),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_receipts(receipts: Any) -> None:
    """Raise :class:`CampaignError` unless every receipt has the exact shape.

    Args:
        receipts: The ledger's ``issuance_receipts`` value.

    Raises:
        CampaignError: If the list or any receipt is malformed.
    """
    if not isinstance(receipts, list):
        raise CampaignError("ledger issuance_receipts must be a list")
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != set(RECEIPT_FIELDS):
            raise CampaignError(
                f"an issuance receipt must carry exactly {sorted(RECEIPT_FIELDS)}")
        for field in ("campaign_snapshot_fingerprint",
                      "certification_header_digest", "certification_digest"):
            value = receipt[field]
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise CampaignError(
                    f"issuance receipt {field} must be a sha256 digest")
        stamps = receipt["question_stamps"]
        if (not isinstance(stamps, dict)
                or not all(isinstance(qid, str) and isinstance(digest, str)
                           for qid, digest in stamps.items())):
            raise CampaignError(
                "issuance receipt question_stamps must map qid to digest")
        issued_at = receipt["issued_at"]
        if not isinstance(issued_at, str) or not issued_at.endswith("+00:00"):
            raise CampaignError("issuance receipt issued_at must be UTC")
