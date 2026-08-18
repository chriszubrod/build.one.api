"""
Read-only feasibility report: BillCredits with no linked Attachment rows.

Part A (always): SELECT-only DB scan — BillCredits mapped from qbo.VendorCredit
with zero dbo.BillCreditLineItemAttachment links.

Part B (--check-qbo): one whole-realm QBO Attachable list/query call
(`QboAttachableClient.query_all_attachables`) filtered in-memory to Part-A
VendorCredit QboIds. Never touches Attachment/BillCredit/VendorCredit business
data or downloads attachment bytes; the QBO call does go through the shared
client's standard API-usage metering (qbo.ApiUsage) and may trigger a token
refresh (qbo.Auth) like any other QBO read.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/analyze_billcredit_attachment_backfill.py
  PYTHONPATH=. ./.venv/bin/python scripts/analyze_billcredit_attachment_backfill.py --check-qbo
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("analyze_billcredit_attachment_backfill")

_BASELINE_COUNT = 443
_REPORTS_DIR = Path(__file__).resolve().parent / "_reports"

_BILLCREDITS_WITHOUT_ATTACHMENTS_SQL = """
SELECT
    bc.Id AS BillCreditId,
    bc.PublicId AS BillCreditPublicId,
    bc.CreditNumber,
    qvc.Id AS QboVendorCreditLocalId,
    qvc.QboId AS QboVendorCreditQboId
FROM dbo.BillCredit bc
JOIN qbo.VendorCreditBillCredit vcbc ON vcbc.BillCreditId = bc.Id
JOIN qbo.VendorCredit qvc ON qvc.Id = vcbc.QboVendorCreditId
WHERE NOT EXISTS (
    SELECT 1
    FROM dbo.BillCreditLineItem bcli
    JOIN dbo.BillCreditLineItemAttachment bclia
      ON bclia.BillCreditLineItemId = bcli.Id
    WHERE bcli.BillCreditId = bc.Id
)
ORDER BY bc.Id
"""


def _fetch_billcredits_without_attachments(conn) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(_BILLCREDITS_WITHOUT_ATTACHMENTS_SQL)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _ref_matches_vendor_credit(ref, qbo_ids: set[str]) -> str | None:
    """Pure predicate called directly by the hot loop below (not a separately-tested
    shadow of the real check) -- returns the matched qbo_id if this ref points at one
    of qbo_ids's VendorCredits, else None."""
    if (ref.entity_ref_type or "").upper() != "VENDORCREDIT":
        return None
    ref_value = ref.entity_ref_value
    return ref_value if ref_value in qbo_ids else None


def _check_qbo_attachables(realm_id: str, qbo_ids: set[str]) -> dict[str, list[str]]:
    """One whole-realm list call; single pass over each attachable's own refs (no
    per-target-id inner loop). Returns qbo_id -> attachable QBO Ids."""
    with QboAttachableClient(realm_id=realm_id) as client:
        all_attachables = client.query_all_attachables()

    by_vendor_credit: dict[str, list[str]] = {qbo_id: [] for qbo_id in qbo_ids}
    for att in all_attachables:
        if not att.id:
            continue
        for ref in att.attachable_ref or []:
            matched = _ref_matches_vendor_credit(ref, qbo_ids)
            if matched is not None:
                by_vendor_credit[matched].append(att.id)
    return by_vendor_credit


def _print_section_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def main(*, check_qbo: bool = False) -> int:
    with get_connection() as conn:
        missing = _fetch_billcredits_without_attachments(conn)

    live_count = len(missing)
    delta = live_count - _BASELINE_COUNT
    qbo_ids = {str(row["QboVendorCreditQboId"]) for row in missing if row.get("QboVendorCreditQboId")}

    _print_section_header("PART A — BillCredits with zero linked Attachment rows (DB-only)")
    print(f"Live count: {live_count}")
    print(f"U-219 follow-up (e) baseline (~{_BASELINE_COUNT}): delta={delta:+d}")
    print()
    if not missing:
        print("No BillCredits without attachments found.")
    else:
        print(f"{'BillCreditPublicId':<38} {'CreditNumber':<20} {'QboVendorCreditQboId'}")
        print("-" * 90)
        for row in missing:
            print(
                f"{str(row['BillCreditPublicId']):<38} "
                f"{str(row.get('CreditNumber') or ''):<20} "
                f"{row.get('QboVendorCreditQboId')}"
            )

    qbo_summary: dict[str, Any] | None = None
    if check_qbo:
        _print_section_header("PART B — QBO attachable presence (--check-qbo)")
        auth_service = QboAuthService()
        realm_id = auth_service.resolve_realm_id()
        logger.info("Resolved realm_id=%s; querying all attachables once", realm_id)

        by_vendor_credit = _check_qbo_attachables(realm_id, qbo_ids)
        with_attachable = [qbo_id for qbo_id, ids in by_vendor_credit.items() if ids]
        without_attachable = [qbo_id for qbo_id, ids in by_vendor_credit.items() if not ids]

        qbo_summary = {
            "realm_id": realm_id,
            "part_a_count": live_count,
            "with_attachable_in_qbo": len(with_attachable),
            "without_attachable_in_qbo": len(without_attachable),
            "with_attachable_qbo_ids": sorted(with_attachable),
            "without_attachable_qbo_ids": sorted(without_attachable),
            "attachable_ids_by_vendor_credit": {
                qbo_id: sorted(set(ids)) for qbo_id, ids in sorted(by_vendor_credit.items()) if ids
            },
        }

        print(f"Part-A BillCredits checked:           {live_count}")
        print(f"With >=1 VendorCredit attachable in QBO: {len(with_attachable)}")
        print(f"With zero VendorCredit attachables:    {len(without_attachable)}")
        if with_attachable:
            print("\nRetrievable today (QBO VendorCredit QboId):")
            for qbo_id in sorted(with_attachable):
                att_ids = sorted(set(by_vendor_credit[qbo_id]))
                print(f"  {qbo_id} -> attachable(s): {', '.join(att_ids)}")
    else:
        print()
        print("Part B skipped (pass --check-qbo to run one whole-realm QBO attachable list).")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_count_u219_e": _BASELINE_COUNT,
        "part_a": {
            "live_count": live_count,
            "delta_from_baseline": delta,
            "bill_credits": missing,
        },
        "part_b": qbo_summary,
    }
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = "with_qbo" if check_qbo else "db_only"
    report_path = _REPORTS_DIR / f"billcredit_attachment_backfill_{suffix}_{ts}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote JSON snapshot: {report_path}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only BillCredit attachment backfill feasibility report.",
    )
    parser.add_argument(
        "--check-qbo",
        action="store_true",
        help=(
            "Run Part B: one whole-realm QBO Attachable list/query call and report "
            "which Part-A VendorCredits still have attachables in QBO today."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(check_qbo=args.check_qbo))
