"""U-652a — lazy CodingSuggestion seed on expense review submit when row is missing."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService

_GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
_STATE = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids"
_CONFIRM = "entities.coding_suggestion.business.service.CodingSuggestionService.confirm"
_UPSERT = "entities.coding_suggestion.business.service.CodingSuggestionService.upsert_from_queue"
_COUNT = "entities.coding_suggestion.business.service.CodingSuggestionService._count_uncategorized_purchase_lines"
_PURCHASE_REPO = "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository.read_by_qbo_id_and_realm_id"
_EXPENSE_READ = "entities.expense.business.service.ExpenseService.read_by_id"


def _expense(*, expense_id=55, qbo_id="P-100", realm_id="realm-1"):
    return SimpleNamespace(id=expense_id, qbo_id=qbo_id, realm_id=realm_id)


def _purchase(*, staging_id=900, qbo_id="P-100", realm_id="realm-1", vendor="V-42"):
    return SimpleNamespace(
        id=staging_id,
        qbo_id=qbo_id,
        realm_id=realm_id,
        entity_ref_value=vendor,
    )


def _coding_state_item(
    *,
    public_id="ci-pub-new",
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
def _mock_dominant(*, project_id=77, sub_cost_code_id=481, description="Fuel", mixed=False):
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


def _state_side_effect_after_upsert(expense_id, state_item):
    calls = {"n": 0}

    def _read(expense_ids):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}
        if expense_id in expense_ids:
            return {expense_id: [state_item]}
        return {}

    return _read


@pytest.mark.parametrize("uncategorized_count", [1, 2])
def test_no_suggestion_uncategorized_purchase_upserts_then_confirms(uncategorized_count):
    expense_id = 55
    svc = ExpenseService()
    state = _coding_state_item()

    with patch(_GATE, return_value=None), patch(
        _EXPENSE_READ, return_value=_expense(expense_id=expense_id)
    ), patch(
        _PURCHASE_REPO, return_value=_purchase()
    ), patch(_COUNT, return_value=uncategorized_count), patch(
        _STATE, side_effect=_state_side_effect_after_upsert(expense_id, state)
    ), patch(_UPSERT) as mock_upsert, _mock_dominant(mixed=uncategorized_count > 1), patch(
        _CONFIRM, return_value={"status": "enqueued", "enqueued": True}
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_upsert.assert_called_once_with(
        qbo_purchase_id=900,
        qbo_purchase_qbo_id="P-100",
        realm_id="realm-1",
        vendor_qbo_id="V-42",
        expense_id=expense_id,
        created_by_user_id=17,
    )
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args.kwargs["allow_multi_line"] is True
    assert summary["enqueued"] == 1
    assert summary["items"][0].get("seeded_suggestion") is True


def test_no_suggestion_zero_uncategorized_lines_no_upsert():
    expense_id = 55
    svc = ExpenseService()

    with patch(_GATE, return_value=None), patch(
        _EXPENSE_READ, return_value=_expense(expense_id=expense_id)
    ), patch(_PURCHASE_REPO, return_value=_purchase()), patch(_COUNT, return_value=0), patch(
        _STATE, return_value={}
    ), patch(_UPSERT) as mock_upsert, patch(_CONFIRM) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_upsert.assert_not_called()
    mock_confirm.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["items"][0].get("reason") == "no_coding_items"


@pytest.mark.parametrize(
    "expense_fixture",
    [
        SimpleNamespace(id=55, qbo_id=None, realm_id="realm-1"),
        SimpleNamespace(id=55, qbo_id="P-100", realm_id=None),
        None,
    ],
    ids=["missing_qbo_id", "missing_realm_id", "expense_not_found"],
)
def test_no_suggestion_missing_qbo_identity_or_purchase_no_upsert(expense_fixture):
    svc = ExpenseService()

    with patch(_GATE, return_value=None), patch(
        _EXPENSE_READ, return_value=expense_fixture
    ), patch(_PURCHASE_REPO, return_value=None), patch(_STATE, return_value={}), patch(
        _UPSERT
    ) as mock_upsert, patch(_CONFIRM) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_upsert.assert_not_called()
    mock_confirm.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["items"][0].get("reason") == "no_coding_items"


def test_no_suggestion_missing_staging_purchase_no_upsert():
    svc = ExpenseService()

    with patch(_GATE, return_value=None), patch(
        _EXPENSE_READ, return_value=_expense()
    ), patch(_PURCHASE_REPO, return_value=None), patch(_STATE, return_value={}), patch(
        _UPSERT
    ) as mock_upsert:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_upsert.assert_not_called()
    assert summary["items"][0].get("reason") == "no_coding_items"


def test_existing_suggestion_no_upsert():
    expense_id = 55
    svc = ExpenseService()
    state = _coding_state_item(public_id="ci-existing")

    with patch(_GATE, return_value=None), patch(
        _STATE, return_value={expense_id: [state]}
    ), patch(_UPSERT) as mock_upsert, _mock_dominant(), patch(
        _CONFIRM, return_value={"status": "enqueued", "enqueued": True}
    ):
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_upsert.assert_not_called()
    assert summary["enqueued"] == 1
    assert "seeded_suggestion" not in summary["items"][0]


def test_upsert_raises_swallowed_no_enqueue():
    expense_id = 55
    svc = ExpenseService()

    with patch(_GATE, return_value=None), patch(
        _EXPENSE_READ, return_value=_expense(expense_id=expense_id)
    ), patch(_PURCHASE_REPO, return_value=_purchase()), patch(_COUNT, return_value=1), patch(
        _STATE, return_value={}
    ), patch(_UPSERT, side_effect=RuntimeError("db down")), patch(_CONFIRM) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_confirm.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["items"][0].get("reason") == "no_coding_items"


def test_gates_off_does_not_upsert_even_with_uncategorized_purchase():
    expense_id = 55
    svc = ExpenseService()

    with patch(_GATE, return_value="recode_writes_disabled"), patch(
        _EXPENSE_READ, return_value=_expense(expense_id=expense_id)
    ), patch(_PURCHASE_REPO, return_value=_purchase()), patch(_COUNT, return_value=2), patch(
        _STATE, return_value={}
    ), patch(_UPSERT) as mock_upsert, patch(_CONFIRM) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_upsert.assert_not_called()
    mock_confirm.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["items"][0].get("reason") == "no_coding_items"
