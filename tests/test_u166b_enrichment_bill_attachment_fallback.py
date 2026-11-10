"""U-166b — BillLineItem attachment fallback in enrich_line_items.

Older bills often have their PDF linked to ONE of their lines only. The invoice
PDF packet keys off `attachment_public_id` per line, so the un-linked sibling
lines dropped out of the packet. The fix falls back to the bill's attachment,
but ONLY when the bill's lines carry exactly one distinct AttachmentId (two or
more PDFs on one bill is ambiguous and must stay null).

The rule lives in SQL, so these tests pin (a) the query text carries the
one-distinct-PDF guard and the own-link-first COALESCE, and (b) the row -> dict
mapping passes the resolved id through. No database is touched: get_connection
is replaced with a fake cursor.
"""

import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.invoice.business import enrichment


def _bill_line(line_id=1, bill_line_item_id=10):
    return SimpleNamespace(
        id=line_id,
        source_type="BillLineItem",
        bill_line_item_id=bill_line_item_id,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
        employee_labor_line_item_id=None,
        invoice_id=None,
        to_dict=lambda: {},
    )


def _bill_row(attachment_public_id, line_id=10):
    return SimpleNamespace(
        Id=line_id,
        SourceLinePublicId="11111111-1111-1111-1111-111111111111",
        ParentNumber="B-1",
        ParentPublicId="22222222-2222-2222-2222-222222222222",
        SourceDate=datetime(2024, 3, 5),
        VendorName="Acme",
        SccNumber="01",
        SccName="Framing",
        CcNumber="010",
        CcName="Site",
        BilledPrice=100,
        AttachmentPublicId=attachment_public_id,
    )


def _run_with_rows(rows):
    """Run enrich_line_items against a fake cursor; return (sql, params, results)."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    gc = MagicMock()
    gc.return_value.__enter__.return_value = conn
    with patch.object(enrichment, "get_connection", gc):
        results = enrichment.enrich_line_items([_bill_line()])
    sql = cursor.execute.call_args[0][0]
    params = cursor.execute.call_args[0][1]
    return sql, params, results


def _bill_sql():
    sql, _, _ = _run_with_rows([_bill_row(None)])
    return sql


def test_bill_query_has_one_distinct_pdf_guard():
    sql = _bill_sql()
    # Fallback only when the bill's links carry exactly ONE distinct AttachmentId.
    assert re.search(
        r"HAVING\s+COUNT\(\s*DISTINCT\s+\w+\.AttachmentId\s*\)\s*=\s*1", sql
    ), "bill query must guard the bill-level fallback with COUNT(DISTINCT AttachmentId) = 1"


def test_bill_query_fallback_is_scoped_to_the_line_bill():
    sql = _bill_sql()
    assert re.search(r"WHERE\s+\w+\.BillId\s*=\s*b\.Id", sql), (
        "bill-level fallback must aggregate over the line's own bill (b.Id), not all bills"
    )


def test_bill_query_coalesce_prefers_own_link():
    sql = _bill_sql()
    assert re.search(
        r"COALESCE\(\s*att_first\.PublicId\s*,\s*att_bill\.PublicId\s*\)", sql
    ), "own link (att_first) must win over the bill-level fallback (att_bill) via COALESCE"


def test_bill_query_stays_parameterised():
    sql, params, _ = _run_with_rows([_bill_row(None)])
    assert "?" in sql
    assert params == [10]
    assert "10" not in sql.replace("?", "")  # no string-formatted id in the SQL text


def test_bill_fallback_id_is_mapped_into_attachment_public_id():
    _, _, results = _run_with_rows([_bill_row("FALLBACK-PDF-PID")])
    assert results[0]["attachment_public_id"] == "FALLBACK-PDF-PID"


def test_bill_ambiguous_or_unlinked_bill_maps_to_empty():
    # SQL returns NULL for a bill with 0 or 2+ distinct PDFs; that must stay "".
    _, _, results = _run_with_rows([_bill_row(None)])
    assert results[0]["attachment_public_id"] == ""
