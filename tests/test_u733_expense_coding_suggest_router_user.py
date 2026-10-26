"""U-733: the per-expense coding-suggest endpoint resolves the user from the JWT `sub`.

Real tokens carry `sub` (Auth.PublicId) and no `id`; the endpoint used
`current_user.get("id")`, so every call answered 401 and the web client logged the
user out. These tests call the router with a real-shaped current_user.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from entities.expense.api import router as expense_router

TOKEN_USER = {"sub": "11111111-2222-3333-4444-555555555555", "token_type": "access"}


def test_suggest_resolves_user_from_sub_not_id():
    svc = MagicMock()
    svc.suggest_for_expense.return_value = {"sub_cost_code": None, "project": None}
    with patch.object(expense_router, "resolve_user_id", return_value=42) as resolve, patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService", return_value=svc
    ):
        out = asyncio.run(
            expense_router.suggest_expense_coding_for_expense_router(
                public_id="exp-1", current_user=dict(TOKEN_USER)
            )
        )
    resolve.assert_called_once_with(TOKEN_USER)
    assert svc.suggest_for_expense.call_args.kwargs["user_id"] == 42
    assert out is not None


def test_suggest_unresolvable_user_still_401():
    with patch.object(
        expense_router,
        "resolve_user_id",
        side_effect=HTTPException(status_code=401, detail="Invalid authentication token."),
    ):
        with pytest.raises(HTTPException) as err:
            asyncio.run(
                expense_router.suggest_expense_coding_for_expense_router(
                    public_id="exp-1", current_user={"token_type": "access"}
                )
            )
    assert err.value.status_code == 401
