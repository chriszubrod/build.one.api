import inspect
import re

import pytest

from entities.contract_labor.business.bill_service import ContractLaborBillService

SVC = ContractLaborBillService.__new__(ContractLaborBillService)

_BY_SCC_LOOP_MARKER = "for (scc_id, billable_flag), group in by_scc.items():"


def _extract_by_scc_loop_body(source: str) -> str:
    start = source.index(_BY_SCC_LOOP_MARKER)
    lines = source[start:].splitlines(keepends=True)
    collected = [lines[0]]
    body_indent = None
    for line in lines[1:]:
        if not line.strip():
            collected.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is None:
            body_indent = indent
        if indent < body_indent:
            break
        collected.append(line)
    return "".join(collected)


def test_get_due_date_fifteenth_to_last_day_of_month():
    assert SVC.get_due_date("2026-01-15") == "2026-01-31"


def test_get_due_date_end_of_month_to_fifteenth_of_next_month():
    assert SVC.get_due_date("2026-01-31") == "2026-02-15"


def test_get_due_date_december_eom_rolls_year():
    assert SVC.get_due_date("2026-12-31") == "2027-01-15"


def test_get_due_date_mid_month_to_last_day():
    assert SVC.get_due_date("2026-03-10") == "2026-03-31"


def test_generate_invoice_number_zero_padded():
    assert SVC.generate_invoice_number("2026-01-05", "HP") == "2026.01.05.HP"


def test_generate_invoice_number_double_digit_month_day():
    assert SVC.generate_invoice_number("2026-11-20", "TB3") == "2026.11.20.TB3"


def test_structural_regression_guard_billable_predicate_uses_is_not_false():
    """STRUCTURAL regression-guard: None-safe billable flag must use `is not False`."""
    source = inspect.getsource(ContractLaborBillService.generate_bills_for_vendor)
    # Pin the EXACT None-safe grouping line — not just any "is not False" in the method.
    assert "billable_flag = li.is_billable is not False" in source
    # The broken truthiness forms must be absent (None would wrongly become non-billable).
    assert "billable_flag = li.is_billable\n" not in source
    assert "billable_flag = bool(li.is_billable)" not in source
    assert "if li.is_billable:" not in source
    assert 'if item["line_item"].is_billable:' not in source


def test_structural_regression_guard_scc_accumulators_not_total_amount_shadow():
    """STRUCTURAL regression-guard: inner SCC loop uses scc_cost/scc_price, not total_amount."""
    source = inspect.getsource(ContractLaborBillService.generate_bills_for_vendor)
    loop_body = _extract_by_scc_loop_body(source)
    assert "scc_cost" in loop_body
    assert "scc_price" in loop_body
    assert re.search(r"\btotal_amount\s*=", loop_body) is None
    assert re.search(r"\btotal_amount\s*\+=", loop_body) is None
