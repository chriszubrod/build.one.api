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
    DetailsLayoutError,
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
    draft of this fix did — would have refused to write them. The key is checked
    on the INSERT path instead, at the caller's own `key_col_index`."""
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
    """Reproduces EVR r1383 field-for-field against the PRE-FIX cell writer.

    This asserts the defect, not the fix, so it is the one test here that would
    pass on the old code — its job is to prove the guard is load-bearing rather
    than decorative, and to pin exactly which cells move.

    Goes through `_write_row_values` rather than `apply_rows_to_details` so it
    isolates the column shift from the dedupe logic: a shifted row's key lands
    in AA, leaving index 25 empty, so the insert path's own key check would stop
    it before any cell was written and the shift would never be observable. That
    is the correct behaviour and is pinned separately by
    test_a_keyless_row_is_refused_on_the_insert_path."""
    import integrations.box.excel.business.workbook_editor as editor

    monkeypatch.setattr(editor, "_assert_details_row_shape", lambda row, **kw: None)
    wb = Workbook()
    ws = wb.active
    editor._write_row_values(ws, 10, _shifted_row())
    assert ws.cell(row=10, column=14).value == "Bill"      # N holds TEXT
    assert ws.cell(row=10, column=15).value == 56.25       # amount fell into O
    assert ws.cell(row=10, column=26).value in (None, "")  # Z empty
    assert ws.cell(row=10, column=27).value == "41C564EE-F027-4EF8-9FA6-70DE9268A8F5"


# ---------------------------------------------------------------------------
# Sheet layout: a well-formed MODERN row written into an OLD-template sheet is
# corruption that no row-level check can see. CC (41), CBT (101) and ML (74)
# sit one column left from the date rightward — amount at M, not N — so every
# row the writer has ever put in them stranded its amount in a column no
# formula reads. Verified live 2026-09-28: $525 in CC r1032, $95 across CBT
# r1120/r2398/r2399.
# ---------------------------------------------------------------------------

def _old_template_bytes() -> bytes:
    """CC/CBT's real layout: amount at M (13), no amount-not-billable column."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    for col, text in {
        1: "Cost Code", 2: "Cost Sub Code", 3: "CODE", 4: "CATEGORY",
        5: "BUDGET AMOUNT", 7: "DRAW REQUEST DATE", 8: "DATE",
        9: "PAYABLE TO", 10: "INVOICE #", 11: "DESCRIPTION", 12: "Ck",
        13: "AMOUNT PAID",
    }.items():
        ws.cell(row=2, column=col, value=text)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def test_an_old_template_sheet_is_refused_before_anything_is_written():
    """The second defect, and the one the row-shape guard cannot see: the row is
    a perfectly well-formed 26-cell modern row — it is the SHEET that differs.

    The real old template has BOTH columns displaced (draw tag at G, amount at
    M), and the guard reports whichever it meets first in scan order — the draw
    tag, since G precedes M. Asserting on the specific message would pin scan
    order rather than the behaviour, so this asserts the refusal and names the
    sheet; the amount-specific message has its own test below."""
    with pytest.raises(DetailsLayoutError) as excinfo:
        apply_rows_to_details(_old_template_bytes(), SHEET, [_valid_bill_row()])
    assert SHEET in str(excinfo.value)


def test_a_displaced_amount_column_is_named_in_the_error():
    """Amount at M with the draw tag correctly at H — isolates the amount check,
    which is the one that costs money: the amount lands in a column no formula
    reads while the type label lands in the column every SUMIFS sums."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.cell(row=2, column=8, value="DRAW REQUEST DATE")
    ws.cell(row=2, column=13, value="AMOUNT PAID")
    out = BytesIO()
    wb.save(out)
    with pytest.raises(DetailsLayoutError, match="amount column"):
        apply_rows_to_details(out.getvalue(), SHEET, [_valid_bill_row()])


def test_the_modern_layout_is_accepted():
    """GREEN both sides — over-reach guard. The 25 modern workbooks must keep
    working, including ones whose column N is headed 'AMOUNT PAID' rather than
    'AMOUNT BILLABLE' (the wording drifts; only the POSITION is checked)."""
    result = apply_rows_to_details(_tracker_bytes(), SHEET, [_valid_bill_row()])
    assert result["applied"] == 1


def test_a_sheet_with_no_locatable_headers_is_left_alone():
    """GREEN both sides. The U-437 survey found trackers with a BLANK amount
    header; refusing those would reject workbooks this writer has always handled
    correctly. The guard fires on positive evidence of a different layout, never
    on absence of evidence."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.cell(row=3, column=2, value="35")
    ws.cell(row=3, column=3, value="35.01")
    ws.cell(row=3, column=26, value="KEY-EXISTING")
    out = BytesIO()
    wb.save(out)
    result = apply_rows_to_details(out.getvalue(), SHEET, [_valid_bill_row()])
    assert result["applied"] == 1


def test_a_shifted_draw_tag_column_is_also_refused():
    """The draw tag is stamped at H by a different code path, so its position is
    checked independently of the amount column."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.cell(row=2, column=6, value="DRAW REQUEST DATE")   # F, not H
    ws.cell(row=2, column=14, value="AMOUNT BILLABLE")
    out = BytesIO()
    wb.save(out)
    with pytest.raises(DetailsLayoutError, match="draw tag"):
        apply_rows_to_details(out.getvalue(), SHEET, [_valid_bill_row()])


def test_the_duplicated_column_constants_agree_across_modules():
    """row_builder and workbook_editor each define DETAILS_ROW_WIDTH and
    DRAW_REQUEST_COL_INDEX, and they MUST agree — the row builder writes to the
    positions the editor validates.

    Deliberately a consistency test rather than an import: row_builder's module
    docstring states it keeps its imports light so that importing it, and the
    outbox worker that dispatches to it, does not pull in a heavier stack. Having
    it import the editor would undo that for the sake of two integers. This test
    is the cheaper coupling and it still catches drift."""
    from integrations.box.excel.business import row_builder

    assert row_builder.DETAILS_ROW_WIDTH == DETAILS_ROW_WIDTH
    assert row_builder.DRAW_REQUEST_COL_INDEX == DRAW_REQUEST_COL_INDEX


def test_the_draw_tag_aliases_agree_with_the_reconcile_reader():
    """The writer's draw-tag wordings must not drift from the reader's.

    NOT true of the amount wordings, and that asymmetry is deliberate: the
    reader excludes "AMOUNT PAID" on purpose (mapping it into the billable slot
    would reconcile client billing against amounts paid, which
    test_u437_worksheet_header_aliases pins), while this writer MUST recognise it
    because it is literally the header on the four old-template sheets."""
    from integrations.box.excel.business.workbook_editor import (
        _AMOUNT_HEADERS, _DRAW_TAG_HEADERS,
    )
    from entities.invoice.business.worksheet_reconcile import _KNOWN_HEADERS

    reader_draw = {k for k, v in _KNOWN_HEADERS.items() if v == "draw_request_date"}
    assert _DRAW_TAG_HEADERS <= reader_draw
    assert "AMOUNT PAID" in _AMOUNT_HEADERS
    assert "AMOUNT PAID" not in _KNOWN_HEADERS
