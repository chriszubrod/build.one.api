"""
Read-only measurement: ReimburseCharge source fingerprint match rates (U-242).

Live QBO GET/query + SELECT-only SQL. Never writes business/staging data (no
qbo.ReimburseCharge, no dbo.Sync, no QBO write endpoints) — the one exception
is routine OAuth token refresh on qbo.Auth if the stored token is near/past
expiry, identical housekeeping every other QBO-touching script in this codebase
performs on every call.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/analyze_rc_source_fingerprint.py
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
from integrations.intuit.qbo.reimburse_charge.business.fingerprint import (
    SOURCE_TXN_TYPES,
    CandidateIndex,
    RcBaseLine,
    SourceCandidate,
    build_candidate_index,
    collect_linked_txn_type_counts,
    match_outcome,
    parse_rc_base_lines,
    tier_match_indexed,
)
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("analyze_rc_source_fingerprint")

_BILL_SOURCE_SQL = """
SELECT bl.CustomerRefValue, bl.Amount, bl.Description, bl.ItemRefValue,
       qb.TxnDate, qb.DocNumber, qb.VendorRefName, bl.Id AS QboBillLineId,
       map.BillLineItemId
FROM qbo.BillLine bl
JOIN qbo.Bill qb ON qb.Id = bl.QboBillId
LEFT JOIN qbo.BillLineItemBillLine map ON map.QboBillLineId = bl.Id
WHERE qb.RealmId = ?
"""

_PURCHASE_SOURCE_SQL = """
SELECT pl.CustomerRefValue, pl.Amount, pl.Description, pl.ItemRefValue,
       qp.TxnDate, qp.DocNumber, qp.EntityRefName, pl.Id AS QboPurchaseLineId,
       map.ExpenseLineItemId
FROM qbo.PurchaseLine pl
JOIN qbo.Purchase qp ON qp.Id = pl.QboPurchaseId
LEFT JOIN qbo.PurchaseLineExpenseLineItem map ON map.QboPurchaseLineId = pl.Id
WHERE qp.RealmId = ?
"""

_REPORTS_DIR = Path(__file__).resolve().parent / "_reports"


def _read_staged_rc_count(conn, realm_id: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM qbo.ReimburseCharge WHERE RealmId = ?",
        (realm_id,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _fetch_bill_candidates(conn, realm_id: str) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    cur = conn.cursor()
    cur.execute(_BILL_SOURCE_SQL, (realm_id,))
    for row in cur.fetchall():
        candidates.append(
            SourceCandidate(
                source_type="BillLineItem",
                customer_ref_value=getattr(row, "CustomerRefValue", None),
                amount=Decimal(str(row.Amount)),
                description=getattr(row, "Description", None) or "",
                txn_date=str(getattr(row, "TxnDate", "") or ""),
                item_ref_value=getattr(row, "ItemRefValue", None),
                doc_number=getattr(row, "DocNumber", None),
                vendor_or_entity_name=getattr(row, "VendorRefName", None),
                qbo_line_id=int(row.QboBillLineId),
                mapped_dbo_id=getattr(row, "BillLineItemId", None),
            )
        )
    return candidates


def _fetch_purchase_candidates(conn, realm_id: str) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    cur = conn.cursor()
    cur.execute(_PURCHASE_SOURCE_SQL, (realm_id,))
    for row in cur.fetchall():
        candidates.append(
            SourceCandidate(
                source_type="ExpenseLineItem",
                customer_ref_value=getattr(row, "CustomerRefValue", None),
                amount=Decimal(str(row.Amount)),
                description=getattr(row, "Description", None) or "",
                txn_date=str(getattr(row, "TxnDate", "") or ""),
                item_ref_value=getattr(row, "ItemRefValue", None),
                doc_number=getattr(row, "DocNumber", None),
                vendor_or_entity_name=getattr(row, "EntityRefName", None),
                qbo_line_id=int(row.QboPurchaseLineId),
                mapped_dbo_id=getattr(row, "ExpenseLineItemId", None),
            )
        )
    return candidates


def _pct(n: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(100.0 * n / total, 2)


def _summarize(per_line: list[tuple[RcBaseLine, list[SourceCandidate]]]) -> dict[str, Any]:
    """Tally one tier's (rc_line, matches) pairs into outcome counts/percentages."""
    outcomes = Counter(match_outcome(matches) for _, matches in per_line)
    total = len(per_line)
    return {
        "unmatched": outcomes["unmatched"],
        "unique": outcomes["unique"],
        "ambiguous": outcomes["ambiguous"],
        "pct_unmatched": _pct(outcomes["unmatched"], total),
        "pct_unique": _pct(outcomes["unique"], total),
        "pct_ambiguous": _pct(outcomes["ambiguous"], total),
        "per_line": per_line,
    }


def _tier_stats_for_lines(
    base_lines: list[RcBaseLine],
    candidate_index: CandidateIndex,
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for tier in ("A", "B", "C"):
        per_line = [(rc_line, tier_match_indexed(rc_line, candidate_index, tier)) for rc_line in base_lines]
        stats[tier] = _summarize(per_line)
    return stats


def _derive_tier_stats(
    all_stats: dict[str, Any],
    line_predicate,
) -> dict[str, Any]:
    """Partition precomputed per_line match results without re-running matching."""
    stats: dict[str, Any] = {}
    for tier in ("A", "B", "C"):
        filtered_per_line = [
            (rc_line, matches)
            for rc_line, matches in all_stats[tier]["per_line"]
            if line_predicate(rc_line)
        ]
        stats[tier] = _summarize(filtered_per_line)
    return stats


def _split_by_invoiced(base_lines: list[RcBaseLine]) -> tuple[list[RcBaseLine], list[RcBaseLine]]:
    invoiced = [ln for ln in base_lines if ln.has_been_invoiced is True]
    uninvoiced = [ln for ln in base_lines if ln.has_been_invoiced is not True]
    return invoiced, uninvoiced


def _actionable_pct(tier_c_stats: dict[str, Any]) -> dict[str, Any]:
    unique_matches = [
        (rc_line, matches[0])
        for rc_line, matches in tier_c_stats["per_line"]
        if match_outcome(matches) == "unique"
    ]
    total_unique = len(unique_matches)
    mapped = sum(1 for _, cand in unique_matches if cand.mapped_dbo_id is not None)
    return {
        "tier_c_unique_total": total_unique,
        "tier_c_unique_with_mapped_dbo_id": mapped,
        "pct_actionable": _pct(mapped, total_unique),
    }


def _ambiguous_samples(
    tier_c_stats: dict[str, Any],
    limit: int = 25,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for rc_line, matches in tier_c_stats["per_line"]:
        if match_outcome(matches) != "ambiguous":
            continue
        samples.append(
            {
                "rc_id": rc_line.rc_id,
                "match_key": {
                    "customer_ref_value": rc_line.customer_ref_value,
                    "amount": str(rc_line.amount),
                    "txn_date": rc_line.txn_date,
                    "description": rc_line.description or "",
                    "item_ref_value": rc_line.item_ref_value,
                },
                "candidates": [
                    {
                        "source_type": c.source_type,
                        "doc_number": c.doc_number,
                        "vendor_or_entity_name": c.vendor_or_entity_name,
                        "mapped": c.mapped_dbo_id is not None,
                        "mapped_dbo_id": c.mapped_dbo_id,
                    }
                    for c in matches
                ],
            }
        )
        if len(samples) >= limit:
            break
    return samples


def _print_linked_txn_breakdown(invoiced: Counter[str], uninvoiced: Counter[str]) -> None:
    print("\nLinkedTxn TxnType breakdown (header + line level):")
    print("  HasBeenInvoiced=false:")
    for txn_type, count in sorted(uninvoiced.items()):
        print(f"    {txn_type}: {count}")
    if not uninvoiced:
        print("    (none)")

    print("  HasBeenInvoiced=true:")
    for txn_type, count in sorted(invoiced.items()):
        print(f"    {txn_type}: {count}")
    if not invoiced:
        print("    (none)")

    found_bill_purchase = any(
        t in SOURCE_TXN_TYPES for t in (*invoiced.keys(), *uninvoiced.keys())
    )
    if found_bill_purchase:
        print("\n⚠️  WARNING: Bill/Purchase LinkedTxn found — this overturns the measured finding!")
    else:
        print("\nNo Bill/Purchase LinkedTxn types observed (consistent with U-242 finding).")


def main() -> int:
    realm_id = QboAuthService().read_all()[0].realm_id
    logger.info("RealmId=%s", realm_id)

    # Step A — live pull + staged count comparison
    with QboInvoiceClient(realm_id=realm_id) as client:
        raw_records = client.query_all_reimburse_charges()

    live_count = len(raw_records)
    with get_connection() as conn:
        staged_count = _read_staged_rc_count(conn, realm_id)
        bill_candidates = _fetch_bill_candidates(conn, realm_id)
        purchase_candidates = _fetch_purchase_candidates(conn, realm_id)
    print(f"Live QBO ReimburseCharge count: {live_count}")
    print(f"Staged qbo.ReimburseCharge count (RealmId={realm_id}): {staged_count}")
    print("(Drift between live and staged is expected — staging lags by up to ~15 min.)")

    # Step B — LinkedTxn re-verification
    invoiced_lt, uninvoiced_lt = collect_linked_txn_type_counts(raw_records)
    _print_linked_txn_breakdown(invoiced_lt, uninvoiced_lt)

    # Step C — parse RC base lines
    all_base_lines: list[RcBaseLine] = []
    total_derivative = 0
    total_skipped_detail = 0

    for raw_rc in raw_records:
        base_lines, derivative_count, skipped_count = parse_rc_base_lines(raw_rc)
        all_base_lines.extend(base_lines)
        total_derivative += derivative_count
        total_skipped_detail += skipped_count
        if skipped_count:
            logger.info(
                "RC %s: skipped %d non-ReimburseLineDetail line(s)",
                raw_rc.get("Id"),
                skipped_count,
            )

    invoiced_base, uninvoiced_base = _split_by_invoiced(all_base_lines)
    print(f"\nBase lines (fingerprint scope): {len(all_base_lines)}")
    print(f"  Derivative (markup) lines excluded: {total_derivative}")
    print(f"  Other DetailType lines skipped: {total_skipped_detail}")
    print(f"  HasBeenInvoiced=true base lines: {len(invoiced_base)}")
    print(f"  HasBeenInvoiced=false base lines: {len(uninvoiced_base)}")

    # Step D — qbo-side candidates (fetched in Step A's connection block, above)
    candidates = bill_candidates + purchase_candidates
    print(f"\nCandidate pool: {len(candidates)} "
          f"(BillLine={len(bill_candidates)}, PurchaseLine={len(purchase_candidates)})")

    candidate_index = build_candidate_index(candidates)

    # Step E/F — tier stats + report
    all_stats = _tier_stats_for_lines(all_base_lines, candidate_index)
    invoiced_stats = _derive_tier_stats(
        all_stats, lambda rc_line: rc_line.has_been_invoiced is True
    )
    uninvoiced_stats = _derive_tier_stats(
        all_stats, lambda rc_line: rc_line.has_been_invoiced is not True
    )
    actionable = _actionable_pct(all_stats["C"])
    ambiguous_samples = _ambiguous_samples(all_stats["C"])

    for label, stats in (
        ("ALL base lines", all_stats),
        ("HasBeenInvoiced=true", invoiced_stats),
        ("HasBeenInvoiced=false", uninvoiced_stats),
    ):
        print(f"\nPer-tier match rates ({label}):")
        for tier in ("A", "B", "C"):
            s = stats[tier]
            print(
                f"  Tier {tier}: unique={s['pct_unique']}% "
                f"ambiguous={s['pct_ambiguous']}% unmatched={s['pct_unmatched']}%"
            )

    print(
        f"\nActionable cut (Tier-C unique with mapped dbo id): "
        f"{actionable['tier_c_unique_with_mapped_dbo_id']}/{actionable['tier_c_unique_total']} "
        f"({actionable['pct_actionable']}%)"
    )

    if ambiguous_samples:
        print(f"\nSample Tier-C ambiguous cases (up to {len(ambiguous_samples)}):")
        for sample in ambiguous_samples:
            print(f"  RC {sample['rc_id']} key={sample['match_key']}")
            for cand in sample["candidates"]:
                mapped_label = "mapped" if cand["mapped"] else "unmapped"
                print(
                    f"    - {cand['source_type']} DocNumber={cand['doc_number']} "
                    f"{cand['vendor_or_entity_name']} ({mapped_label})"
                )

    # JSON report (counts + samples only, no full candidate dumps)
    def _compact_tier_stats(stats: dict[str, Any]) -> dict[str, Any]:
        return {
            tier: {k: v for k, v in stats[tier].items() if k != "per_line"}
            for tier in ("A", "B", "C")
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "realm_id": realm_id,
        "live_qbo_count": live_count,
        "staged_count": staged_count,
        "linked_txn_types": {
            "has_been_invoiced_true": dict(invoiced_lt),
            "has_been_invoiced_false": dict(uninvoiced_lt),
            "bill_purchase_found": any(
                t in SOURCE_TXN_TYPES for t in (*invoiced_lt.keys(), *uninvoiced_lt.keys())
            ),
        },
        "line_counts": {
            "base_lines": len(all_base_lines),
            "derivative_excluded": total_derivative,
            "other_detail_type_skipped": total_skipped_detail,
            "invoiced_base_lines": len(invoiced_base),
            "uninvoiced_base_lines": len(uninvoiced_base),
        },
        "candidate_pool": {
            "total": len(candidates),
            "bill_lines": len(bill_candidates),
            "purchase_lines": len(purchase_candidates),
        },
        "tier_stats_all": _compact_tier_stats(all_stats),
        "tier_stats_invoiced": _compact_tier_stats(invoiced_stats),
        "tier_stats_uninvoiced": _compact_tier_stats(uninvoiced_stats),
        "actionable": actionable,
        "ambiguous_samples": ambiguous_samples,
    }

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = _REPORTS_DIR / f"rc_source_fingerprint_{ts}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote JSON report: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
