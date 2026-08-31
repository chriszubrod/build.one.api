"""
U-347 DI-resolution smoke test — the one real implementation risk the design
doc (`docs/design/u347-qbo-sync-locked-decorator.md` §3.2) called out before
any code was written: does FastAPI's dependency-injection resolution still
work through `@qbo_sync_locked_route(entity)`?

`functools.wraps` sets `__wrapped__`, which `inspect.signature` follows by
default, so FastAPI's route registration (`get_typed_signature` ->
`inspect.signature(endpoint)`) sees the ORIGINAL handler's `body: QboXSync`
and `current_user: dict = Depends(...)` params straight through the
decorator — in theory. The design doc was explicit that this needed proving
with a real request per repointed router, not asserted from reading
`inspect`'s docs: a DI break here would surface as a runtime 422 (body not
parsed) or 500 (dependency not resolved), not a decoration-time error.

Each case below builds the real ASGI app (`from app import app` — the exact
object FastAPI registers routes on in production), overrides the route's own
RBAC dependency object (resolved dynamically via `inspect.signature`, not
hardcoded, so this test can't silently drift from whatever the route
actually declares), patches the module's `service` (or, for vendorcredit,
the service class) so no DB call happens, patches `qbo_app_lock` to grant
(this is a DI smoke test, not a lock test — the lock behavior itself is
covered by test_u337/test_u340/test_u347's import-boundary guard), and POSTs
the minimal valid JSON body (every one of these 8 schemas requires only
`realm_id` — confirmed by reading each `schemas.py`). A passing 200 with the
expected envelope is the proof: routing, body validation, RBAC dependency
resolution, the lock decorator, and the handler body all round-tripped
correctly through the decorator.

**Unexpected finding while writing this test (not a decorator bug — a
pre-existing gap this test is the first to surface, because every prior
lock test calls the router function directly in Python, never through the
mounted app):** `integrations/intuit/qbo/customer/api/router.py` and
`integrations/intuit/qbo/item/api/router.py` are never `app.include_router`'d
in `app.py` — confirmed by grep, both files are imported ONLY by test
modules, nowhere in production code. `POST /api/v1/sync/qbo-customers` and
`POST /api/v1/sync/qbo-items` do not exist as reachable HTTP endpoints today,
regardless of this unit's decorator work. U-340's own docstring claim that
`customer` was one of "6 remaining per-entity QBO sync routers with live
`/sync/*` handlers" was incorrect for this one entity — it verified the
Python-level lock/call contract (which is real and correct) but never
verified HTTP-reachability, because no test before this one went through the
ASGI app. `item`'s route has the identical gap. Locking both is still the
right, harmless thing to do (defense-in-depth for if/when either router is
ever mounted, and the CLI + admin-dispatcher paths for both entities ARE
live and were already racing each other pre-U-346), but the "live gap in
prod today" framing in this unit's design-doc Map (§2.1) overstated it for
`item` specifically — flagged to `/em` at handback, not silently corrected
in the approved doc. `CASES` below only exercises the 6 entities actually
mounted through the full HTTP path; `test_customer_and_item_routers_are_not_mounted_in_app`
pins the current (unreachable) state so a future accidental mount — or an
accidental "cleanup" deletion of what looks like dead code but isn't, since
it's still called directly by other tests and would need the same review
either way — gets noticed.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

import integrations.intuit.qbo.account.api.router as account_router
import integrations.intuit.qbo.base.locking as locking
import integrations.intuit.qbo.bill.api.router as bill_router
import integrations.intuit.qbo.company_info.api.router as company_info_router
import integrations.intuit.qbo.purchase.api.router as purchase_router
import integrations.intuit.qbo.vendor.api.router as vendor_router
import integrations.intuit.qbo.vendorcredit.api.router as vendorcredit_router
from app import app
from conftest import mock_qbo_app_lock_denied, mock_qbo_app_lock_granted
from qbo_sync_test_helpers import _patch_module_service, _patch_vendorcredit_service


# Each case: (entity_key, path, module, handler_fn_name, patch_service).
# `customer` and `item` are DELIBERATELY excluded here — see the module
# docstring: neither router is mounted in `app.py`, so there is no live path
# for a TestClient POST to reach. Their decorator/DI wiring is still proven
# by `test_u340_qbo_entity_sync_lock_fanout.py`'s direct-call cases.
CASES = [
    ("account", "/api/v1/sync/qbo-accounts", account_router, "sync_qbo_accounts_router", _patch_module_service),
    ("bill", "/api/v1/sync/qbo-bills", bill_router, "sync_qbo_bills_router", _patch_module_service),
    ("purchase", "/api/v1/sync/qbo-purchases", purchase_router, "sync_qbo_purchases_router", _patch_module_service),
    ("vendor", "/api/v1/sync/qbo-vendors", vendor_router, "sync_qbo_vendors_router", _patch_module_service),
    ("company_info", "/api/v1/sync/qbo-company-info", company_info_router, "sync_qbo_company_info_router", _patch_module_service),
    ("vendorcredit", "/api/v1/sync/qbo-vendorcredits", vendorcredit_router, "sync_qbo_vendor_credits_router", _patch_vendorcredit_service),
]
CASE_IDS = [c[0] for c in CASES]


def test_customer_and_item_routers_are_not_mounted_in_app():
    """Pins the finding in the module docstring: as of U-347, neither
    router is wired into `app.py`. If this ever flips (someone adds
    `app.include_router(...)` for one of them), this test goes RED as a
    prompt to also add it to `CASES` above for full HTTP-path coverage —
    not because mounting it would be wrong, just because it's a
    consequential change (a genuinely new live endpoint) that deserves its
    own review, not a silent side effect of some unrelated diff."""
    mounted_paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/v1/sync/qbo-customers" not in mounted_paths
    assert "/api/v1/sync/qbo-items" not in mounted_paths


@pytest.fixture
def granted_lock(monkeypatch):
    monkeypatch.setattr(locking, "qbo_app_lock", mock_qbo_app_lock_granted)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("entity_key,path,module,handler_fn_name,patch_service", CASES, ids=CASE_IDS)
def test_route_resolves_body_and_dependency_through_the_decorator(
    monkeypatch, client, granted_lock, entity_key, path, module, handler_fn_name, patch_service
):
    handler = getattr(module, handler_fn_name)
    rbac_dependency = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "test-di-smoke"}
    try:
        mock_service = patch_service(monkeypatch, module)
        response = client.post(path, json={"realm_id": "9341453129481934"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, (
        f"{entity_key}: expected 200 (proves body validation + RBAC "
        f"dependency + lock decorator + handler all resolved through "
        f"@qbo_sync_locked_route), got {response.status_code}: {response.text}"
    )
    mock_service.sync_from_qbo.assert_called_once()
    call_kwargs = mock_service.sync_from_qbo.call_args.kwargs
    assert call_kwargs.get("realm_id") == "9341453129481934"


@pytest.mark.parametrize("entity_key,path,module,handler_fn_name,patch_service", CASES, ids=CASE_IDS)
def test_route_still_validates_a_missing_required_field(
    client, granted_lock, entity_key, path, module, handler_fn_name, patch_service
):
    """Confirms the decorator didn't accidentally bypass Pydantic body
    validation — POSTing without the required `realm_id` must still 422,
    proving FastAPI is genuinely parsing `body: QboXSync` through the
    decorator rather than, say, silently treating it as `**kwargs`."""
    handler = getattr(module, handler_fn_name)
    rbac_dependency = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "test-di-smoke"}
    try:
        response = client.post(path, json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, (
        f"{entity_key}: expected 422 for a missing required realm_id "
        f"(proves body: QboXSync is still validated through the decorator), "
        f"got {response.status_code}: {response.text}"
    )


@pytest.mark.parametrize("entity_key,path,module,handler_fn_name,patch_service", CASES, ids=CASE_IDS)
def test_route_denies_lock_through_the_mounted_app(
    monkeypatch, client, entity_key, path, module, handler_fn_name, patch_service
):
    """Codex Pass-1 P3 (confirmed real, fixed): the two tests above only
    exercise the GRANTED-lock path over HTTP — they'd stay green even if
    `@qbo_sync_locked_route` and `@router.post(...)` were ever stacked in
    the wrong order, because `router.post` captures whatever function
    object it's given AT REGISTRATION TIME. Stack them
    `@qbo_sync_locked_route(...)` above `@router.post(...)` (the reverse of
    every router in this diff) and `router.post` registers the RAW,
    unwrapped handler as the live ASGI route — the module-level name
    (`sync_qbo_accounts_router`, etc.) still ends up bound to the wrapped
    version afterwards, so `test_u337`/`test_u340`'s direct-call denial
    tests would keep passing while the actual mounted HTTP endpoint quietly
    ignores the lock. This test closes that gap: it denies the lock and
    asserts the MOUNTED route (not the module attribute) 409s and never
    reaches the service — the only assertion that can only pass if the
    live ASGI endpoint is genuinely the locked wrapper.

    Mutation-proven: temporarily reversing account's decorator order
    (`@qbo_sync_locked_route("account")` above `@router.post(...)`) turns
    this case RED (200 instead of 409, service called) while every other
    test in this repo — including test_u337's own denial test — stays
    green, confirming this was a real, previously-uncovered gap."""
    monkeypatch.setattr(locking, "qbo_app_lock", mock_qbo_app_lock_denied)

    handler = getattr(module, handler_fn_name)
    rbac_dependency = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "test-di-smoke"}
    try:
        mock_service = patch_service(monkeypatch, module)
        response = client.post(path, json={"realm_id": "9341453129481934"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409, (
        f"{entity_key}: expected 409 on a busy lock through the MOUNTED "
        f"route (not just the module-level function), got "
        f"{response.status_code}: {response.text}"
    )
    mock_service.sync_from_qbo.assert_not_called()
