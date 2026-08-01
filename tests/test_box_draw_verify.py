"""U-184 — Box draw value-read (KI-46) + draw_requests folder guard (HP2-11) pure logic."""

import io
from decimal import Decimal

import pytest
from openpyxl import Workbook

from entities.invoice.business.box_verify import (
    _worksheet_rows_from_bytes,
    compare_box_draw,
    is_draw_requests_folder,
    sum_box_draw_column,
)

_COL_H = 7
_COL_N = 13


def _blank_row(width: int = 20) -> list:
    return [None] * width


def _row(*, draw=..., amount=...) -> list:
    r = _blank_row()
    if draw is not ...:
        r[_COL_H] = draw
    if amount is not ...:
        r[_COL_N] = amount
    return r


def test_sum_box_draw_column_string_h_matches_and_sums_n():
    rows = [
        _row(draw="22", amount="100.50"),
        _row(draw="22", amount=200),
        _row(draw="23", amount="999"),
        _row(draw="22", amount=None),
    ]
    out = sum_box_draw_column(rows, "22")
    assert out["tagged_row_count"] == 3
    assert out["draw_total"] == Decimal("300.50")


def test_sum_box_draw_column_numeric_h_coercion():
    rows = [
        _row(draw=22.0, amount="10"),
        _row(draw="022", amount="5"),
    ]
    out = sum_box_draw_column(rows, "22")
    assert out["tagged_row_count"] == 2
    assert out["draw_total"] == Decimal("15")


def test_sum_box_draw_column_ignores_other_draws():
    rows = [
        _row(draw="21", amount="1000"),
        _row(draw="22", amount="1"),
    ]
    out = sum_box_draw_column(rows, "22")
    assert out["draw_total"] == Decimal("1")
    assert out["tagged_row_count"] == 1


def test_compare_box_draw_match_within_tolerance():
    out = compare_box_draw(Decimal("100.00"), Decimal("100.005"), tolerance=Decimal("0.01"))
    assert out["match"] is True
    assert out["difference"] == "-0.005"


def test_compare_box_draw_no_match_outside_tolerance():
    out = compare_box_draw(Decimal("100.00"), Decimal("100.02"), tolerance=Decimal("0.01"))
    assert out["match"] is False
    assert out["box_total"] == "100.00"
    assert out["expected_total"] == "100.02"
    assert out["difference"] == "-0.02"


def test_compare_box_draw_exact_decimal():
    out = compare_box_draw(Decimal("84.45"), Decimal("84.45"))
    assert out["match"] is True
    assert out["difference"] == "0.00"


def test_is_draw_requests_folder_draw_like_names():
    assert is_draw_requests_folder("15 - Draw Requests") is True
    assert is_draw_requests_folder("draw request folder") is True


def test_is_draw_requests_folder_rejects_invoice_subfolder_names():
    assert is_draw_requests_folder("HP2-11") is False
    assert is_draw_requests_folder("22") is False
    assert is_draw_requests_folder("") is False
    assert is_draw_requests_folder(None) is False


def test_worksheet_rows_from_bytes_raises_when_worksheet_missing():
    wb = Workbook()
    wb.active.title = "DETAILS"
    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()

    with pytest.raises(ValueError, match="worksheet_not_found:Tracking Budget DETAILS"):
        _worksheet_rows_from_bytes(file_bytes, "Tracking Budget DETAILS")


def test_sum_box_draw_column_unaffected_by_missing_worksheet_guard():
    """Pure sum path unchanged — guard applies only at workbook load."""
    rows = [_row(draw="22", amount="50")]
    assert sum_box_draw_column(rows, "22")["draw_total"] == Decimal("50")
