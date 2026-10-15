"""U-632 — shared helpers: detect suspense/holding SCC on document line items."""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from entities.sub_cost_code.business.service import SubCostCodeService
from shared.lifecycle.completion_gate import HoldingCodeRefused


def sub_cost_code_ids_from_lines(lines: Sequence[object]) -> list[Optional[int]]:
    return [getattr(li, "sub_cost_code_id", None) for li in lines]


def any_holding_sub_cost_code(
    sub_cost_code_ids: Iterable[Optional[int]],
    *,
    scc_svc: Optional[SubCostCodeService] = None,
) -> bool:
    svc = scc_svc or SubCostCodeService()
    return any(svc.is_holding_code(sid) for sid in sub_cost_code_ids)


def assert_no_holding_sub_cost_codes(
    sub_cost_code_ids: Iterable[Optional[int]],
    *,
    message: str,
    scc_svc: Optional[SubCostCodeService] = None,
) -> None:
    if any_holding_sub_cost_code(sub_cost_code_ids, scc_svc=scc_svc):
        raise HoldingCodeRefused(message)


def assert_no_holding_on_lines(
    lines: Sequence[object],
    *,
    message: str,
    scc_svc: Optional[SubCostCodeService] = None,
) -> None:
    assert_no_holding_sub_cost_codes(
        sub_cost_code_ids_from_lines(lines),
        message=message,
        scc_svc=scc_svc,
    )


def contract_labor_entry_has_holding_code(
    entry,
    *,
    line_items: Sequence[object],
    scc_svc: Optional[SubCostCodeService] = None,
) -> bool:
    """True when CL header or any child line is on a holding SCC."""
    svc = scc_svc or SubCostCodeService()
    if svc.is_holding_code(getattr(entry, "sub_cost_code_id", None)):
        return True
    return any_holding_sub_cost_code(sub_cost_code_ids_from_lines(line_items), scc_svc=svc)


def parent_has_holding_line(parent_type: str, parent_id: int) -> bool:
    """True when any line on Bill / Expense / BillCredit is on a holding SCC."""
    if parent_type == "bill":
        from entities.bill_line_item.business.service import BillLineItemService

        lines = BillLineItemService().read_by_bill_id(bill_id=parent_id)
    elif parent_type == "expense":
        from entities.expense_line_item.business.service import ExpenseLineItemService

        lines = ExpenseLineItemService().read_by_expense_id(expense_id=parent_id)
    elif parent_type == "bill_credit":
        from entities.bill_credit_line_item.business.service import (
            BillCreditLineItemService,
        )

        lines = BillCreditLineItemService().read_by_bill_credit_id(
            bill_credit_id=parent_id
        )
    elif parent_type == "contract_labor":
        from entities.contract_labor.persistence.line_item_repo import (
            ContractLaborLineItemRepository,
        )
        from entities.contract_labor.persistence.repo import ContractLaborRepository

        entry = ContractLaborRepository().read_by_id(parent_id)
        if entry is None:
            return False
        cl_lines = ContractLaborLineItemRepository().read_by_contract_labor_id(
            contract_labor_id=parent_id
        )
        return contract_labor_entry_has_holding_code(entry, line_items=cl_lines)
    elif parent_type == "invoice":
        from entities.invoice.business.service import InvoiceService

        # Same predicate invoice completion uses, so approval and completion agree.
        return InvoiceService().invoice_has_holding_line(parent_id)
    else:
        return False
    return any_holding_sub_cost_code(sub_cost_code_ids_from_lines(lines))


def refuse_if_parent_has_holding_line(
    parent_type: str,
    parent_id: int,
    *,
    message: str,
) -> None:
    if parent_has_holding_line(parent_type, parent_id):
        raise HoldingCodeRefused(message)
