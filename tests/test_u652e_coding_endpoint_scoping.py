"""U-652e — flag/confirm coding routes enforce expense/project access before writes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.api.schemas import (
    ConfirmCodingSuggestionRequest,
    FlagCodingSuggestionRequest,
)
from shared.access import EntityNotAccessibleError
from shared.api.errors import ApiError, ErrorCode
from shared.authz import current_is_system_admin, current_user_id

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_PUB = "00000000-0000-0000-0000-000000000010"
SCC_PUB = "00000000-0000-0000-0000-000000000020"
USER_ID = 42

_CONFIRM_BODY = ConfirmCodingSuggestionRequest(
    project_public_id=PROJECT_PUB,
    sub_cost_code_public_id=SCC_PUB,
    description="Materials",
    was_overridden=False,
)
_FLAG_BODY = FlagCodingSuggestionRequest(reason="needs follow-up")


def _suggestion(**overrides):
    base = dict(
        expense_id=None,
        confirmed_project_id=None,
        suggested_project_id=None,
        claimed_by_user_id=USER_ID,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _expect_coding_not_found(callable_fn, detail="Expense coding item not found"):
    with pytest.raises(ApiError) as exc:
        callable_fn()
    assert exc.value.status_code == 404
    assert exc.value.detail == detail
    assert exc.value.error_code == ErrorCode.NOT_FOUND


# --- flag -------------------------------------------------------------------


def test_flag_inaccessible_expense_returns_404_without_write():
    service = MagicMock()
    service.read_by_public_id.return_value = _suggestion(expense_id=99)
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense",
        side_effect=EntityNotAccessibleError("Expense", 99),
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp:
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import flag_expense_coding_item_router

        _expect_coding_not_found(
            lambda: flag_expense_coding_item_router(PUBLIC_ID, _FLAG_BODY, _={"can_update": True})
        )
    ace.assert_called_once_with(99)
    acp.assert_not_called()
    service.record_flag.assert_not_called()


@pytest.mark.parametrize(
    "confirmed_project_id,suggested_project_id,expected_project",
    [(7, None, 7), (None, 8, 8)],
)
def test_flag_inaccessible_project_returns_404_without_write(
    confirmed_project_id, suggested_project_id, expected_project
):
    service = MagicMock()
    service.read_by_public_id.return_value = _suggestion(
        confirmed_project_id=confirmed_project_id,
        suggested_project_id=suggested_project_id,
    )
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project",
        side_effect=EntityNotAccessibleError("Project", expected_project),
    ) as acp:
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import flag_expense_coding_item_router

        _expect_coding_not_found(
            lambda: flag_expense_coding_item_router(PUBLIC_ID, _FLAG_BODY, _={"can_update": True})
        )
    ace.assert_not_called()
    acp.assert_called_once_with(expected_project)
    service.record_flag.assert_not_called()


def test_flag_unscoped_suggestion_without_project_is_allowed():
    service = MagicMock()
    item = _suggestion()
    service.read_by_public_id.return_value = item
    flagged = SimpleNamespace(public_id=PUBLIC_ID)
    service.record_flag.return_value = flagged
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID, "status": "flagged"}

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp:
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import flag_expense_coding_item_router

        out = flag_expense_coding_item_router(PUBLIC_ID, _FLAG_BODY, _={"can_update": True})

    ace.assert_not_called()
    acp.assert_called_once_with(None)
    service.record_flag.assert_called_once()
    assert out["data"]["status"] == "flagged"


def test_flag_accessible_expense_proceeds():
    service = MagicMock()
    item = _suggestion(expense_id=55)
    service.read_by_public_id.return_value = item
    service.record_flag.return_value = item
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID, "status": "flagged"}

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp:
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import flag_expense_coding_item_router

        flag_expense_coding_item_router(PUBLIC_ID, _FLAG_BODY, _={"can_update": True})

    ace.assert_called_once_with(55)
    acp.assert_not_called()
    service.record_flag.assert_called_once()


def test_flag_system_admin_bypasses_expense_gate():
    service = MagicMock()
    item = _suggestion(expense_id=99)
    service.read_by_public_id.return_value = item
    service.record_flag.return_value = item
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID}

    admin_token = current_is_system_admin.set(True)
    user_token = current_user_id.set(USER_ID)
    try:
        with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service):
            from entities.coding_suggestion.api.router import flag_expense_coding_item_router

            flag_expense_coding_item_router(PUBLIC_ID, _FLAG_BODY, _={"can_update": True})
    finally:
        current_user_id.reset(user_token)
        current_is_system_admin.reset(admin_token)

    service.record_flag.assert_called_once()


# --- confirm ----------------------------------------------------------------


def test_confirm_inaccessible_expense_returns_404_without_claim_or_confirm():
    service = MagicMock()
    service.read_by_public_id.return_value = _suggestion(expense_id=99, claimed_by_user_id=None)
    service.is_multi_line_purchase.return_value = False

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense",
        side_effect=EntityNotAccessibleError("Expense", 99),
    ), patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp, patch(
        "entities.project.business.service.ProjectService"
    ):
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

        _expect_coding_not_found(
            lambda: confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True})
        )

    acp.assert_not_called()
    service.claim.assert_not_called()
    service.confirm.assert_not_called()


def test_confirm_inaccessible_suggestion_project_returns_404_without_claim():
    service = MagicMock()
    service.read_by_public_id.return_value = _suggestion(
        suggested_project_id=8, claimed_by_user_id=None
    )
    service.is_multi_line_purchase.return_value = False

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project",
        side_effect=EntityNotAccessibleError("Project", 8),
    ) as acp, patch(
        "entities.project.business.service.ProjectService"
    ):
        uid.get.return_value = USER_ID
        from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

        _expect_coding_not_found(
            lambda: confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True})
        )

    ace.assert_not_called()
    acp.assert_called_once_with(8)
    service.claim.assert_not_called()
    service.confirm.assert_not_called()


def test_confirm_unscoped_suggestion_without_project_is_allowed():
    service = MagicMock()
    existing = _suggestion(claimed_by_user_id=USER_ID)
    service.read_by_public_id.side_effect = [existing, existing]
    service.is_multi_line_purchase.return_value = False
    service.confirm.return_value = {"status": "enqueued", "enqueued": True}
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID, "status": "confirmed"}

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp, patch(
        "entities.project.business.service.ProjectService"
    ) as ps, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scs:
        uid.get.return_value = USER_ID
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

        out = confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True})

    ace.assert_not_called()
    assert acp.call_args_list[0].args == (None,)
    assert acp.call_args_list[1].args == (10,)
    service.confirm.assert_called_once()
    assert out["data"]["enqueued"] is True


def test_confirm_inaccessible_target_project_returns_404_without_claim_or_confirm():
    service = MagicMock()
    service.read_by_public_id.return_value = _suggestion(expense_id=1, claimed_by_user_id=USER_ID)
    service.is_multi_line_purchase.return_value = False

    def project_gate(project_id):
        if project_id == 10:
            raise EntityNotAccessibleError("Project", 10)

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ), patch(
        "entities.coding_suggestion.api.router.assert_can_access_project",
        side_effect=project_gate,
    ) as acp, patch(
        "entities.project.business.service.ProjectService"
    ) as ps, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ):
        uid.get.return_value = USER_ID
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

        _expect_coding_not_found(
            lambda: confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True}),
            detail="Project not found",
        )

    assert acp.call_args_list[-1].args == (10,)
    service.claim.assert_not_called()
    service.confirm.assert_not_called()


def test_confirm_accessible_proceeds_as_before():
    service = MagicMock()
    existing = _suggestion(expense_id=5, claimed_by_user_id=USER_ID)
    service.read_by_public_id.side_effect = [existing, existing]
    service.is_multi_line_purchase.return_value = False
    service.confirm.return_value = {"status": "enqueued", "enqueued": True}
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID}

    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router.assert_can_access_expense"
    ) as ace, patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ) as acp, patch(
        "entities.project.business.service.ProjectService"
    ) as ps, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scs:
        uid.get.return_value = USER_ID
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

        confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True})

    ace.assert_called_once_with(5)
    acp.assert_called_once_with(10)
    service.confirm.assert_called_once()


def test_confirm_system_admin_bypasses_suggestion_expense_gate():
    service = MagicMock()
    existing = _suggestion(expense_id=99, claimed_by_user_id=USER_ID)
    service.read_by_public_id.side_effect = [existing, existing]
    service.is_multi_line_purchase.return_value = False
    service.confirm.return_value = {"status": "enqueued", "enqueued": True}
    service.to_api_dict.return_value = {"public_id": PUBLIC_ID}

    admin_token = current_is_system_admin.set(True)
    user_token = current_user_id.set(USER_ID)
    try:
        with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), patch(
            "entities.project.business.service.ProjectService"
        ) as ps, patch(
            "entities.sub_cost_code.business.service.SubCostCodeService"
        ) as scs:
            ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
            scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
            from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

            confirm_expense_coding_item_router(PUBLIC_ID, _CONFIRM_BODY, _={"can_update": True})
    finally:
        current_user_id.reset(user_token)
        current_is_system_admin.reset(admin_token)

    service.confirm.assert_called_once()
