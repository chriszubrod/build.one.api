"""U-485 — Expense router async offload parity with Bill (U-477 Phase 3).

Pure-logic / route-table pins — no live DB. Blocking pyodbc I/O must not run
on the event loop; ContextVars must survive the ``asyncio.to_thread`` hop.
"""

import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.authz import (
    clear_authz_context,
    current_company_id,
    current_is_system_admin,
    current_user_id,
    set_authz_context,
)

# Seven handlers converted in U-485 — resolved from the module, not path strings.
ASYNC_OFFLOAD_HANDLER_NAMES = (
    "create_expense_router",
    "get_expenses_router",
    "get_expense_public_id_coding_router",
    "get_expense_by_reference_number_and_vendor_router",
    "get_expense_by_public_id_router",
    "update_expense_by_public_id_router",
    "delete_expense_by_public_id_router",
)

CLAIM_RELEASE_FORBIDDEN_SUFFIXES = ("/claim", "/release")


def _expense_router_handler_names():
    import entities.expense.api.router as expense_router

    names = set(ASYNC_OFFLOAD_HANDLER_NAMES)
    for route in expense_router.router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        name = getattr(endpoint, "__name__", None)
        if name in names:
            yield name


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
# 1 — authz ContextVars survive asyncio.to_thread (not bare run_in_executor)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_authz_context():
    clear_authz_context()
    yield
    clear_authz_context()


def test_authz_contextvars_survive_asyncio_to_thread_like_handlers():
    """Row scoping rides on ContextVars; a bare executor hop drops them."""
    async def _run():
        # is_system_admin is set True deliberately: the ContextVar's DEFAULT is
        # False, so asserting False here would pass even with zero propagation.
        set_authz_context(user_id=42, company_id=7, is_system_admin=True)

        seen: dict[str, object] = {}

        def _probe_inside_thread():
            seen["user_id"] = current_user_id.get()
            seen["company_id"] = current_company_id.get()
            seen["is_system_admin"] = current_is_system_admin.get()

        await asyncio.to_thread(_probe_inside_thread)

        assert seen == {
            "user_id": 42,
            "company_id": 7,
            "is_system_admin": True,
        }

    asyncio.run(_run())


def test_authz_contextvars_do_not_survive_bare_run_in_executor():
    """Guards against swapping to_thread for loop.run_in_executor(None, ...)."""
    async def _run():
        # Set is_system_admin TRUE: a fresh thread context falls back to each
        # ContextVar's DEFAULT, and that default is False — so True is the only
        # value that makes non-propagation distinguishable from propagation.
        set_authz_context(user_id=42, company_id=7, is_system_admin=True)

        seen: dict[str, object] = {}

        def _probe_inside_thread():
            seen["user_id"] = current_user_id.get()
            seen["company_id"] = current_company_id.get()
            seen["is_system_admin"] = current_is_system_admin.get()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _probe_inside_thread)

        # None / None / False are the declared DEFAULTS, i.e. nothing crossed.
        assert seen["user_id"] is None
        assert seen["company_id"] is None
        assert seen["is_system_admin"] is False

    asyncio.run(_run())


def test_get_expense_by_public_id_offload_sees_authz_context():
    """Same hop the handler uses — context visible inside the sync _fetch body."""
    from entities.expense.api.router import get_expense_by_public_id_router

    set_authz_context(user_id=99, company_id=3, is_system_admin=True)
    captured: dict[str, object] = {}

    expense = SimpleNamespace(
        id=5,
        is_draft=False,
        status="completed",
        to_dict=lambda: {"public_id": "exp-5"},
    )

    def _read_by_public_id(*, public_id):
        captured["user_id"] = current_user_id.get()
        captured["company_id"] = current_company_id.get()
        captured["is_system_admin"] = current_is_system_admin.get()
        captured["public_id"] = public_id
        return expense

    service = MagicMock()
    service.read_by_public_id.side_effect = _read_by_public_id
    review_repo = MagicMock()
    review_repo.read_current_by_expense_id.return_value = None
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {5: [_coding_item()]}

    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        asyncio.run(get_expense_by_public_id_router(public_id="exp-5", current_user={}))

    assert captured == {
        "user_id": 99,
        "company_id": 3,
        "is_system_admin": True,
        "public_id": "exp-5",
    }


# ---------------------------------------------------------------------------
# 2 — seven named handlers are async def + asyncio.to_thread
# ---------------------------------------------------------------------------


def test_async_offload_handlers_are_registered_on_expense_router():
    registered = set(_expense_router_handler_names())
    assert registered == set(ASYNC_OFFLOAD_HANDLER_NAMES)


@pytest.mark.parametrize("handler_name", ASYNC_OFFLOAD_HANDLER_NAMES)
def test_expense_handler_is_async_and_offloads_blocking_work(handler_name):
    import entities.expense.api.router as mod

    fn = getattr(mod, handler_name)
    assert inspect.iscoroutinefunction(fn), (
        f"{handler_name} must be async def so blocking I/O runs off the event loop"
    )
    source = inspect.getsource(fn)
    assert "asyncio.to_thread" in source, (
        f"{handler_name} must offload blocking work via asyncio.to_thread (Bill parity)"
    )


# ---------------------------------------------------------------------------
# 3 — complete_expense_router stays sync (Bill parity)
# ---------------------------------------------------------------------------


def test_complete_expense_router_stays_sync_matching_bill():
    from entities.bill.api.router import complete_bill_router
    from entities.expense.api.router import complete_expense_router

    assert not inspect.iscoroutinefunction(complete_expense_router), (
        "complete_expense_router must stay sync — Bill's complete_bill_router is "
        "also sync; converting the 202 + background-work path is out of scope for "
        "U-485 async offload parity"
    )
    assert not inspect.iscoroutinefunction(complete_bill_router)


# ---------------------------------------------------------------------------
# 4 — claim / release HTTP routes removed
# ---------------------------------------------------------------------------


def test_claim_and_release_routes_are_not_registered():
    from app import app

    paths = [getattr(route, "path", "") for route in app.routes]
    for path in paths:
        if "expense" not in path.lower():
            continue
        for suffix in CLAIM_RELEASE_FORBIDDEN_SUFFIXES:
            assert not path.endswith(suffix), (
                f"Unused lease route still registered: {path!r}"
            )


# ---------------------------------------------------------------------------
# 5 — service lease survives; confirm still auto-claims
# ---------------------------------------------------------------------------


def test_expense_coding_service_lease_methods_still_exist():
    from entities.expense_coding_item.business.service import ExpenseCodingItemService

    assert callable(getattr(ExpenseCodingItemService, "claim", None))
    assert callable(getattr(ExpenseCodingItemService, "release", None))


def test_confirm_router_still_calls_service_claim():
    from entities.expense_coding_item.api.router import confirm_expense_coding_item_router

    source = inspect.getsource(confirm_expense_coding_item_router)
    assert "service.claim(" in source


# ---------------------------------------------------------------------------
# 6 — response key sets unchanged (list + single read)
# ---------------------------------------------------------------------------


def test_list_handler_response_keys_unchanged():
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
    coding_repo.read_state_by_expense_ids.return_value = {42: [_coding_item(status="pending")]}

    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        payload = asyncio.run(get_expenses_router(
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

    assert set(payload.keys()) == {"data", "count", "page", "page_size"}
    row = payload["data"][0]
    assert "status" in row
    assert "review_status" in row
    assert "review_status_kind" in row
    assert "coding" in row
    assert set(row["coding"].keys()) == {"needs_coding", "open_items", "items"}


def test_single_read_handler_response_keys_unchanged():
    from entities.expense.api.router import get_expense_by_public_id_router

    expense = SimpleNamespace(
        id=7,
        is_draft=True,
        status="draft",
        to_dict=lambda: {"public_id": "exp-7", "vendor_id": 1},
    )
    service = MagicMock()
    service.read_by_public_id.return_value = expense
    review_repo = MagicMock()
    review_repo.read_current_by_expense_id.return_value = None
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {7: []}

    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        payload = asyncio.run(get_expense_by_public_id_router(
            public_id="exp-7",
            current_user={},
        ))

    assert set(payload.keys()) == {"data"}
    row = payload["data"]
    assert "status" in row
    assert "review_status" in row
    assert "review_status_kind" in row
    assert "coding" in row
