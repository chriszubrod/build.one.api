"""
Backfill dbo.InvoiceLineItemSourceProvenance from qbo.InvoiceLineItemInvoiceLine
-> qbo.InvoiceLine for existing mapped invoice lines (U-272).

SAFE BY DEFAULT: dry-run unless --apply is passed. Dry-run is READ-ONLY
(SELECTs only) and reports pre/post-flight counts. --apply upserts via
UpsertInvoiceLineItemSourceProvenance in batched loops — never writes to
qbo.* tables.

Usage:
  PYTHONPATH=. python scripts/backfill_invoice_line_source_provenance.py
  PYTHONPATH=. python scripts/backfill_invoice_line_source_provenance.py --apply --limit 100
"""
from __future__ import annotations

import argparse
import logging

from scripts.sync_helper import assert_cli_system_admin
from shared.database import call_procedure, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_invoice_line_source_provenance")


_COUNT_SQL = """
SELECT
    (SELECT COUNT(*) FROM qbo.[InvoiceLineItemInvoiceLine]) AS mapping_count,
    (SELECT COUNT(*)
     FROM dbo.[InvoiceLineItem] ili
     INNER JOIN qbo.[InvoiceLineItemInvoiceLine] ilil ON ilil.[InvoiceLineItemId] = ili.[Id]) AS eligible,
    (SELECT COUNT(*) FROM dbo.[InvoiceLineItemSourceProvenance]) AS stamped,
    (SELECT COUNT(*)
     FROM qbo.[InvoiceLineItemInvoiceLine] ilil
     WHERE NOT EXISTS (
         SELECT 1 FROM qbo.[InvoiceLine] s WHERE s.[Id] = ilil.[QboInvoiceLineId]
     )) AS dangling
"""

_BATCH_SELECT_SQL = """
SELECT TOP ({limit})
    ili.[Id] AS InvoiceLineItemId,
    s.[LineNum],
    s.[Amount] AS QboAmount,
    s.[Description] AS QboDescription,
    s.[ServiceDate],
    s.[LinkedTxnType],
    s.[LinkedTxnId],
    s.[ItemRefValue]
FROM dbo.[InvoiceLineItem] ili
INNER JOIN qbo.[InvoiceLineItemInvoiceLine] ilil ON ilil.[InvoiceLineItemId] = ili.[Id]
INNER JOIN qbo.[InvoiceLine] s ON s.[Id] = ilil.[QboInvoiceLineId]
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.[InvoiceLineItemSourceProvenance] prov WHERE prov.[InvoiceLineItemId] = ili.[Id]
)
ORDER BY ili.[Id]
"""

_MISMATCH_SQL = """
SELECT
    ili.[Id],
    prov.[QboAmount] AS dbo_amount,
    s.[Amount] AS staging_amount,
    prov.[ServiceDate] AS dbo_service_date,
    s.[ServiceDate] AS staging_service_date
FROM dbo.[InvoiceLineItem] ili
INNER JOIN qbo.[InvoiceLineItemInvoiceLine] ilil ON ilil.[InvoiceLineItemId] = ili.[Id]
INNER JOIN qbo.[InvoiceLine] s ON s.[Id] = ilil.[QboInvoiceLineId]
INNER JOIN dbo.[InvoiceLineItemSourceProvenance] prov ON prov.[InvoiceLineItemId] = ili.[Id]
WHERE ISNULL(CAST(prov.[QboAmount] AS VARCHAR(50)), '') <> ISNULL(CAST(s.[Amount] AS VARCHAR(50)), '')
   OR ISNULL(prov.[ServiceDate], '') <> ISNULL(s.[ServiceDate], '')
"""


def _fetch_counts(cursor) -> dict:
    cursor.execute(_COUNT_SQL)
    row = cursor.fetchone()
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _print_counts(counts: dict, *, prefix: str) -> None:
    print(
        f"{prefix} InvoiceLineItemSourceProvenance: "
        f"mapping={counts['mapping_count']} eligible={counts['eligible']} "
        f"stamped={counts['stamped']} dangling_mappings={counts['dangling']}"
    )


def _stamp_via_sproc(cursor, row) -> None:
    call_procedure(
        cursor,
        "UpsertInvoiceLineItemSourceProvenance",
        {
            "InvoiceLineItemId": row.InvoiceLineItemId,
            "LineNum": row.LineNum,
            "QboAmount": row.QboAmount,
            "QboDescription": row.QboDescription,
            "ServiceDate": row.ServiceDate,
            "LinkedTxnType": row.LinkedTxnType,
            "LinkedTxnId": row.LinkedTxnId,
            "ItemRefValue": row.ItemRefValue,
        },
    )


def _verify(cursor) -> bool:
    """Row-level value match between the new mirror and its qbo.InvoiceLine source."""
    cursor.execute(_MISMATCH_SQL)
    mismatches = cursor.fetchall()
    ok = not mismatches
    if mismatches:
        for row in mismatches[:10]:
            logger.error(
                "InvoiceLineItemSourceProvenance mismatch Id=%s dbo_amount=%s staging_amount=%s "
                "dbo_service_date=%s staging_service_date=%s",
                row.Id, row.dbo_amount, row.staging_amount, row.dbo_service_date, row.staging_service_date,
            )
        if len(mismatches) > 10:
            logger.error("... and %s more mismatch(es)", len(mismatches) - 10)
    print(f"  VERIFY: value_match={'PASS' if ok else 'FAIL'}")
    return ok


def backfill(*, apply: bool, batch_size: int, limit: int | None) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        pre = _fetch_counts(cur)
        _print_counts(pre, prefix="PRE ")

        pending = max(0, pre["eligible"] - pre["stamped"])
        would_apply = pending if limit is None else min(pending, limit)
        print(f"  -> {'WOULD stamp' if not apply else 'Stamping'} up to {would_apply} row(s)")

        applied = 0
        if apply and would_apply > 0:
            while applied < would_apply:
                fetch_n = min(batch_size, would_apply - applied)
                cur.execute(_BATCH_SELECT_SQL.format(limit=fetch_n))
                rows = cur.fetchall()
                if not rows:
                    break
                for row in rows:
                    _stamp_via_sproc(cur, row)
                    applied += 1
                conn.commit()
                logger.info("Stamped batch of %s (total %s)", len(rows), applied)

        post = _fetch_counts(cur)
        _print_counts(post, prefix="POST")

        if apply:
            _verify(cur)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill dbo.InvoiceLineItemSourceProvenance (dry-run by default)."
    )
    ap.add_argument("--apply", action="store_true", help="Write via UpsertInvoiceLineItemSourceProvenance.")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=None, help="Max rows to stamp (apply mode).")
    args = ap.parse_args()

    assert_cli_system_admin()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: InvoiceLineItemSourceProvenance backfill ===")
    backfill(apply=args.apply, batch_size=args.batch_size, limit=args.limit)


if __name__ == "__main__":
    main()
