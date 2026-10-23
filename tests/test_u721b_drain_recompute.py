"""
U-721b — recompute DETAILS insert row_index at drain from the same used-range
read that powers column-Z dedup (U-440a).
"""
from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from unittest.mock import patch

import pytest

from integrations.ms.base.errors import MsGraphError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.worker import (
    RECONCILIATION_KEY_COL_INDEX,
    MsOutboxWorker,
)
from integrations.ms.sharepoint.excel.workbook_sections import (
    SectionGroup,
    find_insertion_row_for_subcostcode,
    plan_section_inserts,
)

_CLIENT = "integrations.ms.sharepoint.external.client"


def _section(scc: str) -> list:
    return ["", scc.split(".")[0], scc] + [""] * 23


def _data_row(
    scc: str,
    *,
    z: str | None = None,
    col_c_override=None,
) -> list:
    row = _section(scc)
    row[8], row[9] = "2026-01-01", "Vendor"
    if z is not None:
        row[RECONCILIATION_KEY_COL_INDEX] = z
    if col_c_override is not None:
        row[2] = col_c_override
    return row


def _std_sheet() -> list[list]:
    return [
        ["", "", "SCC"] + [""] * 23,
        _section("10.01"),
        _data_row("10.01", z="old-10"),
        _section("20.01"),
        _data_row("20.01", z="old-20"),
        _section("30.01"),
        _data_row("30.01", z="old-30"),
    ]


def _used_range(rows, status_code=200):
    return {"status_code": status_code, "range": {"values": rows}, "message": "ok"}


def _outbox_row(outbox_id=9001):
    return MsOutbox(
        id=outbox_id,
        public_id=f"outbox-{outbox_id}",
        row_version="rv-1",
        kind="insert_excel_row",
        entity_type="Bill",
        entity_public_id="bill-1",
        tenant_id="tenant-1",
        request_id="req-1",
        payload=json.dumps({}),
        status="in_progress",
        attempts=0,
        ready_after=None,
        correlation_id=None,
    )


def _payload(values, row_index: int, **overrides):
    payload = {
        "drive_id": "drive-1",
        "item_id": "item-1",
        "worksheet_name": "DETAILS",
        "row_index": row_index,
        "values": values,
        "session_id": None,
    }
    payload.update(overrides)
    return payload


def _warning_events(caplog) -> list[str]:
    return [
        r.__dict__.get("event_name") or r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ]


def _apply_insert(sheet: list[list], row_index: int, values: list[list]) -> None:
    """Simulate Graph insert at 1-based row_index."""
    at = row_index - 1
    for offset, row in enumerate(values):
        sheet.insert(at + offset, list(row))


# --------------------------------------------------------------------------- #


def test_insert_at_live_row_when_stored_index_matches(caplog):
    sheet = deepcopy(_std_sheet())
    scc = "20.01"
    live = find_insertion_row_for_subcostcode(sheet, scc)
    new_row = _data_row(scc, z=str(uuid.uuid4()))

    worker = MsOutboxWorker()
    with caplog.at_level(logging.WARNING):
        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            return_value=_used_range(sheet),
        ), patch(
            f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
        ) as insert, patch(f"{_CLIENT}.append_excel_rows") as append:
            worker._handle_insert_excel_row(
                _outbox_row(), _payload([new_row], live)
            )

    insert.assert_called_once()
    assert insert.call_args.kwargs["row_index"] == live
    append.assert_not_called()
    assert "ms.outbox.excel.insert_row_recomputed" not in _warning_events(caplog)


def test_insert_recomputes_when_stored_index_stale(caplog):
    sheet = deepcopy(_std_sheet())
    scc = "20.01"
    live = find_insertion_row_for_subcostcode(sheet, scc)
    stale = live + 2
    new_row = _data_row(scc, z=str(uuid.uuid4()))

    worker = MsOutboxWorker()
    with caplog.at_level(logging.WARNING):
        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            return_value=_used_range(sheet),
        ), patch(
            f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
        ) as insert, patch(f"{_CLIENT}.append_excel_rows") as append:
            worker._handle_insert_excel_row(
                _outbox_row(), _payload([new_row], stale)
            )

    insert.assert_called_once()
    assert insert.call_args.kwargs["row_index"] == live
    assert insert.call_args.kwargs["row_index"] != stale
    append.assert_not_called()
    assert "ms.outbox.excel.insert_row_recomputed" in _warning_events(caplog)


def test_insert_appends_when_section_missing_on_live_sheet(caplog):
    sheet = deepcopy(_std_sheet())
    scc = "99.99"
    new_row = _data_row(scc, z=str(uuid.uuid4()))
    stored = 42

    worker = MsOutboxWorker()
    with caplog.at_level(logging.WARNING):
        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            return_value=_used_range(sheet),
        ), patch(f"{_CLIENT}.insert_excel_rows") as insert, patch(
            f"{_CLIENT}.append_excel_rows", return_value={"status_code": 200}
        ) as append:
            worker._handle_insert_excel_row(
                _outbox_row(), _payload([new_row], stored)
            )

    insert.assert_not_called()
    append.assert_called_once()
    assert append.call_args.kwargs["values"] == [new_row]
    assert "ms.outbox.excel.insert_section_missing_appended" in _warning_events(
        caplog
    )


def test_insert_keeps_stored_index_when_column_c_mixed(caplog):
    sheet = deepcopy(_std_sheet())
    stored = 5
    batch = [
        _data_row("10.01", z=str(uuid.uuid4())),
        _data_row("20.01", z=str(uuid.uuid4())),
    ]

    worker = MsOutboxWorker()
    with caplog.at_level(logging.WARNING):
        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            return_value=_used_range(sheet),
        ), patch(
            f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
        ) as insert:
            worker._handle_insert_excel_row(
                _outbox_row(), _payload(batch, stored)
            )

    insert.assert_called_once()
    assert insert.call_args.kwargs["row_index"] == stored
    assert "ms.outbox.excel.insert_row_kept_stored" in _warning_events(caplog)


def test_insert_keeps_stored_index_when_column_c_blank(caplog):
    sheet = deepcopy(_std_sheet())
    stored = 7
    row = _data_row("10.01", z=str(uuid.uuid4()), col_c_override="")

    worker = MsOutboxWorker()
    with caplog.at_level(logging.WARNING):
        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            return_value=_used_range(sheet),
        ), patch(
            f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
        ) as insert:
            worker._handle_insert_excel_row(
                _outbox_row(), _payload([row], stored)
            )

    assert insert.call_args.kwargs["row_index"] == stored
    assert "ms.outbox.excel.insert_row_kept_stored" in _warning_events(caplog)


def test_numeric_equivalence_on_column_c_recomputes_to_live_row():
    sheet = deepcopy(_std_sheet())
    for row in sheet:
        if len(row) > 2 and str(row[2]).strip() == "20.01":
            row[2] = 20.1

    scc = "20.10"
    live = find_insertion_row_for_subcostcode(sheet, scc)
    new_row = _data_row("20.10", z=str(uuid.uuid4()))
    stale = live + 3

    worker = MsOutboxWorker()
    with patch(
        f"{_CLIENT}.get_excel_used_range_values",
        return_value=_used_range(sheet),
    ), patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ) as insert:
        worker._handle_insert_excel_row(
            _outbox_row(), _payload([new_row], stale)
        )

    assert insert.call_args.kwargs["row_index"] == live


def test_dedup_runs_before_recompute_no_write_when_all_present():
    sheet = deepcopy(_std_sheet())
    pid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    new_row = _data_row("20.01", z=pid)
    sheet.append(_data_row("20.01", z=pid))

    worker = MsOutboxWorker()
    with patch(
        f"{_CLIENT}.get_excel_used_range_values",
        return_value=_used_range(sheet),
    ) as get_range, patch(f"{_CLIENT}.insert_excel_rows") as insert, patch(
        f"{_CLIENT}.append_excel_rows"
    ) as append:
        worker._handle_insert_excel_row(
            _outbox_row(), _payload([new_row], row_index=999)
        )

    insert.assert_not_called()
    append.assert_not_called()
    assert get_range.call_count == 1


def test_exactly_one_used_range_read_per_drain():
    sheet = deepcopy(_std_sheet())
    new_row = _data_row("10.01", z=str(uuid.uuid4()))
    live = find_insertion_row_for_subcostcode(sheet, "10.01")

    worker = MsOutboxWorker()
    with patch(
        f"{_CLIENT}.get_excel_used_range_values",
        return_value=_used_range(sheet),
    ) as get_range, patch(
        f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
    ):
        worker._handle_insert_excel_row(
            _outbox_row(), _payload([new_row], live)
        )

    assert get_range.call_count == 1


def test_failed_used_range_read_still_raises_fail_closed():
    worker = MsOutboxWorker()
    bad = {"status_code": 500, "range": None, "message": "boom"}
    new_row = _data_row("10.01", z=str(uuid.uuid4()))

    with patch(f"{_CLIENT}.get_excel_used_range_values", return_value=bad), patch(
        f"{_CLIENT}.insert_excel_rows"
    ) as insert, patch(f"{_CLIENT}.append_excel_rows") as append:
        with pytest.raises(MsGraphError):
            worker._handle_insert_excel_row(
                _outbox_row(), _payload([new_row], row_index=4)
            )

    insert.assert_not_called()
    append.assert_not_called()


def _row_section_bounds(sheet: list[list], scc: str) -> tuple[int, int]:
    """Inclusive 0-based indices of rows belonging to scc block (column C match)."""
    from integrations.ms.sharepoint.excel.workbook_sections import (
        sub_cost_code_number_matches_holding,
    )

    holding = frozenset({scc})
    indices = [
        i
        for i, row in enumerate(sheet)
        if i > 0 and sub_cost_code_number_matches_holding(
            row[2] if len(row) > 2 else None, holding
        )
    ]
    assert indices, f"no section for {scc}"
    return indices[0], indices[-1]


def test_e2e_upper_section_drain_first_then_lower_retry_recomputes():
    """
    Both groups enqueued from the same snapshot. Draining 10.01 first shifts the
    20.01 block down, so 20.01's stored row_index is stale on retry — recompute
    must still land each line inside its own SCC section.
    """
    sheet = deepcopy(_std_sheet())
    groups = [
        # Three rows: enough that a stale 20.01 index lands inside 10.01 (F1).
        SectionGroup(
            sub_cost_code="10.01",
            rows=[_data_row("10.01", z=str(uuid.uuid4())) for _ in range(3)],
        ),
        SectionGroup(
            sub_cost_code="20.01",
            rows=[_data_row("20.01", z=str(uuid.uuid4()))],
        ),
    ]
    insert_groups, _append = plan_section_inserts(sheet, groups, log_no_match=False)
    assert len(insert_groups) == 2

    worker = MsOutboxWorker()
    stored_by_scc = {g[2]: g[0] for g in insert_groups}
    rows_by_scc = {g[2]: g[1] for g in insert_groups}
    stale_20 = stored_by_scc["20.01"]

    def drain_group(scc: str):
        nonlocal sheet
        stored = stored_by_scc[scc]
        values = rows_by_scc[scc]

        with patch(
            f"{_CLIENT}.get_excel_used_range_values",
            side_effect=lambda *a, **k: _used_range(deepcopy(sheet)),
        ), patch(
            f"{_CLIENT}.insert_excel_rows", return_value={"status_code": 200}
        ) as insert, patch(f"{_CLIENT}.append_excel_rows") as append:

            def _record_insert(**kwargs):
                _apply_insert(sheet, kwargs["row_index"], kwargs["values"])
                return {"status_code": 200}

            insert.side_effect = _record_insert
            worker._handle_insert_excel_row(
                _outbox_row(), _payload(values, stored)
            )
        append.assert_not_called()
        return insert.call_args.kwargs["row_index"]

    drain_group("10.01")
    after_upper = deepcopy(sheet)
    expected_20 = find_insertion_row_for_subcostcode(after_upper, "20.01")
    # Control: without the recompute, the stale index would put the row (0-based
    # stale_20 - 1) outside the 20.01 section of the shifted sheet.
    lo_20, hi_20 = _row_section_bounds(after_upper, "20.01")
    assert not (lo_20 <= stale_20 - 1 <= hi_20)
    live_20 = drain_group("20.01")
    assert live_20 != stale_20
    assert live_20 == expected_20

    for scc, values in rows_by_scc.items():
        z_key = values[0][RECONCILIATION_KEY_COL_INDEX]
        placed = next(
            r
            for r in sheet
            if len(r) > RECONCILIATION_KEY_COL_INDEX
            and r[RECONCILIATION_KEY_COL_INDEX] == z_key
        )
        idx = sheet.index(placed)
        lo, hi = _row_section_bounds(sheet, scc)
        assert lo <= idx <= hi, f"{scc} row landed outside its section"


def test_failed_append_on_missing_section_raises():
    sheet = deepcopy(_std_sheet())
    values = [_data_row("99.99", z=str(uuid.uuid4()))]
    with patch(
        f"{_CLIENT}.get_excel_used_range_values", return_value=_used_range(sheet)
    ), patch(f"{_CLIENT}.insert_excel_rows") as insert, patch(
        f"{_CLIENT}.append_excel_rows", return_value={"status_code": 503, "message": "down"}
    ):
        with pytest.raises(MsGraphError):
            MsOutboxWorker()._handle_insert_excel_row(_outbox_row(), _payload(values, 4))
    insert.assert_not_called()


def test_insert_with_empty_snapshot_raises_without_writing():
    values = [_data_row("10.01", z=str(uuid.uuid4()))]
    with patch(
        f"{_CLIENT}.get_excel_used_range_values", return_value=_used_range([])
    ), patch(f"{_CLIENT}.insert_excel_rows") as insert, patch(
        f"{_CLIENT}.append_excel_rows"
    ) as append:
        with pytest.raises(MsGraphError):
            MsOutboxWorker()._handle_insert_excel_row(_outbox_row(), _payload(values, 3))
    insert.assert_not_called()
    append.assert_not_called()
