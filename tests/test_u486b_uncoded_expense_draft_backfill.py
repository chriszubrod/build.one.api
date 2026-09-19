"""U-486 Phase B — backfill genuinely-uncoded completed expenses to draft.

Pure-logic / source pins — no live DB. The detection predicate must require
ItemRefValue IS NULL so stale 58999 labels on already-coded lines are never
re-opened.
"""

import inspect
import re
from unittest.mock import MagicMock, patch

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"

DETECTION_SPROC = "ReadUncodedCompletedExpenseCandidates"
MARK_SPROC = "MarkExpenseDraftForCoding"

PRE_U486B_SPROC_NAMES = frozenset(
    {
        "CreateExpense",
        "ReadExpenses",
        "ReadExpenseById",
        "ReadExpenseByQboIdAndRealmId",
        "ReadExpenseQboIdsByRealmId",
        "ReadExpenseByPublicId",
        "ReadExpenseByReferenceNumberAndVendorId",
        "UpdateExpenseById",
        "FinalizeExpenseById",
        "DeleteExpenseCascadeById",
        "DeleteExpenseById",
        "ReadExpensesPaginated",
        "CountExpenses",
        "SetExpenseQboIdentity",
        "TransitionExpenseStatus",
    }
)


def _sproc_names_in_file(path) -> set[str]:
    text = path.read_text()
    return set(
        re.findall(
            r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?(\w+)",
            text,
            re.IGNORECASE,
        )
    )


def _status_in_where_completed_only(body: str) -> bool:
    """True when completed appears as a transition-from guard on Expense status."""
    return bool(
        re.search(
            r"\[Status\]\s*=\s*N'completed'",
            body,
            re.IGNORECASE,
        )
    )


# ---------------------------------------------------------------------------
# 1 — detection requires ItemRefValue IS NULL (not account label alone)
# ---------------------------------------------------------------------------


def test_detection_sproc_requires_item_ref_value_null():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, DETECTION_SPROC))
    assert re.search(
        r"ItemRefValue\]\s+IS\s+NULL",
        body,
        re.IGNORECASE,
    ), (
        "ReadUncodedCompletedExpenseCandidates must require ItemRefValue IS NULL "
        "so stale 58999 labels on coded lines are not re-opened"
    )


def test_detection_sproc_does_not_select_on_account_label_alone():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, DETECTION_SPROC))
    assert re.search(
        r"AccountRefName\]\s+LIKE\s+N'%NEED TO CATEGORIZE%'",
        body,
        re.IGNORECASE,
    )
    assert re.search(r"ItemRefValue\]\s+IS\s+NULL", body, re.IGNORECASE)
    # The completed-expense filter must AND both predicates — not OR, not label-only.
    where_match = re.search(
        r"WHERE\s+e\.\[Status\]\s*=\s*N'completed'(.*?)\n\s*\)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert where_match is not None, "expected Ranked CTE WHERE on completed expenses"
    where_chunk = where_match.group(1)
    assert "AccountRefName" in where_chunk and "ItemRefValue" in where_chunk, (
        "detection must AND account placeholder label with ItemRefValue IS NULL"
    )
    assert " OR " not in where_chunk.upper(), (
        "must not OR the label predicate without ItemRefValue IS NULL"
    )


# ---------------------------------------------------------------------------
# 2 — flip transitions ONLY from completed
# ---------------------------------------------------------------------------


def test_mark_sproc_only_transitions_from_completed():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, MARK_SPROC))
    assert _status_in_where_completed_only(body), (
        "MarkExpenseDraftForCoding must guard UPDATE with Status = completed"
    )
    forbidden = ("draft", "submitted", "in_review", "approved", "declined")
    for status in forbidden:
        assert not re.search(
            rf"\[Status\]\s+IN\s*\([^)]*N'{status}'",
            body,
            re.IGNORECASE,
        ), f"MarkExpenseDraftForCoding must NOT allow transition from {status!r}"


# ---------------------------------------------------------------------------
# 3 — provenance: StatusOrigin = coding_backfill
# ---------------------------------------------------------------------------


def test_mark_sproc_stamps_coding_backfill_origin():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, MARK_SPROC))
    assert re.search(
        r"StatusOrigin\]\s*=\s*N'coding_backfill'",
        body,
        re.IGNORECASE,
    ), "backfilled drafts must be distinguishable via StatusOrigin = coding_backfill"


# ---------------------------------------------------------------------------
# 4 — three-hop join; no qbo PK aliased as ExpenseId
# ---------------------------------------------------------------------------


def test_detection_sproc_uses_purchase_line_expense_line_item_hop():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, DETECTION_SPROC))
    assert "PurchaseLineExpenseLineItem" in body
    assert not re.search(
        r"\bAS\s+ExpenseId\b",
        body,
        re.IGNORECASE,
    ), "never alias a qbo PK as ExpenseId"


# ---------------------------------------------------------------------------
# 5 — service defaults to dry-run
# ---------------------------------------------------------------------------


def test_backfill_uncoded_to_draft_defaults_to_dry_run_without_marking():
    from entities.expense.business.service import ExpenseService

    candidates = [
        {
            "id": 1,
            "public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "status": "completed",
            "qbo_purchase_line_id": 99,
            "coding_item_public_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }
    ]
    svc = ExpenseService()
    svc.repo = MagicMock()
    svc.repo.read_uncoded_completed_candidates.return_value = candidates

    result = svc.backfill_uncoded_to_draft()

    assert result == {"candidates": 1, "items": candidates}
    svc.repo.mark_draft_for_coding.assert_not_called()


def test_backfill_uncoded_to_draft_apply_calls_mark_for_each_candidate():
    from entities.expense.business.service import ExpenseService

    candidates = [
        {
            "id": 1,
            "public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "status": "completed",
            "qbo_purchase_line_id": 99,
            "coding_item_public_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
        {
            "id": 2,
            "public_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "status": "completed",
            "qbo_purchase_line_id": 100,
            "coding_item_public_id": None,
        },
    ]
    svc = ExpenseService()
    svc.repo = MagicMock()
    svc.repo.read_uncoded_completed_candidates.return_value = candidates
    svc.repo.mark_draft_for_coding.side_effect = [MagicMock(), None]

    result = svc.backfill_uncoded_to_draft(dry_run=False)

    assert result == {"marked": 1, "skipped": 1}
    assert svc.repo.mark_draft_for_coding.call_count == 2


# ---------------------------------------------------------------------------
# 6 — admin endpoint: drain secret + apply defaults false
# ---------------------------------------------------------------------------


def test_admin_backfill_uncoded_to_draft_route_uses_drain_secret():
    import shared.api.admin as admin_module

    src = inspect.getsource(admin_module.backfill_expense_uncoded_to_draft_router)
    assert "Depends(_require_drain_secret)" in src


def test_admin_backfill_uncoded_to_draft_defaults_apply_to_false():
    import shared.api.admin as admin_module

    src = inspect.getsource(admin_module.backfill_expense_uncoded_to_draft_router)
    assert "apply" in src
    assert re.search(
        r"apply\s*:\s*bool\s*=\s*Query\s*\(\s*default\s*=\s*False",
        src,
    )


# ---------------------------------------------------------------------------
# 7 — SET NOCOUNT ON + GO-terminated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [DETECTION_SPROC, MARK_SPROC])
def test_new_sproc_starts_with_set_nocount_on(name):
    body = sproc_body(EXPENSE_SQL, name)
    after_as = body.split("AS", 1)[1]
    assert "SET NOCOUNT ON" in after_as.split("BEGIN", 1)[1][:80]


def test_new_sprocs_are_go_terminated_in_file():
    text = EXPENSE_SQL.read_text()
    for name in (DETECTION_SPROC, MARK_SPROC):
        match = re.search(
            rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{name}\b.*?^GO\s*$",
            text,
            re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )
        assert match is not None, f"{name} must be GO-terminated in the SQL file"


# ---------------------------------------------------------------------------
# 8 — no existing sproc bodies modified (only two new names added)
# ---------------------------------------------------------------------------


def test_expense_sql_only_adds_two_new_sprocs():
    names = _sproc_names_in_file(EXPENSE_SQL)
    assert PRE_U486B_SPROC_NAMES.issubset(names)
    added = names - PRE_U486B_SPROC_NAMES
    assert added == {DETECTION_SPROC, MARK_SPROC}
