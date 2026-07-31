"""U-180 — Budget Tracker worksheet reconcile pure matching."""

from decimal import Decimal
import uuid

from fastapi import HTTPException
import pytest

from entities.invoice.business.worksheet_reconcile import (
    detect_header_and_columns,
    match_worksheet_rows,
)

# Excel columns: H=7 draw, I=8 date, K=10 invoice #, M=12 source, N=13 billable, Z=25 public_id
_COL_H = 7
_COL_I = 8
_COL_K = 10
_COL_M = 12
_COL_N = 13
_COL_Z = 25


def _blank_row(width: int = 30) -> list:
    return [""] * width


def _header_row() -> list:
    row = _blank_row()
    row[_COL_H] = "DRAW REQUEST DATE"
    row[_COL_I] = "DATE"
    row[_COL_K] = "INVOICE #"
    row[11] = "DESCRIPTION"
    row[_COL_M] = "SOURCE"
    row[_COL_N] = "BILLABLE"
    row[14] = "SUB COST CODE"
    row[9] = "PAYABLE TO"
    return row


def _data_row(
    *,
    date: str = "2026-07-01",
    draw_request: str = "",
    invoice_num: str = "",
    description: str = "Materials",
    source: str = "Bill",
    billable=100.0,
    z_public_id: str = "",
) -> list:
    row = _blank_row()
    row[_COL_I] = date
    row[_COL_H] = draw_request
    row[_COL_K] = invoice_num
    row[11] = description
    row[_COL_M] = source
    row[_COL_N] = billable
    if z_public_id:
        row[_COL_Z] = z_public_id
    return row


def _sheet(*data_rows, title_row: str | None = None) -> list[list]:
    rows = []
    if title_row is not None:
        title = _blank_row()
        title[0] = title_row
        rows.append(title)
    rows.append(_header_row())
    rows.extend(data_rows)
    return rows


def _enriched_bill(spid: str, *, parent_number: str = "", amount="100.00", description: str = ""):
    amt = float(Decimal(str(amount)))
    return {
        "source_line_public_id": spid,
        "source_type": "BillLineItem",
        "parent_number": parent_number,
        "description": description,
        "price": amt,
        "amount": amt,
    }


def _match_ws(ws, invoice_number, enriched_line_items):
    header_row_idx, col_map, data_rows = detect_header_and_columns(ws)
    return match_worksheet_rows(
        header_row_idx,
        col_map,
        data_rows,
        invoice_number,
        enriched_line_items,
    )


def test_detect_header_skips_title_row():
    ws = _sheet(_data_row(), title_row="Budget Tracker — HP2")
    idx, col_map, data_rows = detect_header_and_columns(ws)
    assert idx == 1
    assert col_map["date"] == _COL_I
    assert col_map["draw_request_date"] == _COL_H
    assert len(data_rows) == 1


def test_worksheet_no_data_rows_raises():
    with pytest.raises(HTTPException) as exc:
        detect_header_and_columns([["only one row"]])
    assert exc.value.status_code == 400
    assert exc.value.detail == "Worksheet has no data rows"


def test_worksheet_no_header_row_raises():
    ws = [
        ["foo", "bar"],
        ["a", "b", "c"],
    ]
    with pytest.raises(HTTPException) as exc:
        detect_header_and_columns(ws)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Could not locate header row in worksheet"


def test_worksheet_missing_required_columns_raises():
    row = _blank_row()
    row[0] = "DATE"
    row[1] = "DESCRIPTION"
    row[2] = "PAYABLE TO"
    row[3] = "INVOICE #"
    ws = [row, _blank_row()]
    with pytest.raises(HTTPException) as exc:
        detect_header_and_columns(ws)
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Worksheet missing columns:")
    assert "draw_request_date" in exc.value.detail
    assert "billable" in exc.value.detail


def test_direction_a_db_only_when_source_public_id_missing_from_col_z():
    spid = str(uuid.uuid4())
    invoice_number = "DR-100"
    ws = _sheet(
        _data_row(date="2026-07-01", z_public_id=str(uuid.uuid4())),
    )
    enriched = [_enriched_bill(spid, parent_number="INV-NOT-ON-SHEET", amount="250.00")]
    result = _match_ws(ws, invoice_number, enriched)
    assert len(result["db_only"]) == 1
    assert result["db_only"][0]["db_total"] == 250.0
    assert result["db_only"][0]["ws_total"] == 0.0
    assert result["matched"] == []


def test_direction_b_already_tagged_when_col_z_not_on_invoice():
    invoice_number = "DR-200"
    extra_z = str(uuid.uuid4())
    ws = _sheet(
        _data_row(
            draw_request=invoice_number,
            billable=75.0,
            z_public_id=extra_z,
        ),
    )
    result = _match_ws(ws, invoice_number, [])
    assert len(result["already_tagged"]) == 1
    assert result["already_tagged"][0]["ws_total"] == 75.0
    assert result["tagged_ok_count"] == 0


def test_tagged_row_amount_drift_surfaces_in_mismatched():
    spid = str(uuid.uuid4())
    invoice_number = "DR-300"
    db_amt = float(Decimal("100.00"))
    ws_amt = float(Decimal("85.50"))
    ws = _sheet(
        _data_row(
            draw_request=invoice_number,
            billable=ws_amt,
            z_public_id=spid,
        ),
    )
    enriched = [_enriched_bill(spid, amount="100.00")]
    result = _match_ws(ws, invoice_number, enriched)
    assert result["tagged_ok_count"] == 1
    assert len(result["mismatched"]) == 1
    entry = result["mismatched"][0]
    assert entry.get("tagged") is True
    assert entry["db_total"] == db_amt
    assert entry["ws_total"] == ws_amt
    assert entry["difference"] == round(db_amt - ws_amt, 2)


def test_tier0_match_when_col_z_aligns():
    spid = str(uuid.uuid4())
    amt = float(Decimal("123.45"))
    ws = _sheet(
        _data_row(z_public_id=spid, billable=amt),
    )
    enriched = [_enriched_bill(spid, amount="123.45")]
    result = _match_ws(ws, "DR-0", enriched)
    assert len(result["matched"]) == 1
    assert result["matched"][0]["match_key"] == "public_id"
    assert result["z_matched_count"] == 1
