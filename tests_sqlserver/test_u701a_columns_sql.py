"""U-701a: referenced columns exist on prod-shaped tables (snapshot in fixtures/)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    column_exists,
    u701a_real_schema_conn,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "u701a_prod_columns.txt"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Columns touched by U-701a SQL + backfill candidate query (table -> columns).
REFERENCED: dict[str, frozenset[str]] = {
    "Attachment": frozenset(
        {
            "Id",
            "ExtractionStatus",
            "ExtractedTextBlobUrl",
        }
    ),
    "TaxpayerAttachment": frozenset({"AttachmentId"}),
    "Bill": frozenset({"Id", "VendorId", "CreatedDatetime", "IsDraft"}),
    "BillLineItem": frozenset({"BillId", "SubCostCodeId", "ProjectId", "Amount", "Id"}),
    "BillLineItemAttachment": frozenset({"BillLineItemId", "AttachmentId", "Id"}),
    "AttachmentTextTokenHash": frozenset(
        {"AttachmentId", "TokenizerVersion", "TokenHashes", "TokenCount", "ModifiedDatetime"}
    ),
    "BillIntakePrediction": frozenset(
        {"AttachmentId", "SimilarPredSubCostCodeId", "SimilarConfidence"}
    ),
    "SubCostCode": frozenset({"Id"}),
}


def _load_prod_column_map() -> dict[str, set[str]]:
    text = FIXTURE.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\w+)\s+\d+\s+(.+)$", line)
        if not m:
            continue
        table, rest = m.group(1), m.group(2)
        cols = {c.strip() for c in rest.split(",") if c.strip()}
        out[table] = cols
    return out


# Created by this unit's SQL, so absent from the pre-apply prod snapshot. Everything else must already exist in prod.
UNIT_CREATED: dict[str, frozenset[str]] = {
    "AttachmentTextTokenHash": REFERENCED["AttachmentTextTokenHash"],
    "BillIntakePrediction": frozenset({"SimilarPredSubCostCodeId", "SimilarConfidence"}),
}


def test_prod_snapshot_lists_referenced_columns():
    prod = _load_prod_column_map()
    missing: list[str] = []
    for table, cols in REFERENCED.items():
        prod_cols = prod.get(table, set()) | UNIT_CREATED.get(table, frozenset())
        if not prod_cols:
            missing.append(f"{table}: table missing from snapshot")
            continue
        for col in cols:
            if col not in prod_cols:
                missing.append(f"{table}.{col}")
    assert not missing, "prod snapshot missing: " + ", ".join(missing)


def test_unit_created_columns_are_new_to_prod():
    """The snapshot is the PRE-apply prod schema; a unit-created column listed there means it was edited."""
    prod = _load_prod_column_map()
    clash = [f"{t}.{c}" for t, cols in UNIT_CREATED.items() for c in cols if c in prod.get(t, set())]
    assert not clash, "snapshot already holds unit-created columns: " + ", ".join(clash)


def test_referenced_columns_exist_on_real_schema(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    missing: list[str] = []
    for table, cols in REFERENCED.items():
        dbo_table = f"dbo.{table}"
        for col in cols:
            if not column_exists(cur, dbo_table, col):
                missing.append(f"{dbo_table}.{col}")
    assert not missing, "real schema missing: " + ", ".join(missing)
