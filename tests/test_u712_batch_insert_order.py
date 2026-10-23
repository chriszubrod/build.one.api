"""
U-712: Bill / BillCredit batch Excel insert groups must enqueue bottom-up
(highest row_index first) so MS outbox drain order matches shift semantics.
"""
import pytest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.bill.business.service import (
    find_first_row_for_subcostcode,
    find_insertion_row_for_subcostcode,
)


def _section(scc):
    return ["", scc.split(".")[0], scc] + [""] * 23


def _data(scc, tag, *, type_col="Bill"):
    r = ["", scc.split(".")[0], scc] + [""] * 23
    r[8], r[9], r[12], r[25] = "2026-01-01", "Vendor", type_col, tag
    return r


def _apply_enqueued_inserts(sheet, ms_outbox):
    applied = [list(r) for r in sheet]
    for c in ms_outbox.enqueue_excel_insert.call_args_list:
        r = int(c.kwargs["row_index"])
        for offset, row in enumerate(c.kwargs["values"]):
            applied.insert(r - 1 + offset, list(row))
    return applied


def _assert_new_rows_in_own_sections(applied):
    for i, row in enumerate(applied):
        tag = row[25] if len(row) > 25 else ""
        if not str(tag).startswith("new-"):
            continue
        above = applied[i - 1]
        assert above[2] == row[2], (
            f"new {row[2]} row at sheet row {i + 1} sits under a {above[2]} row (wrong section)"
        )


def _assert_scc_new_rows_in_section(applied, scc):
    """Assert new rows for one SubCostCode sit directly under same-SCC rows."""
    for i, row in enumerate(applied):
        tag = row[25] if len(row) > 25 else ""
        if not str(tag).startswith("new-"):
            continue
        if row[2] != scc:
            continue
        above = applied[i - 1]
        assert above[2] == scc, (
            f"new {scc} row at sheet row {i + 1} sits under a {above[2]} row (wrong section)"
        )


def _workbook_patches(mod, sheet):
    driveitem_repo = MagicMock()
    driveitem_repo.read_all.return_value = [SimpleNamespace(id=7, ms_drive_id=3, item_id="itm")]
    ms_outbox = MagicMock()
    ms_outbox.enqueue_excel_insert.return_value = MagicMock()
    ms_outbox.enqueue_excel_append.return_value = MagicMock()
    return (
        ms_outbox,
        patch.object(mod, "MsDriveItemRepository", return_value=driveitem_repo),
        patch.object(mod, "create_workbook_session", return_value="sess"),
        patch.object(mod, "close_workbook_session"),
        patch.object(
            mod,
            "get_excel_used_range_values",
            return_value={"status_code": 200, "range": {"values": [list(r) for r in sheet]}},
        ),
        patch("integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox),
    )


def test_bill_sync_to_excel_workbook_inserts_land_in_own_sections():
    """BillService.sync_to_excel_workbook — single-bill outbox path."""
    import entities.bill.business.service as bill_mod

    header = ["", "", "SCC"] + [""] * 23
    sheet = [
        header,
        _section("10.01"),
        _data("10.01", "old-a"),
        _section("20.01"),
        _data("20.01", "old-b"),
        _section("30.01"),
        _data("30.01", "old-c"),
    ]
    scc_by_id = {1: "10.01", 2: "20.01"}

    svc = bill_mod.BillService.__new__(bill_mod.BillService)
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.return_value = {"id": 7, "worksheet_name": "DETAILS"}
    svc._drive_repo = MagicMock()
    svc._drive_repo.read_by_id.return_value = SimpleNamespace(drive_id="drv")
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(name="Acme")
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        number=scc_by_id[int(id)]
    )

    def li(n, scc_id):
        return SimpleNamespace(
            id=n,
            public_id=f"new-{n}",
            sub_cost_code_id=scc_id,
            description=f"line {n}",
            price=Decimal("10"),
            amount=Decimal("10"),
        )

    bill = SimpleNamespace(public_id="b-1", vendor_id=1, bill_date="2026-10-01", bill_number="B1")
    line_items = [li(1, 1), li(2, 1), li(3, 2)]

    ms_outbox, *patches = _workbook_patches(bill_mod, sheet)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, project_id=5)

    assert result["errors"] == []
    assert ms_outbox.enqueue_excel_insert.call_count == 2
    _assert_new_rows_in_own_sections(_apply_enqueued_inserts(sheet, ms_outbox))


def test_bill_sync_bills_batch_to_excel_inserts_land_in_own_sections():
    """BillService.sync_bills_batch_to_excel — QBO pull batch path."""
    import entities.bill.business.service as bill_mod

    header = ["", "", "SCC"] + [""] * 23
    sheet = [
        header,
        _section("10.01"),
        _data("10.01", "old-a"),
        _section("20.01"),
        _data("20.01", "old-b"),
    ]
    scc_by_id = {1: "10.01", 2: "20.01"}

    svc = bill_mod.BillService.__new__(bill_mod.BillService)
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.return_value = {"id": 7, "worksheet_name": "DETAILS"}
    svc._drive_repo = MagicMock()
    svc._drive_repo.read_by_id.return_value = SimpleNamespace(drive_id="drv")
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(name="Acme")
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        number=scc_by_id[int(id)]
    )

    def li(n, scc_id):
        return SimpleNamespace(
            id=n,
            public_id=f"new-{n}",
            sub_cost_code_id=scc_id,
            description=f"line {n}",
            price=Decimal("10"),
            amount=None,
        )

    bill = SimpleNamespace(public_id="b-1", vendor_id=1, bill_date="2026-10-01", bill_number="B1")
    pairs = [(bill, [li(1, 1), li(2, 1), li(3, 2)])]

    ms_outbox, *patches = _workbook_patches(bill_mod, sheet)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel(pairs, project_id=5)

    assert result["errors"] == []
    assert ms_outbox.enqueue_excel_insert.call_count == 2
    _assert_new_rows_in_own_sections(_apply_enqueued_inserts(sheet, ms_outbox))


def test_bill_credit_batch_inserts_land_in_own_sections():
    """BillCreditCompleteService.sync_bill_credits_batch_to_excel."""
    import entities.bill_credit.business.complete_service as bc_mod

    header = ["", "", "SCC"] + [""] * 23
    sheet = [
        header,
        _section("10.01"),
        _data("10.01", "old-a", type_col="Credit"),
        _section("20.01"),
        _data("20.01", "old-b", type_col="Credit"),
    ]
    scc_by_id = {1: "10.01", 2: "20.01"}

    svc = bc_mod.BillCreditCompleteService.__new__(bc_mod.BillCreditCompleteService)
    svc.project_excel_connector = MagicMock()
    svc.project_excel_connector.get_excel_for_project.return_value = {"id": 7, "worksheet_name": "DETAILS"}
    svc.drive_repo = MagicMock()
    svc.drive_repo.read_by_id.return_value = SimpleNamespace(drive_id="drv")
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(name="Acme")
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        number=scc_by_id[int(id)]
    )

    def li(n, scc_id):
        return SimpleNamespace(
            id=n,
            public_id=f"new-{n}",
            sub_cost_code_id=scc_id,
            description=f"line {n}",
            amount=Decimal("10"),
        )

    credit = SimpleNamespace(
        public_id="c-1", vendor_id=1, credit_date="2026-10-01", credit_number="C1"
    )
    pairs = [(credit, [li(1, 1), li(2, 1), li(3, 2)])]

    ms_outbox, *patches = _workbook_patches(bc_mod, sheet)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel(pairs, project_id=5)

    assert result["errors"] == []
    assert ms_outbox.enqueue_excel_insert.call_count == 2
    _assert_new_rows_in_own_sections(_apply_enqueued_inserts(sheet, ms_outbox))


def _tie_sheet():
    header = ["", "", "SCC"] + [""] * 23
    return [
        header,
        _section("10.01"),
        _data("20.01", "old-b"),
        _section("30.01"),
    ]


def _run_bill_tie_sync(line_item_scc_ids):
    """Drive sync_to_excel_workbook on the shared tie sheet; return (result, ms_outbox, sheet)."""
    sheet = _tie_sheet()
    assert find_insertion_row_for_subcostcode(sheet, "10.01") == 4
    assert find_insertion_row_for_subcostcode(sheet, "20.01") == 4
    assert find_first_row_for_subcostcode(sheet, "10.01") == 2
    assert find_first_row_for_subcostcode(sheet, "20.01") == 3

    import entities.bill.business.service as bill_mod

    scc_by_id = {1: "10.01", 2: "20.01"}
    svc = bill_mod.BillService.__new__(bill_mod.BillService)
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.return_value = {"id": 7, "worksheet_name": "DETAILS"}
    svc._drive_repo = MagicMock()
    svc._drive_repo.read_by_id.return_value = SimpleNamespace(drive_id="drv")
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(name="Acme")
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        number=scc_by_id[int(id)]
    )

    def li(n, scc_id):
        return SimpleNamespace(
            id=n,
            public_id=f"new-{n}",
            sub_cost_code_id=scc_id,
            description=f"line {n}",
            price=Decimal("10"),
            amount=Decimal("10"),
        )

    bill = SimpleNamespace(public_id="b-tie", vendor_id=1, bill_date="2026-10-01", bill_number="B1")
    line_items = [li(n, sid) for n, sid in enumerate(line_item_scc_ids, start=1)]

    ms_outbox, *patches = _workbook_patches(bill_mod, sheet)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, project_id=5)

    return result, ms_outbox, sheet


@pytest.mark.parametrize(
    "line_item_scc_ids",
    [[2, 1], [1, 2]],
    ids=["build_20_then_10", "build_10_then_20"],
)
def test_equal_row_index_tie_enqueue_preserves_after_data_section(line_item_scc_ids):
    """Equal insertion_row ties: fallback 10.01 vs after-data 20.01 at row 4.

    Enqueue must order by section start ASC so 20.01 (after-data) drains last;
    only 10.01 may remain misplaced by its header-only fallback rule.
    """
    result, ms_outbox, sheet = _run_bill_tie_sync(line_item_scc_ids)

    assert result["errors"] == []
    assert ms_outbox.enqueue_excel_insert.call_count == 2
    applied = _apply_enqueued_inserts(sheet, ms_outbox)
    _assert_scc_new_rows_in_section(applied, "20.01")


def test_find_first_row_for_subcostcode_matches_insertion_normalisation():
    sheet = [
        ["", "", "SCC"] + [""] * 23,
        _section("10.01"),
        ["", "10", "10.01"] + [""] * 23,
        _data("20.01", "old"),
    ]
    assert find_first_row_for_subcostcode(sheet, "10.01") == 2
    assert find_first_row_for_subcostcode(sheet, "10.010") == 2
    assert find_first_row_for_subcostcode(sheet, "99.99") is None
