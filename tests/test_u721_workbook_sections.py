"""Unit tests for integrations.ms.sharepoint.excel.workbook_sections (U-721a)."""
from __future__ import annotations

import uuid

import pytest

from entities.bill.business import service as bill_service
from integrations.ms.sharepoint.excel import workbook_sections as ws
from integrations.ms.sharepoint.excel.workbook_sections import (
    SectionGroup,
    batch_entity_public_id,
    group_rows_by_subcostcode,
    plan_section_inserts,
    sub_cost_code_number_matches_holding,
)


def _section(scc: str) -> list:
    return ["", scc.split(".")[0], scc] + [""] * 23


def _data_row(scc: str) -> list:
    row = _section(scc)
    row[8], row[9] = "2026-01-01", "Vendor"
    return row


def _std_sheet() -> list[list]:
    return [
        ["", "", "SCC"] + [""] * 23,
        _section("10.01"),
        _data_row("10.01"),
        _section("20.01"),
        _data_row("20.01"),
    ]


def test_batch_entity_public_id():
    project_id = 5
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ms-excel-batch:{project_id}"))
    assert batch_entity_public_id(project_id) == expected


def test_group_rows_by_subcostcode_first_seen_and_merge():
    rows = [
        ("10.01", ["a"]),
        ("20.01", ["b1"]),
        ("10.01", ["a2"]),
        ("20.01", ["b2"]),
        ("", ["empty"]),
    ]
    groups = group_rows_by_subcostcode(rows)
    assert [g.sub_cost_code for g in groups] == ["10.01", "20.01", ""]
    assert groups[0].rows == [["a"], ["a2"]]
    assert groups[1].rows == [["b1"], ["b2"]]
    assert groups[2].rows == [["empty"]]


def test_plan_section_inserts_sort_and_append():
    sheet = _std_sheet()
    groups = [
        SectionGroup(sub_cost_code="20.01", rows=[["r20"]]),
        SectionGroup(sub_cost_code="99.99", rows=[["r99"]]),
        SectionGroup(sub_cost_code="10.01", rows=[["r10"]]),
    ]
    insert_groups, append_rows = plan_section_inserts(sheet, groups, log_no_match=False)
    assert append_rows == [["r99"]]
    assert [g[2] for g in insert_groups] == ["20.01", "10.01"]
    assert all(g[0] for g in insert_groups)


def test_bill_service_reexports_same_objects():
    assert bill_service.find_insertion_row_for_subcostcode is ws.find_insertion_row_for_subcostcode
    assert bill_service.find_first_row_for_subcostcode is ws.find_first_row_for_subcostcode
    assert bill_service.sort_insert_groups_for_excel_enqueue is ws.sort_insert_groups_for_excel_enqueue


HOLDING = frozenset({"00.02"})


@pytest.mark.parametrize(
    "candidate,expected",
    [
        ("00.02", True),
        ("0.02", True),
        ("00.020", True),
        (0.02, True),
        ("00.2", False),
        ("10.01", False),
        ("", False),
        (None, False),
    ],
)
def test_sub_cost_code_number_matches_holding(candidate, expected):
    assert sub_cost_code_number_matches_holding(candidate, HOLDING) is expected


def test_enqueue_section_inserts_holds_matching_rows(caplog):
    import logging
    from unittest.mock import MagicMock, patch
    from types import SimpleNamespace

    from integrations.ms.sharepoint.excel.workbook_sections import enqueue_section_inserts

    caplog.set_level(logging.WARNING)
    holding_row = _section("00.02")
    holding_row[8], holding_row[9] = "2026-01-01", "SecretVendor"
    real_row = _section("10.01")
    real_row[8], real_row[9] = "2026-01-01", "Vendor"
    groups = [
        SectionGroup(sub_cost_code="00.02", rows=[holding_row]),
        SectionGroup(sub_cost_code="10.01", rows=[real_row]),
    ]
    ms_outbox = MagicMock()
    ms_outbox.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
    with patch(
        "integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox
    ):
        res = enqueue_section_inserts(
            worksheet_values=_std_sheet(),
            groups=groups,
            holding_sub_cost_codes=HOLDING,
            entity_type="Bill",
            entity_public_id="pid",
            drive_id="d",
            item_id="i",
            worksheet_name="DETAILS",
            batch=False,
        )
    assert res.held_count == 1
    assert res.synced_count == 1
    assert ms_outbox.enqueue_excel_insert.call_count == 1
    enqueued = ms_outbox.enqueue_excel_insert.call_args.kwargs["values"]
    assert all(r[2] != "00.02" for r in enqueued)
    assert "ms.excel.holding_rows_held" in caplog.text
    assert "SecretVendor" not in caplog.text


def _run_enqueue_with(holding, groups):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from integrations.ms.sharepoint.excel.workbook_sections import enqueue_section_inserts

    ms_outbox = MagicMock()
    ms_outbox.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
    ms_outbox.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    with patch(
        "integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox
    ):
        res = enqueue_section_inserts(
            worksheet_values=_std_sheet(),
            groups=groups,
            holding_sub_cost_codes=holding,
            entity_type="Bill",
            entity_public_id="bill-1",
            drive_id="d",
            item_id="i",
            worksheet_name="DETAILS",
            batch=False,
        )
    sent = [
        row
        for call in ms_outbox.enqueue_excel_insert.call_args_list
        + ms_outbox.enqueue_excel_append.call_args_list
        for row in call.kwargs["values"]
    ]
    return res, sent


@pytest.mark.parametrize("holding", [frozenset(), set(), [], ("10.99",)])
def test_00_02_is_always_held_whatever_holding_set_is_passed(holding):
    """U-721 r2: the rule may not depend on the holding flag being configured or on the
    caller passing a frozenset - 00.02 is held even when the set passed is empty."""
    groups = [
        SectionGroup(sub_cost_code="00.02", rows=[_section("00.02")]),
        SectionGroup(sub_cost_code="10.01", rows=[_section("10.01")]),
    ]
    res, sent = _run_enqueue_with(holding, groups)
    assert res.held_count == 1
    assert all(str(r[2]) != "00.02" for r in sent)
    assert any(str(r[2]) == "10.01" for r in sent)


def test_holding_set_as_plain_set_is_honoured():
    groups = [
        SectionGroup(sub_cost_code="10.99", rows=[_section("10.99")]),
        SectionGroup(sub_cost_code="10.01", rows=[_section("10.01")]),
    ]
    res, sent = _run_enqueue_with({"10.99"}, groups)
    assert res.held_count == 1
    assert all(str(r[2]) != "10.99" for r in sent)


def test_non_iterable_holding_set_fails_loudly():
    with pytest.raises(TypeError):
        _run_enqueue_with(None, [SectionGroup(sub_cost_code="10.01", rows=[_section("10.01")])])


def test_holding_numbers_fail_closed_when_a_holding_code_cannot_be_resolved():
    from unittest.mock import MagicMock

    from entities.sub_cost_code.business.service import SubCostCodeService

    svc = SubCostCodeService.__new__(SubCostCodeService)
    svc._holding_sub_cost_code_numbers = None
    svc.holding_sub_cost_code_ids = MagicMock(return_value=[7])
    svc.read_by_id = MagicMock(return_value=None)
    with pytest.raises(RuntimeError):
        svc.holding_sub_cost_code_numbers()
