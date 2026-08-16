"""Shared fan-out overlap checks for QBO Customer/Item identity (U-238c)."""

from __future__ import annotations

import logging

from shared.database import get_connection

logger = logging.getLogger(__name__)

CUSTOMER_FANOUT_OVERLAP_SQL = """
SELECT c.[QboId], c.[RealmId]
FROM dbo.[Customer] c
INNER JOIN dbo.[Project] p
    ON p.[QboId] = c.[QboId]
   AND ((p.[RealmId] = c.[RealmId]) OR (p.[RealmId] IS NULL AND c.[RealmId] IS NULL))
WHERE c.[QboId] IS NOT NULL
"""

ITEM_FANOUT_OVERLAP_SQL = """
SELECT cc.[QboId], cc.[RealmId]
FROM dbo.[CostCode] cc
INNER JOIN dbo.[SubCostCode] scc
    ON scc.[QboId] = cc.[QboId]
   AND ((scc.[RealmId] = cc.[RealmId]) OR (scc.[RealmId] IS NULL AND cc.[RealmId] IS NULL))
WHERE cc.[QboId] IS NOT NULL
"""


def check_customer_fanout_overlap(cursor) -> tuple[bool, list[tuple[str, str]]]:
    cursor.execute(CUSTOMER_FANOUT_OVERLAP_SQL)
    rows = [(row.QboId, row.RealmId) for row in cursor.fetchall()]
    if rows:
        for qbo_id, realm_id in rows[:5]:
            logger.error(
                "Customer/Project fan-out overlap: QboId=%s RealmId=%s stamped in BOTH tables",
                qbo_id,
                realm_id,
            )
        if len(rows) > 5:
            logger.error("Customer/Project fan-out overlap: ... and %s more pair(s)", len(rows) - 5)
    return (len(rows) == 0, rows)


def check_item_fanout_overlap(cursor) -> tuple[bool, list[tuple[str, str]]]:
    cursor.execute(ITEM_FANOUT_OVERLAP_SQL)
    rows = [(row.QboId, row.RealmId) for row in cursor.fetchall()]
    if rows:
        for qbo_id, realm_id in rows[:5]:
            logger.error(
                "CostCode/SubCostCode fan-out overlap: QboId=%s RealmId=%s stamped in BOTH tables",
                qbo_id,
                realm_id,
            )
        if len(rows) > 5:
            logger.error(
                "CostCode/SubCostCode fan-out overlap: ... and %s more pair(s)", len(rows) - 5
            )
    return (len(rows) == 0, rows)


def check_all_fanout_overlaps(*, use_connection: bool = True) -> dict[str, bool]:
    """Run both fan-out overlap checks. Returns pass/fail per pair."""
    if not use_connection:
        return {"customer_project": True, "cost_code_sub_cost_code": True}

    with get_connection() as conn:
        cur = conn.cursor()
        customer_ok, _ = check_customer_fanout_overlap(cur)
        item_ok, _ = check_item_fanout_overlap(cur)
    return {
        "customer_project": customer_ok,
        "cost_code_sub_cost_code": item_ok,
    }
