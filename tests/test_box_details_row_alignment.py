"""A DETAILS row must land on its contracted columns, or not be written at all.

The Box DETAILS writer's column arithmetic (`column=col_index + 1`) is correct
and always has been. What was missing is that the 26-cell positional contract
lived only in a docstring, so a caller that handed in a row with one EXTRA
element ahead of column I shifted every field one column right:

    amount -> O (AMOUNT NOT BILLABLE)  ... so it silently leaves the draw
    type label 'Bill' -> N (AMOUNT BILLABLE)  ... so col N is TEXT and every
                                                  SUMIFS over it skips the row
    col-Z key -> AA  ... so the row is un-rekeyable and the next drain of the
                        same bill inserts a SECOND copy

Observed live on three of 28 mapped workbooks, one row each: WVA r1824
(2026-08-27), SHT r2294 (2026-09-23), EVR r1383 (2026-09-28). EVR's cost the
EVR-20 draw $56.25 and was found by a Box-vs-SharePoint total mismatch.

MEASURED RED: with `_assert_details_row_shape` replaced by a no-op — the
pre-fix code — the four `pytest.raises` tests below fail with DID NOT RAISE,
and `test_the_shift_is_real_when_the_guard_is_removed` reproduces the exact
field-for-field corruption seen on EVR r1383. The two tests that stay green
either way say so in their own docstring: they guard against the fix
over-reaching, not against the defect.
"""

from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from integrations.box.excel.business.workbook_editor import (
    DEFAULT_KEY_COL_INDEX,
    DETAILS_ROW_WIDTH,
    DRAW_REQUEST_COL_INDEX,
    DetailsRowShapeError,
    _write_row_values,
    apply_rows_to_details,
)

SHEET = "Tracking Budget DETAILS"


def _tracker_bytes() -> bytes:
    """A minimal but structurally real tracker: blank row 1, header row 2, one
    populated 35.01 cost line (so _find_insertion_row has a group to match),
    and a SUBTOTAL row below it."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    headers = {
        2: "Cost Code", 3: "Cost Sub Code", 4: "CODE", 5: "CATEGORY",
        6: "BUDGET AMOUNT", 8: "DRAW REQUEST DATE", 9: "DATE",
        10: "PAYABLE TO", 11: "INVOICE #", 12: "DESCRIPTION", 13: "Ck",
        14: "AMOUNT BILLABLE", 15: "AMOUNT NOT BILLABLE",
    }
    for col, text in headers.items():
        ws.cell(row=2, column=col, value=text)
    existing = {2: "35", 3: "35.01", 10: "Prior Vendor", 12: "Prior Line",
                13: "Bill", 14: 100.0, 26: "KEY-EXISTING"}
    for col, value in existing.items():
        ws.cell(row=3, column=col, value=value)
    ws.cell(row=4, column=5, value="SUBTOTAL")
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _valid_bill_row() -> list:
    """The EVR r1383 line as it SHOULD be built — 26 cells, 0-based."""
    row = [None] * DETAILS_ROW_WIDTH
    row[1] = "35"                                    # B
    row[2] = "35.01"                                 # C
    row[8] = "2026-02-28"                            # I
    row[9] = "Michael Jacobson"                      # J
    row[10] = "2026.02.28.EVR"                       # K
    row[11] = "Drywall Labor"                        # L
    row[12] = "Bill"                                 # M
    row[13] = 56.25                                  # N
    row[DEFAULT_KEY_COL_INDEX] = "41C564EE-F027-4EF8-9FA6-70DE9268A8F5"
    return row


def _shifted_row() -> list:
    """The corruption verbatim: the draw stamp spliced in ahead of col I."""
    row = _valid_bill_row()
    row.insert(DRAW_REQUEST_COL_INDEX, "2026-09-18")
    return row


def _row_with_key(ws, key):
    for r in range(1, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            value = ws.cell(row=r, column=c).value
            if value and str(value).strip() == key:
                return r
    raise AssertionError(f"key {key!r} not found in sheet")


def test_an_over_wide_row_is_refused_on_the_insert_path():
    """The field defect. 27 cells must never reach the workbook."""
    with pytest.raises(DetailsRowShapeError, match="exactly 26 cells"):
        apply_rows_to_details(_tracker_bytes(), SHEET, [_shifted_row()])


def test_an_over_wide_row_is_refused_by_the_bare_cell_writer():
    """Pins the PLACEMENT of the guard, which is the load-bearing decision.

    The manual workbook-reconciliation playbook calls `_write_row_values`
    directly and never goes through `apply_rows_to_details`, so a guard that
    lived only on the insert path would not have covered the caller most
    likely to hand-assemble a row. Revert the call in `_write_row_values` and
    only this test goes red."""
    wb = Workbook()
    ws = wb.active
    with pytest.raises(DetailsRowShapeError, match="exactly 26 cells"):
        _write_row_values(ws, 10, _shifted_row())


def test_text_in_the_amount_column_is_refused():
    """The money symptom on its own: col N holding a type label instead of a
    number makes every SUMIFS over the column skip the row."""
    row = _valid_bill_row()
    row[13] = "Bill"
    with pytest.raises(DetailsRowShapeError, match="non-numeric"):
        apply_rows_to_details(_tracker_bytes(), SHEET, [row])


def test_a_keyless_row_is_refused_on_the_insert_path():
    """A keyless INSERT is not 'possibly a duplicate' — apply_rows_to_details
    dedupes on col Z, so it is guaranteed to duplicate on the next drain."""
    row = _valid_bill_row()
    row[DEFAULT_KEY_COL_INDEX] = ""
    with pytest.raises(DetailsRowShapeError, match="col Z"):
        apply_rows_to_details(_tracker_bytes(), SHEET, [row])


def test_a_keyless_row_is_still_allowed_by_the_bare_cell_writer():
    """GREEN both sides — an over-reach guard, not a regression guard.

    DETAILS legitimately carries keyless rows: the per-draw builder's-fee
    lines (cost code 90.01, invoice number in K, no source line item) have no
    public_id to key on. Requiring a key in `_write_row_values` — as the first
    draft of this fix did — would have refused to write them."""
    wb = Workbook()
    ws = wb.active
    fee_row = [None] * DETAILS_ROW_WIDTH
    fee_row[1] = "90"
    fee_row[2] = "90.01"
    fee_row[10] = "EVR-20"
    fee_row[13] = 1314.15
    _write_row_values(ws, 10, fee_row)
    assert ws.cell(row=10, column=14).value == 1314.15
    assert ws.cell(row=10, column=11).value == "EVR-20"


def test_a_correct_row_still_lands_on_the_canonical_columns():
    """GREEN both sides — guards the fix against over-reach."""
    result = apply_rows_to_details(_tracker_bytes(), SHEET, [_valid_bill_row()])
    ws = load_workbook(BytesIO(result["bytes"]))[SHEET]
    r = _row_with_key(ws, "41C564EE-F027-4EF8-9FA6-70DE9268A8F5")
    assert ws.cell(row=r, column=13).value == "Bill"          # M type
    assert ws.cell(row=r, column=14).value == 56.25           # N amount, numeric
    assert ws.cell(row=r, column=15).value in (None, "")      # O empty
    assert ws.cell(row=r, column=26).value == "41C564EE-F027-4EF8-9FA6-70DE9268A8F5"
    assert ws.cell(row=r, column=27).value in (None, "")      # AA never used
    assert result["applied"] == 1


def test_the_shift_is_real_when_the_guard_is_removed(monkeypatch):
    """Reproduces EVR r1383 field-for-field against the PRE-FIX code path.

    This asserts the defect, not the fix, so it is the one test here that
    would pass on the old code — its job is to prove the guard is load-bearing
    rather than decorative, and to pin exactly which cells move."""
    import integrations.box.excel.business.workbook_editor as editor

    monkeypatch.setattr(editor, "_assert_details_row_shape", lambda row, **kw: None)
    result = editor.apply_rows_to_details(_tracker_bytes(), SHEET, [_shifted_row()])
    ws = load_workbook(BytesIO(result["bytes"]))[SHEET]
    r = _row_with_key(ws, "41C564EE-F027-4EF8-9FA6-70DE9268A8F5")
    assert ws.cell(row=r, column=14).value == "Bill"      # N holds TEXT
    assert ws.cell(row=r, column=15).value == 56.25       # amount fell into O
    assert ws.cell(row=r, column=26).value in (None, "")  # Z empty
    assert ws.cell(row=r, column=27).value == "41C564EE-F027-4EF8-9FA6-70DE9268A8F5"
