"""U-437 — tracker header wording varies; column POSITION does not.

A 2026-09-09 survey of 10 mapped project trackers found column H (the draw tag)
spelled "DRAW REQUEST DATE", "DRAW REQUEST" and "DRAW", and column N (the
billable amount) spelled "AMOUNT BILLABLE", "BILLABLE AMOUNT", "AMOUNT PAID" and
blank. All 10 failed `detect_header_and_columns`, so the draw-push worksheet step
had never completed on any of them.

The safe wordings are aliased. "AMOUNT PAID" is NOT: it is a different quantity,
and mapping it into the billable slot would reconcile client billing against
amounts paid. That exclusion is the point of this file.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from entities.invoice.business.worksheet_reconcile import (
    _KNOWN_HEADERS,
    detect_header_and_columns,
)

# Real layout, shared by every tracker sampled: H=7 draw, I=8 date, L=11 desc, N=13 billable.
def _sheet(h_header: str, n_header: str) -> list[list]:
    header = [""] * 15
    header[1], header[2], header[4] = "Cost Code", "Cost Sub Code", "CATEGORY"
    header[7], header[8] = h_header, "DATE"
    header[9], header[10], header[11] = "PAYABLE TO", "INVOICE #", "DESCRIPTION"
    header[12], header[13] = "Ck", n_header
    row = [""] * 15
    row[7], row[8], row[11], row[13] = "SHT-25", "46081", "Pool Permit", "870.35"
    return [[""] * 15, header, row]


@pytest.mark.parametrize("h_header", ["DRAW REQUEST DATE", "DRAW REQUEST", "DRAW"])
def test_every_observed_draw_header_maps_to_the_same_column(h_header: str) -> None:
    _, col_map, _ = detect_header_and_columns(_sheet(h_header, "AMOUNT BILLABLE"))
    assert col_map["draw_request_date"] == 7, f"{h_header!r} did not map to column H"


@pytest.mark.parametrize("n_header", ["BILLABLE", "AMOUNT BILLABLE", "BILLABLE AMOUNT"])
def test_every_safe_billable_header_maps_to_the_same_column(n_header: str) -> None:
    _, col_map, _ = detect_header_and_columns(_sheet("DRAW", n_header))
    assert col_map["billable"] == 13, f"{n_header!r} did not map to column N"


def test_amount_paid_is_never_treated_as_billable() -> None:
    """The load-bearing exclusion. 'AMOUNT PAID' sits in column N on 4 of 10
    sampled trackers but is a DIFFERENT quantity; accepting it would reconcile
    billing against amounts paid. Those trackers must keep failing loudly."""
    assert "AMOUNT PAID" not in _KNOWN_HEADERS
    with pytest.raises(HTTPException) as exc:
        detect_header_and_columns(_sheet("DRAW", "AMOUNT PAID"))
    assert "billable" in str(exc.value.detail)


def test_blank_billable_header_still_halts() -> None:
    """3 of 10 sampled trackers have a blank column-N header — nothing to match,
    so there is no safe inference available."""
    with pytest.raises(HTTPException) as exc:
        detect_header_and_columns(_sheet("DRAW", ""))
    assert "billable" in str(exc.value.detail)


def test_the_sht_tracker_shape_now_passes() -> None:
    """SHT-25's live tracker: column H reads 'DRAW', column N 'AMOUNT BILLABLE'.
    This exact pair halted the forced draw push on 2026-09-09."""
    idx, col_map, data_rows = detect_header_and_columns(_sheet("DRAW", "AMOUNT BILLABLE"))
    assert idx == 1
    for key, col in (("draw_request_date", 7), ("date", 8), ("description", 11), ("billable", 13)):
        assert col_map[key] == col
    assert len(data_rows) == 1
