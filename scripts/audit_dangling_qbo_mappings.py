"""
Read-only audit: count qbo.* mapping rows whose target dbo.* row no longer exists.

Reports per-topology dangling counts plus a total. Never writes — SELECT only.

Usage:
  PYTHONPATH=. python scripts/audit_dangling_qbo_mappings.py
"""
from __future__ import annotations

import logging

from integrations.intuit.qbo.base.identity_drift import (
    HEADER_ENTITY_SPECS,
    LINE_ENTITY_SPECS,
    REFERENCE_ENTITY_SPECS,
)
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("audit_dangling_qbo_mappings")

# Keys covered by this audit, in stable output order. "invoice" is included for visibility
# only — InvoiceService.delete_by_public_id has the identical uncleared-mapping gap this unit
# fixes for BillCredit/Bill/Expense, but Invoice was out of scope for the U-226 fix itself; see
# TODO/BOARD for the deferred follow-up.
#
# U-353: "bill_credit" was removed from this tuple — qbo.VendorCreditBillCredit (its
# mapping_table) is retired, so a dangling-mapping count against it is meaningless
# (there is no more mapping table to strand a dangling row in) and the query would
# raise "invalid object name" if left in. The bill_credit FlatEntitySpec row itself
# stays in REFERENCE_ENTITY_SPECS for an unrelated reconciliation consumer — see
# identity_drift.py's comment there.
_AUDIT_SPEC_KEYS: tuple[str, ...] = (
    "bill",
    "expense",
    "invoice",
    "bill_line_item",
    "invoice_line_item",
    "bill_credit_line_item",
    "expense_line_item",
)


def _build_topologies() -> list[tuple[str, str, str, str, str]]:
    """(topology label, mapping table, mapping FK column, dbo table, dbo PK column)."""
    spec_by_key = {
        spec.key: spec
        for spec in (*HEADER_ENTITY_SPECS, *REFERENCE_ENTITY_SPECS, *LINE_ENTITY_SPECS)
    }
    topologies: list[tuple[str, str, str, str, str]] = []
    for key in _AUDIT_SPEC_KEYS:
        spec = spec_by_key[key]
        topologies.append(
            (
                f"{spec.mapping_table} -> {spec.label}",
                spec.mapping_table,
                spec.dbo_fk_col,
                spec.label,
                "Id",
            )
        )
    return topologies


# (topology label, mapping table, mapping FK column, dbo table, dbo PK column)
TOPOLOGIES: list[tuple[str, str, str, str, str]] = _build_topologies()


def _dangling_count_sql(mapping_table: str, mapping_fk_col: str, dbo_table: str, dbo_pk_col: str) -> str:
    return f"""
    SELECT COUNT(*)
    FROM qbo.[{mapping_table}] m
    WHERE NOT EXISTS (
        SELECT 1
        FROM dbo.[{dbo_table}] t
        WHERE t.[{dbo_pk_col}] = m.[{mapping_fk_col}]
    )
    """


def main() -> int:
    total = 0
    print("Dangling qbo.* mapping rows (mapping exists, dbo target missing):")
    print("-" * 60)

    with get_connection() as conn:
        cur = conn.cursor()
        for label, mapping_table, mapping_fk_col, dbo_table, dbo_pk_col in TOPOLOGIES:
            cur.execute(_dangling_count_sql(mapping_table, mapping_fk_col, dbo_table, dbo_pk_col))
            row = cur.fetchone()
            count = int(row[0]) if row else 0
            total += count
            print(f"  {label}: {count}")

    print("-" * 60)
    print(f"  TOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
