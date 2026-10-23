"""Unit tests for integrations.ms.sharepoint.excel.workbook_sections (U-721a)."""
from __future__ import annotations

import uuid

from entities.bill.business import service as bill_service
from integrations.ms.sharepoint.excel import workbook_sections as ws
from integrations.ms.sharepoint.excel.workbook_sections import (
    SectionGroup,
    batch_entity_public_id,
    group_rows_by_subcostcode,
    plan_section_inserts,
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
