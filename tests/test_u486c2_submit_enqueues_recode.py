"""U-486 Phase C2 — enqueue QBO recode on expense review submit (no DB)."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService
from entities.review.business.service import ReviewService

LIVE_FIRST_STATUS_ID = 1


def _first_status():
    return SimpleNamespace(id=LIVE_FIRST_STATUS_ID, name="Submitted", sort_order=10)


def _submitted_review(*, expense_id=55, review_status_id=LIVE_FIRST_STATUS_ID):
    return SimpleNamespace(
        id=101,
        review_status_id=review_status_id,
        user_id=17,
        expense_id=expense_id,
        bill_id=None,
        contract_labor_id=None,
        status_is_final=False,
        status_is_declined=False,
    )


def _review_service_with_stubbed_create(review):
    svc = ReviewService()
    svc.repo = MagicMock()
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_first_status.return_value = _first_status()
    svc.repo.create.return_value = review
    return svc


def _coding_state_item(
    *,
    public_id="ci-pub-1",
    status="pending",
    suggested_project_id=10,
    suggested_sub_cost_code_id=20,
):
    return {
        "public_id": public_id,
        "status": status,
        "confidence": None,
        "suggested_project_id": suggested_project_id,
        "suggested_sub_cost_code_id": suggested_sub_cost_code_id,
        "flag_reason": None,
    }


@contextmanager
def _mock_dominant(*, project_id, sub_cost_code_id, description=None, mixed=False):
    with patch.object(
        ExpenseService,
        "_resolve_dominant_expense_coding_for_submit",
        return_value={
            "project_id": project_id,
            "sub_cost_code_id": sub_cost_code_id,
            "description": description,
            "confirmed_mixed_codes": mixed,
        },
    ):
        yield


# ---------------------------------------------------------------------------
# 1 — initial submit calls confirm() with line item project + sub cost code
# ---------------------------------------------------------------------------


def test_initial_submit_calls_confirm_with_line_item_coding():
    expense_id = 55
    svc = ExpenseService()
    state = _coding_state_item(
        suggested_project_id=99,
        suggested_sub_cost_code_id=88,
    )

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={expense_id: [state]},
    ), _mock_dominant(project_id=77, sub_cost_code_id=481, description="Fuel at site"), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
        return_value={"status": "writes_disabled", "reason": "recode_writes_disabled"},
    ) as mock_confirm:
        svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_confirm.assert_called_once_with(
        public_id="ci-pub-1",
        project_id=77,
        sub_cost_code_id=481,
        description="Fuel at site",
        was_overridden=True,
        user_id=17,
        confirmed_mixed_codes=False,
        allow_multi_line=True,
    )


# ---------------------------------------------------------------------------
# 2 — was_overridden truth table (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "suggested_project,suggested_scc,line_project,line_scc,expected_was_overridden",
    [
        (None, None, 77, 481, True),
        (10, 20, 77, 481, True),
        (77, 481, 77, 481, False),
    ],
    ids=["no_suggestion", "human_differs", "human_matches_suggestion"],
)
def test_was_overridden_truth_table(
    suggested_project,
    suggested_scc,
    line_project,
    line_scc,
    expected_was_overridden,
):
    svc = ExpenseService()
    state = _coding_state_item(
        suggested_project_id=suggested_project,
        suggested_sub_cost_code_id=suggested_scc,
    )

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), _mock_dominant(
        project_id=line_project,
        sub_cost_code_id=line_scc,
        description="desc",
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
        return_value={"status": "writes_disabled"},
    ) as mock_confirm:
        svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    assert mock_confirm.call_args.kwargs["was_overridden"] is expected_was_overridden


# ---------------------------------------------------------------------------
# 3 — recode failure does not roll back review
# ---------------------------------------------------------------------------


def test_recode_failure_does_not_roll_back_review():
    review = _submitted_review()
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense",
    ), patch(
        "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit",
        side_effect=RuntimeError("confirm blew up"),
    ):
        result = svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    assert result is review
    svc.repo.create.assert_called_once()


# ---------------------------------------------------------------------------
# 4 — notification and recode failures are independent
# ---------------------------------------------------------------------------


def test_notification_failure_still_fires_recode():
    review = _submitted_review()
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense",
        side_effect=RuntimeError("smtp down"),
    ), patch(
        "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit",
    ) as mock_recode:
        svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    mock_recode.assert_called_once_with(expense_id=55, user_id=17)


def test_recode_failure_still_fires_notification():
    review = _submitted_review()
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense",
    ) as mock_notify, patch(
        "entities.expense.persistence.repo.ExpenseRepository.read_by_id",
        return_value=SimpleNamespace(id=55),
    ), patch(
        "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit",
        side_effect=RuntimeError("recode down"),
    ):
        svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# 5 — writes_disabled is counted, not raised
# ---------------------------------------------------------------------------


def test_writes_disabled_is_counted_not_raised():
    review = _submitted_review()
    review_svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense",
    ), patch(
        "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit",
        return_value={
            "enqueued": 0,
            "skipped": 0,
            "writes_disabled": 1,
            "invalid": 0,
            "items": [{"public_id": "ci-pub-1", "outcome": "writes_disabled"}],
        },
    ):
        result = review_svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    assert result is review


def test_enqueue_method_counts_writes_disabled():
    svc = ExpenseService()
    state = _coding_state_item()

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), _mock_dominant(project_id=77, sub_cost_code_id=481), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
        return_value={"status": "writes_disabled", "reason": "recode_writes_disabled"},
    ):
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    assert summary["writes_disabled"] == 1
    assert summary["enqueued"] == 0


# ---------------------------------------------------------------------------
# 6 — terminal coding items skipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["written", "resolved_externally"])
def test_terminal_coding_items_never_confirmed(terminal_status):
    svc = ExpenseService()
    state = _coding_state_item(status=terminal_status)

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), _mock_dominant(project_id=77, sub_cost_code_id=481, description="x"), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_confirm.assert_not_called()
    assert summary["skipped"] >= 1


# ---------------------------------------------------------------------------
# 7 — missing project or sub cost code → skipped, confirm not called
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "project_id,sub_cost_code_id",
    [(None, 481), (77, None), (0, 481), (77, 0)],
)
def test_missing_project_or_sub_cost_code_skipped(project_id, sub_cost_code_id):
    svc = ExpenseService()
    state = _coding_state_item()

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), _mock_dominant(
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        description="desc",
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_confirm.assert_not_called()
    assert summary["skipped"] >= 1


# ---------------------------------------------------------------------------
# 8 — no coding items → clean skip
# ---------------------------------------------------------------------------


def test_no_coding_items_clean_skip():
    svc = ExpenseService()

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={},
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_confirm.assert_not_called()
    assert summary.get("skipped", 0) >= 1 or summary.get("no_coding_items") is True


# ---------------------------------------------------------------------------
# 9 — not fired on non-initial submit
# ---------------------------------------------------------------------------


def test_non_initial_submit_does_not_enqueue_recode():
    review = _submitted_review(review_status_id=2)
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit",
    ) as mock_recode:
        svc.create(
            review_status_id=2,
            user_id=17,
            expense_id=55,
        )

    mock_recode.assert_not_called()
