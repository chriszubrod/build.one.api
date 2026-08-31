"""
U-340: fan out U-337's proven `qbo_app_lock` pattern from `account` to every
sibling per-entity QBO sync API route (bill, purchase, vendor, customer,
company_info, vendorcredit — `term` has no API router to fix, `invoice`'s
route is dead code that never calls `sync_from_qbo`, both verified at Map).

Before this unit each of these handlers called its service's `sync_from_qbo`
directly, unlocked — able to race the admin `sync/qbo/{entity}` dispatcher
(and each other) exactly like the account race U-337 closed. Mirrors
`tests/test_u337_qbo_account_sync_lock.py`'s three assertions per entity:
the lock wraps the sync call, a busy lock raises 409 and skips the sync, and
the API route's lock resource is the exact string the admin dispatcher would
compute for the same entity key (a mismatch here is exactly the U-337 Pass-1
P1 that let two locked-individually entry points still race each other).

Mutation-proof (verified manually per case, not re-asserted here since these
tests replace the primitive via monkeypatch, same as test_u337's note):
reverting any one handler's `with qbo_app_lock(...)` wrap back to a bare
`service.sync_from_qbo(...)` call turns that case's
`test_sync_denied_lock_raises_409_and_skips_sync` RED.
"""

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import scripts.sync_qbo_account as sync_qbo_account_script

import integrations.intuit.qbo.bill.api.router as bill_router
import integrations.intuit.qbo.company_info.api.router as company_info_router
import integrations.intuit.qbo.customer.api.router as customer_router
import integrations.intuit.qbo.purchase.api.router as purchase_router
import integrations.intuit.qbo.vendor.api.router as vendor_router
import integrations.intuit.qbo.vendorcredit.api.router as vendorcredit_router
import shared.api.admin as admin_module
from conftest import mock_qbo_app_lock_denied
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.bill.api.schemas import QboBillSync
from integrations.intuit.qbo.company_info.api.schemas import QboCompanyInfoSync
from integrations.intuit.qbo.customer.api.schemas import QboCustomerSync
from integrations.intuit.qbo.purchase.api.schemas import QboPurchaseSync
from integrations.intuit.qbo.vendor.api.schemas import QboVendorSync
from integrations.intuit.qbo.vendorcredit.api.schemas import QboVendorCreditSyncRequest

REALM_ID = "9341453129481934"


def _outcome():
    fake_item = MagicMock()
    fake_item.to_dict.return_value = {"id": 1}
    outcome = SyncOutcome.for_service_pull()
    outcome.record_synced(fake_item)
    return outcome


def _patch_module_service(monkeypatch, module):
    """bill/purchase/vendor/customer/company_info hold a module-level `service`."""
    mock_service = MagicMock()
    mock_service.sync_from_qbo.return_value = _outcome()
    monkeypatch.setattr(module, "service", mock_service)
    return mock_service


def _patch_vendorcredit_service(monkeypatch, module):
    """vendorcredit instantiates `QboVendorCreditService()` inside the handler —
    patch the class so every instantiation returns the same mock."""
    mock_service = MagicMock()
    mock_service.sync_from_qbo.return_value = _outcome()
    monkeypatch.setattr(module, "QboVendorCreditService", lambda: mock_service)
    return mock_service


# Each case: (entity_key, module, call, patch_service)
# `call(current_user)` invokes the router function with a realm_id=REALM_ID body.
CASES = [
    (
        "bill",
        bill_router,
        lambda cu: bill_router.sync_qbo_bills_router(
            body=QboBillSync(realm_id=REALM_ID, last_updated_time=None), current_user=cu
        ),
        _patch_module_service,
    ),
    (
        "purchase",
        purchase_router,
        lambda cu: purchase_router.sync_qbo_purchases_router(
            body=QboPurchaseSync(realm_id=REALM_ID, last_updated_time=None), current_user=cu
        ),
        _patch_module_service,
    ),
    (
        "vendor",
        vendor_router,
        lambda cu: vendor_router.sync_qbo_vendors_router(
            body=QboVendorSync(realm_id=REALM_ID, last_updated_time=None), current_user=cu
        ),
        _patch_module_service,
    ),
    (
        "customer",
        customer_router,
        lambda cu: customer_router.sync_qbo_customers_router(
            body=QboCustomerSync(realm_id=REALM_ID, last_updated_time=None), current_user=cu
        ),
        _patch_module_service,
    ),
    (
        "company_info",
        company_info_router,
        lambda cu: company_info_router.sync_qbo_company_info_router(
            body=QboCompanyInfoSync(realm_id=REALM_ID), current_user=cu
        ),
        _patch_module_service,
    ),
    (
        "vendorcredit",
        vendorcredit_router,
        lambda cu: vendorcredit_router.sync_qbo_vendor_credits_router(
            body=QboVendorCreditSyncRequest(realm_id=REALM_ID, last_updated_time=None),
            current_user=cu,
        ),
        _patch_vendorcredit_service,
    ),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("entity_key,module,call,patch_service", CASES, ids=CASE_IDS)
def test_sync_acquires_lock_and_calls_sync_inside_it(
    monkeypatch, entity_key, module, call, patch_service
):
    """The handler acquires `qbo_app_lock` keyed to its own entity, and the
    sync call happens INSIDE the lock (proving the lock actually wraps
    `sync_from_qbo`, not just that the import exists)."""
    entered = []

    @contextmanager
    def _tracking_lock(resource_name, timeout_ms=15000):
        assert resource_name == f"qbo_sync:{entity_key}"
        entered.append("enter")
        yield True
        entered.append("exit")

    monkeypatch.setattr(module, "qbo_app_lock", _tracking_lock)
    mock_service = patch_service(monkeypatch, module)

    def _assert_inside_lock(*args, **kwargs):
        assert entered == ["enter"]
        assert kwargs.get("realm_id") == REALM_ID
        return _outcome()

    mock_service.sync_from_qbo.side_effect = _assert_inside_lock

    call({})

    assert entered == ["enter", "exit"]
    mock_service.sync_from_qbo.assert_called_once()


@pytest.mark.parametrize("entity_key,module,call,patch_service", CASES, ids=CASE_IDS)
def test_sync_denied_lock_raises_409_and_skips_sync(
    monkeypatch, entity_key, module, call, patch_service
):
    """A busy lock must raise 409 and never reach `sync_from_qbo` — two
    overlapping calls serialize instead of racing."""
    monkeypatch.setattr(module, "qbo_app_lock", mock_qbo_app_lock_denied)
    mock_service = patch_service(monkeypatch, module)

    with pytest.raises(HTTPException) as exc_info:
        call({})

    assert exc_info.value.status_code == 409
    mock_service.sync_from_qbo.assert_not_called()


@pytest.mark.parametrize("entity_key,module,call,patch_service", CASES, ids=CASE_IDS)
def test_api_route_and_admin_dispatcher_share_one_lock_resource(
    monkeypatch, entity_key, module, call, patch_service
):
    """Codex Pass-1 P1 that U-337 fixed for `account`, checked for every
    sibling entity here: the per-entity API route and the admin
    `sync/qbo/{entity}` dispatcher must resolve to the exact same
    `qbo_app_lock` resource string, or a user-triggered sync and an
    admin/scheduler-triggered sync for the same entity race each other
    unlocked even though each individually takes a lock."""
    seen_resources = []

    @contextmanager
    def _recording_lock(resource_name, timeout_ms=15000):
        seen_resources.append(resource_name)
        yield True

    monkeypatch.setattr(module, "qbo_app_lock", _recording_lock)
    patch_service(monkeypatch, module)
    call({})

    monkeypatch.setattr(admin_module, "qbo_app_lock", _recording_lock)
    monkeypatch.setattr(
        admin_module,
        "_qbo_sync_fn",
        lambda entity: (lambda: {"result": {"success": True}, "status_code": 200}),
    )
    asyncio.run(admin_module.sync_qbo_router(entity=entity_key, attachments=True))

    expected = f"qbo_sync:{entity_key}"
    assert seen_resources == [expected, expected]


# --- scripts/sync_qbo_account.py's third (CLI-direct) entry point ---------- #


def test_account_script_run_locked_acquires_lock_matching_admin_key(monkeypatch):
    """`run_locked` (the CLI `__main__` path) locks on the same
    `qbo_sync:account` resource as the API route and admin dispatcher, and
    only calls `sync_qbo_account()` once the lock is held."""
    entered = []

    @contextmanager
    def _tracking_lock(resource_name, timeout_ms=15000):
        assert resource_name == "qbo_sync:account"
        entered.append("enter")
        yield True
        entered.append("exit")

    monkeypatch.setattr(sync_qbo_account_script, "qbo_app_lock", _tracking_lock)

    def _fake_sync_qbo_account(skip_sync_record_update=False, dry_run=False):
        assert entered == ["enter"]
        return {"result": {"success": True}, "status_code": 200}

    monkeypatch.setattr(
        sync_qbo_account_script, "sync_qbo_account", _fake_sync_qbo_account
    )

    result = sync_qbo_account_script.run_locked()

    assert entered == ["enter", "exit"]
    assert result == {"result": {"success": True}, "status_code": 200}


def test_account_script_run_locked_denied_returns_409_and_skips_sync(monkeypatch):
    """A busy lock must short-circuit to a failure envelope (so
    `exit_nonzero_on_sync_failure` exits non-zero for cron/CLI callers)
    without ever calling `sync_qbo_account()`."""
    monkeypatch.setattr(
        sync_qbo_account_script, "qbo_app_lock", mock_qbo_app_lock_denied
    )
    called = []
    monkeypatch.setattr(
        sync_qbo_account_script,
        "sync_qbo_account",
        lambda **kwargs: called.append(kwargs),
    )

    result = sync_qbo_account_script.run_locked()

    assert result["status_code"] == 409
    assert result["result"]["success"] is False
    assert called == []
