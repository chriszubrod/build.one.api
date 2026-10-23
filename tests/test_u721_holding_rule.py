"""U-721a — holding SCC 00.02 must never reach SharePoint DETAILS enqueue (single enforcement point)."""
from __future__ import annotations

import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.sharepoint.excel.workbook_sections import (
    SectionGroup,
    enqueue_section_inserts,
    sub_cost_code_number_matches_holding,
)

PROJECT_ID = 5
DRIVE_ID = "drv"
ITEM_ID = "itm"
WORKSHEET = "DETAILS"

HOLDING_SCC_ID = 9001
REAL_SCC_ID = 9002
HOLDING_NUMBERS = frozenset({"00.02"})


def _is_holding(scc_id):
    return scc_id == HOLDING_SCC_ID


def _scc_read(scc_id=None, *, id=None):
    raw = id if id is not None else scc_id
    key = int(raw) if raw is not None and str(raw).lstrip("-").isdigit() else raw
    if key == HOLDING_SCC_ID:
        return SimpleNamespace(number="00.02")
    if key == REAL_SCC_ID:
        return SimpleNamespace(number="10.01")
    if key == 903:
        return SimpleNamespace(number="0.02")
    return SimpleNamespace(number="10.01")


def _wire_scc(svc):
    svc.sub_cost_code_service.is_holding_code = MagicMock(side_effect=_is_holding)
    svc.sub_cost_code_service.read_by_id = MagicMock(side_effect=_scc_read)
    svc.sub_cost_code_service.holding_sub_cost_code_numbers = MagicMock(
        return_value=HOLDING_NUMBERS
    )


def _workbook_patches(mod, sheet, ms_outbox=None):
    if ms_outbox is None:
        ms_outbox = MagicMock()
        ms_outbox.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
        ms_outbox.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    driveitem_repo = MagicMock()
    driveitem_repo.read_all.return_value = [
        SimpleNamespace(id=7, ms_drive_id=3, item_id=ITEM_ID)
    ]
    patches = (
        patch.object(mod, "MsDriveItemRepository", return_value=driveitem_repo),
        patch.object(mod, "create_workbook_session", return_value="sess"),
        patch.object(mod, "close_workbook_session"),
        patch.object(
            mod,
            "get_excel_used_range_values",
            return_value={
                "status_code": 200,
                "range": {"values": [list(r) for r in sheet]},
            },
        ),
        patch("integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox),
    )
    return ms_outbox, patches


def _section(scc: str) -> list:
    return ["", scc.split(".")[0] if "." in scc else scc[:2], scc] + [""] * 23


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


def _all_enqueued_values(ms_outbox: MagicMock) -> list[list]:
    rows = []
    for method in ("enqueue_excel_insert", "enqueue_excel_append"):
        mock = getattr(ms_outbox, method)
        for call in mock.call_args_list:
            rows.extend(call.kwargs.get("values") or [])
    return rows


def _assert_no_holding_in_outbox(ms_outbox: MagicMock) -> None:
    for row in _all_enqueued_values(ms_outbox):
        assert not sub_cost_code_number_matches_holding(
            row[2] if len(row) > 2 else None, HOLDING_NUMBERS
        )


def _bill_line(n, scc_id):
    return SimpleNamespace(
        id=n,
        public_id=f"li-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        sub_cost_code_id=scc_id,
        description=f"line {n}",
        price=Decimal("10.50"),
        amount=None,
    )


def _expense_line(n, scc_id):
    return SimpleNamespace(
        id=n,
        public_id=f"eli-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        sub_cost_code_id=scc_id,
        project_id=PROJECT_ID,
        description=f"exp {n}",
        price=Decimal("40.00"),
        amount=Decimal("35.00"),
    )


def _credit_line(n, scc_id):
    return SimpleNamespace(
        id=n,
        public_id=f"cli-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        sub_cost_code_id=scc_id,
        description=f"cr {n}",
        amount=Decimal("25.75"),
    )


def _bill_service(mod):
    svc = mod.BillService.__new__(mod.BillService)
    svc._project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc._drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Acme"))
    )
    svc.sub_cost_code_service = MagicMock()
    _wire_scc(svc)
    return svc


def _expense_service(mod):
    svc = mod.ExpenseService.__new__(mod.ExpenseService)
    svc._project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc._drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Ramp"))
    )
    svc.sub_cost_code_service = MagicMock()
    _wire_scc(svc)
    return svc


def _credit_service(mod):
    svc = mod.BillCreditCompleteService.__new__(mod.BillCreditCompleteService)
    svc.project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc.drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Acme"))
    )
    svc.sub_cost_code_service = MagicMock()
    _wire_scc(svc)
    return svc


def test_holding_sub_cost_code_numbers_from_ids():
    from entities.sub_cost_code.business.service import SubCostCodeService

    repo = MagicMock()
    repo.read_holding_sub_cost_code_ids.return_value = [42]
    repo.read_by_id.return_value = SimpleNamespace(number="00.02")
    svc = SubCostCodeService(repo=repo)
    assert svc.holding_sub_cost_code_numbers() == frozenset({"00.02"})
    repo.read_holding_sub_cost_code_ids.assert_called_once()
    repo.read_by_id.assert_called_once_with(42)
    assert svc.holding_sub_cost_code_numbers() == frozenset({"00.02"})
    repo.read_by_id.assert_called_once()


@pytest.mark.parametrize(
    "path_factory",
    [
        "bill_single",
        "bill_batch",
        "expense_single",
        "expense_batch",
        "credit_batch",
    ],
)
def test_five_paths_hold_00_02_not_enqueued(path_factory, caplog):
    caplog.set_level(logging.WARNING)
    sheet = _std_sheet()
    holding = _bill_line(1, HOLDING_SCC_ID)
    real = _bill_line(2, REAL_SCC_ID)
    expect_shared_warning = path_factory in (
        "bill_single",
        "bill_batch",
        "credit_batch",
    )

    if path_factory == "bill_single":
        import entities.bill.business.service as mod

        ms, patches = _workbook_patches(mod, sheet)
        svc = _bill_service(mod)
        bill = SimpleNamespace(
            public_id="11111111-1111-4111-8111-111111111101",
            vendor_id=1,
            bill_date="2026-10-01",
            bill_number="B-100",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = svc.sync_to_excel_workbook(bill, [holding, real], PROJECT_ID)
        assert result["synced_count"] == 1

    elif path_factory == "bill_batch":
        import entities.bill.business.service as mod

        ms, patches = _workbook_patches(mod, sheet)
        svc = _bill_service(mod)
        bill = SimpleNamespace(
            public_id="11111111-1111-4111-8111-111111111101",
            vendor_id=1,
            bill_date="2026-10-01",
            bill_number="B-100",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = svc.sync_bills_batch_to_excel([(bill, [holding, real])], PROJECT_ID)
        assert result["synced_count"] == 1

    elif path_factory == "expense_single":
        import entities.expense.business.service as mod

        ms, patches = _workbook_patches(mod, sheet)
        svc = _expense_service(mod)
        expense = SimpleNamespace(
            public_id="22222222-2222-4222-8222-222222222202",
            vendor_id=1,
            expense_date="2026-10-03",
            reference_number="REF-9",
            is_credit=False,
        )
        el_h = _expense_line(1, HOLDING_SCC_ID)
        el_r = _expense_line(2, REAL_SCC_ID)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = svc.sync_to_excel_workbook(expense, [el_h, el_r], PROJECT_ID)
        assert result["synced_count"] == 1
        assert result["deferred_holding_count"] == 1

    elif path_factory == "expense_batch":
        import entities.expense.business.service as mod

        ms, patches = _workbook_patches(mod, sheet)
        svc = _expense_service(mod)
        expense = SimpleNamespace(
            public_id="22222222-2222-4222-8222-222222222202",
            vendor_id=1,
            expense_date="2026-10-03",
            reference_number="REF-9",
            is_credit=False,
            id=1,
        )
        el_h = _expense_line(1, HOLDING_SCC_ID)
        el_r = _expense_line(2, REAL_SCC_ID)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = svc.sync_expenses_batch_to_excel([(expense, [el_h, el_r])], PROJECT_ID)
        assert result["synced_count"] == 1
        assert result["deferred_holding_count"] == 1

    else:
        import entities.bill_credit.business.complete_service as mod

        ms, patches = _workbook_patches(mod, sheet)
        svc = _credit_service(mod)
        credit = SimpleNamespace(
            public_id="33333333-3333-4333-8333-333333333303",
            vendor_id=1,
            credit_date="2026-10-02",
            credit_number="C-50",
        )
        cl_h = _credit_line(1, HOLDING_SCC_ID)
        cl_r = _credit_line(2, REAL_SCC_ID)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = svc.sync_bill_credits_batch_to_excel([(credit, [cl_h, cl_r])], PROJECT_ID)
        assert result["synced_count"] == 1

    _assert_no_holding_in_outbox(ms)
    if expect_shared_warning:
        assert "ms.excel.holding_rows_held" in caplog.text
        assert "Acme" not in caplog.text
    else:
        assert "ms.excel.holding_rows_held" not in caplog.text


def test_bill_batch_holds_zero_padded_alias_in_column_c(caplog):
    """Column C formatted as 0.02 still matches holding 00.02."""
    caplog.set_level(logging.WARNING)
    import entities.bill.business.service as mod

    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    svc.sub_cost_code_service.read_by_id = MagicMock(
        side_effect=lambda id=None, **kw: SimpleNamespace(number="0.02")
        if str(id) == str(903)
        else _scc_read(id=id)
    )
    bill = SimpleNamespace(
        public_id="11111111-1111-4111-8111-111111111101",
        vendor_id=1,
        bill_date="2026-10-01",
        bill_number="B-100",
    )
    lines = [_bill_line(1, 903), _bill_line(2, REAL_SCC_ID)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, lines)], PROJECT_ID)
    assert result["synced_count"] == 1
    _assert_no_holding_in_outbox(ms)
