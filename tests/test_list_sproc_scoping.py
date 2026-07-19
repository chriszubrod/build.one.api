"""Guard: the Bill/Expense/ContractLabor list sprocs stay RBAC-scoped in their base files.

Background (U-089, 2026-07-19): the deployed repo layer passes @ActorUserId /
@ActorIsSystemAdmin to these 9 list-path sprocs. A base-file re-apply had reverted
them to their unscoped form in prod, so `EXEC` failed with SQL 8145 (param not found)
and `GET /api/v1/get/{bills,expenses,contract-labor}` 500'd — the same single-source
drift class as the U-037 TimeEntry outage.

The prod sprocs were restored and the base files were reconciled to the scoped form
(base == prod). This test keeps the base files from being silently un-scoped again:
each list sproc's body must carry the actor params AND a UserCanAccess* filter.

NOTE: this is a SCOPING guard, not a single-source guard. These sprocs are still
duplicated in several historical migrations (gap1_bill_family_*, 003_read_bill_
source_email_message_id, add_is_credit_column). Folding those into SUPERSEDED stubs +
adding a `SINGLE_SOURCE_SPROCS` row is a follow-up (see api/TODO.md).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# (sproc, base file, the UserCanAccess UDF its scoping must call)
SCOPED_LIST_SPROCS = [
    ("ReadBills", "entities/bill/sql/dbo.bill.sql", "UserCanAccessBill"),
    ("ReadBillsPaginated", "entities/bill/sql/dbo.bill.sql", "UserCanAccessBill"),
    ("CountBills", "entities/bill/sql/dbo.bill.sql", "UserCanAccessBill"),
    ("ReadExpenses", "entities/expense/sql/dbo.expense.sql", "UserCanAccessExpense"),
    ("ReadExpensesPaginated", "entities/expense/sql/dbo.expense.sql", "UserCanAccessExpense"),
    ("CountExpenses", "entities/expense/sql/dbo.expense.sql", "UserCanAccessExpense"),
    ("ReadContractLabors", "entities/contract_labor/sql/dbo.contract_labor.sql", "UserCanAccessProject"),
    ("ReadContractLaborsPaginated", "entities/contract_labor/sql/dbo.contract_labor.sql", "UserCanAccessProject"),
    ("CountContractLabors", "entities/contract_labor/sql/dbo.contract_labor.sql", "UserCanAccessProject"),
]


def _sproc_body(base_path: str, name: str) -> str:
    text = (REPO_ROOT / base_path).read_text(encoding="utf-8")
    m = re.search(rf"CREATE\s+(OR\s+ALTER\s+)?PROCEDURE\s+(dbo\.)?{name}\b", text, re.I)
    assert m, f"{name} not found in {base_path}"
    go = re.search(r"(?im)^\s*GO\s*$", text[m.start():])
    return text[m.start(): m.start() + (go.start() if go else len(text))]


@pytest.mark.parametrize("name,base_path,udf", SCOPED_LIST_SPROCS)
def test_list_sproc_carries_actor_params(name, base_path, udf):
    body = _sproc_body(base_path, name)
    assert "@ActorUserId" in body and "@ActorIsSystemAdmin" in body, (
        f"{name} in {base_path} lost its actor params — the deployed repo passes them, "
        f"so an unscoped copy 8145s at runtime (U-089)."
    )


@pytest.mark.parametrize("name,base_path,udf", SCOPED_LIST_SPROCS)
def test_list_sproc_applies_access_filter(name, base_path, udf):
    body = _sproc_body(base_path, name)
    assert udf in body, f"{name} in {base_path} must filter via dbo.{udf}(...) (U-089)."
