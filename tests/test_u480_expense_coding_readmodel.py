"""U-480 — Expense read model carries coding state (U-477 Phase 1).

Pure-logic / source pins — no live DB. The ledger and coding cockpit must agree
on whether an expense still needs coding; `needs_coding` derives from
ExpenseCodingItem.Status, never from ExpenseLineItem.SubCostCodeId.
"""

import asyncio
import inspect
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params, strip_sql_comments

EXPENSE_CODING_SQL = (
    REPO_ROOT / "entities/expense_coding_item/sql/dbo.expense_coding_item.sql"
)

# Full vocabulary — a new DB status must update this set or tests fail loudly.
EXPENSE_CODING_STATUSES = frozenset(
    {
        "pending",
        "suggested",
        "flagged",
        "confirmed",
        "enqueued",
        "written",
        "changed_in_qbo",
        "error",
    }
)
EXPENSE_CODING_TERMINAL_STATUSES = frozenset({"written"})
EXPENSE_CODING_OPEN_STATUSES = EXPENSE_CODING_STATUSES - EXPENSE_CODING_TERMINAL_STATUSES


def _coding_item(**over):
    base = {
        "public_id": "11111111-1111-1111-1111-111111111111",
        "status": "pending",
        "confidence": Decimal("0.85"),
        "suggested_project_id": 10,
        "suggested_sub_cost_code_id": 20,
        "flag_reason": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1 — needs_coding follows ExpenseCodingItem.Status, not SubCostCodeId
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(EXPENSE_CODING_OPEN_STATUSES))
def test_needs_coding_is_true_for_each_non_terminal_status(status):
    from entities.expense.api.router import build_expense_coding_block

    block = build_expense_coding_block([_coding_item(status=status)])
    assert block["needs_coding"] is True
    assert block["open_items"] == 1


@pytest.mark.parametrize("status", sorted(EXPENSE_CODING_TERMINAL_STATUSES))
def test_needs_coding_is_false_for_terminal_status(status):
    from entities.expense.api.router import build_expense_coding_block

    block = build_expense_coding_block([_coding_item(status=status)])
    assert block["needs_coding"] is False
    assert block["open_items"] == 0


def test_status_vocabulary_is_exhaustive():
    """Every known status is classified open or terminal — no silent default."""
    assert EXPENSE_CODING_OPEN_STATUSES | EXPENSE_CODING_TERMINAL_STATUSES == EXPENSE_CODING_STATUSES
    assert EXPENSE_CODING_OPEN_STATUSES.isdisjoint(EXPENSE_CODING_TERMINAL_STATUSES)


def test_sub_cost_code_on_the_line_does_not_clear_needs_coding():
    """Post-recode / pre-pull window: line may already have SubCostCodeId while
    the coding item is still open. A SubCostCodeId-derived flag would lie."""
    from entities.expense.api.router import build_expense_coding_block

    # The line already healed locally; coding item still `confirmed` until pull.
    items = [_coding_item(status="confirmed", suggested_sub_cost_code_id=999)]
    block = build_expense_coding_block(items)
    assert block["needs_coding"] is True, (
        "needs_coding must derive from coding item status, not from whether the "
        "expense line already carries a SubCostCodeId"
    )
    src = inspect.getsource(build_expense_coding_block)
    assert 'item.get("status")' in src
    assert "EXPENSE_CODING_TERMINAL_STATUSES" in src


# ---------------------------------------------------------------------------
# 2 — empty block shape
# ---------------------------------------------------------------------------


def test_expense_with_no_coding_items_gets_empty_block_with_key():
    from entities.expense.api.router import build_expense_coding_block

    block = build_expense_coding_block([])
    assert block == {"needs_coding": False, "open_items": 0, "items": []}


# ---------------------------------------------------------------------------
# 3 — batch, not N+1
# ---------------------------------------------------------------------------


def test_list_handler_calls_coding_repo_exactly_once_for_multi_row_page():
    from entities.expense.api.router import get_expenses_router

    rows = [
        SimpleNamespace(id=101, is_draft=False, status="completed", to_dict=lambda: {"public_id": "e-101"}),
        SimpleNamespace(id=202, is_draft=False, status="completed", to_dict=lambda: {"public_id": "e-202"}),
        SimpleNamespace(id=303, is_draft=False, status="completed", to_dict=lambda: {"public_id": "e-303"}),
    ]
    service = MagicMock()
    service.read_paginated.return_value = (rows, len(rows))
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = {}
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {}

    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        asyncio.run(get_expenses_router(
            page=1,
            page_size=50,
            search=None,
            vendor_id=None,
            is_draft=None,
            start_date=None,
            end_date=None,
            status=None,
            current_user={},
        ))

    coding_repo.read_state_by_expense_ids.assert_called_once_with([101, 202, 303])


# ---------------------------------------------------------------------------
# 4 — sproc source pins
# ---------------------------------------------------------------------------


def test_read_expense_coding_state_sproc_declares_actor_params():
    params = sproc_params(EXPENSE_CODING_SQL, "ReadExpenseCodingStateByExpenseIds")
    assert "@ActorUserId" in params
    assert "@ActorIsSystemAdmin" in params


def test_read_expense_coding_state_sproc_uses_user_can_access_project():
    body = strip_sql_comments(
        sproc_body(EXPENSE_CODING_SQL, "ReadExpenseCodingStateByExpenseIds")
    )
    assert "UserCanAccessProject" in body


def test_read_expense_coding_state_sproc_uses_purchase_line_expense_line_item_hop():
    body = strip_sql_comments(
        sproc_body(EXPENSE_CODING_SQL, "ReadExpenseCodingStateByExpenseIds")
    )
    assert "PurchaseLineExpenseLineItem" in body


def test_read_expense_coding_state_sproc_does_not_filter_on_the_lines_sub_cost_code():
    """/em mutation-check follow-up. The Python guard is pinned, but the SAME
    defect is reachable one layer down and invisible to it: adding
    `AND eli.[SubCostCodeId] IS NULL` to the sproc would drop already-healed
    lines from the result, so `needs_coding` would go false while the coding
    item is still open — exactly the post-recode / pre-pull lie the design
    forbids (u477 §9 non-negotiable 1). Verified surviving: the mutation left
    every other spec green.

    The sproc may SELECT the coding item's own Suggested/Confirmed SCC columns —
    those are payload, not a predicate. What it must never do is PREDICATE on
    the expense line's SubCostCodeId.
    """
    body = strip_sql_comments(
        sproc_body(EXPENSE_CODING_SQL, "ReadExpenseCodingStateByExpenseIds")
    )
    # Strip the SELECT list: everything from WHERE onward is predicate territory.
    idx = body.upper().find("WHERE")
    predicate = body[idx:] if idx != -1 else body
    assert "subcostcodeid" not in predicate.lower(), (
        "ReadExpenseCodingStateByExpenseIds must not PREDICATE on a SubCostCodeId — "
        "needs_coding derives from ExpenseCodingItem.Status only. Filtering out "
        "already-healed lines here recreates the post-recode/pre-pull lie one layer "
        "below the Python guard, where test_sub_cost_code_on_the_line_does_not_clear_"
        "needs_coding cannot see it."
    )


def test_read_expense_coding_state_sproc_starts_with_set_nocount_on():
    body = sproc_body(EXPENSE_CODING_SQL, "ReadExpenseCodingStateByExpenseIds")
    after_as = body.split("AS", 1)[1]
    assert "SET NOCOUNT ON" in after_as.split("BEGIN", 1)[1][:80]


# ---------------------------------------------------------------------------
# 5 — lifecycle keys preserved alongside coding
# ---------------------------------------------------------------------------


def test_list_response_keeps_lifecycle_keys_and_adds_coding():
    from entities.expense.api.router import get_expenses_router

    expense = SimpleNamespace(
        id=42,
        is_draft=False,
        status="completed",
        to_dict=lambda: {"public_id": "exp-42"},
    )
    service = MagicMock()
    service.read_paginated.return_value = ([expense], 1)
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = {}
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {
        42: [_coding_item(status="pending")],
    }

    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        data = asyncio.run(get_expenses_router(
            page=1,
            page_size=50,
            search=None,
            vendor_id=None,
            is_draft=None,
            start_date=None,
            end_date=None,
            status=None,
            current_user={},
        ))["data"]

    row = data[0]
    assert "status" in row
    assert "review_status" in row
    assert "review_status_kind" in row
    assert "coding" in row
    assert row["coding"]["needs_coding"] is True
