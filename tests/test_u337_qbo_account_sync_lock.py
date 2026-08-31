"""
U-337 regression: POST /api/v1/sync/qbo-accounts must serialize concurrent
calls through `qbo_app_lock`, using the SAME resource key as the admin
`sync/qbo/{entity}` path (`shared/api/admin.py`) — both share
`integrations.intuit.qbo.base.locking.qbo_entity_sync_lock_resource("account")`.
Before this unit, the handler called `QboAccountService.sync_from_qbo`
unlocked entirely — two overlapping calls could race the read-mirror-then-write
upsert and the last-writer-wins `SetCompanyApAccount` cache write.

Pass-1 fix-loop round: Codex (xhigh) confirmed a P1 in the first cut, which
keyed the API lock `qbo_sync:account:{realm_id}` — a string that never
contended with the admin path's entity-only `qbo_sync:account` key, so a
user-triggered and an admin/scheduler-triggered sync could still race each
other unlocked. Fixed by extracting the shared `qbo_entity_sync_lock_resource`
helper both routes now call, and
`test_admin_and_api_account_sync_share_one_lock_resource` below pins it.

Mutation-proof (verified manually, not re-asserted here since these tests
fully replace the primitive via monkeypatch): reverting the handler to call
`service.sync_from_qbo` directly, without the `with qbo_app_lock(...)` wrap,
turns `test_sync_denied_lock_raises_409_and_skips_sync` RED — no lock is
consulted, no 409 is raised, and `sync_from_qbo` runs unlocked.
"""

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import integrations.intuit.qbo.account.api.router as account_router
import shared.api.admin as admin_module
from conftest import mock_qbo_app_lock_denied
from integrations.intuit.qbo.account.api.schemas import QboAccountSync
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome

REALM_ID = "9341453129481934"
EXPECTED_LOCK_RESOURCE = "qbo_sync:account"


def _body(realm_id=REALM_ID):
    return QboAccountSync(realm_id=realm_id, last_updated_time=None)


def test_sync_acquires_lock_matching_admin_entity_key(monkeypatch):
    """The handler acquires `qbo_app_lock` keyed `qbo_sync:account` — the
    same resource the admin path uses for entity="account" — around the
    sync call, proving the lock actually wraps `sync_from_qbo`, not just
    that the import exists."""
    entered = []

    @contextmanager
    def _tracking_lock(resource_name, timeout_ms=15000):
        assert resource_name == EXPECTED_LOCK_RESOURCE
        entered.append("enter")
        yield True
        entered.append("exit")

    monkeypatch.setattr(account_router, "qbo_app_lock", _tracking_lock)

    fake_account = MagicMock()
    fake_account.to_dict.return_value = {"id": 1}
    outcome = SyncOutcome.for_service_pull()
    outcome.record_synced(fake_account)

    mock_service = MagicMock()

    def _sync_from_qbo(realm_id, last_updated_time):
        # Sync call must happen INSIDE the lock (after enter, before exit).
        assert entered == ["enter"]
        assert realm_id == REALM_ID
        return outcome

    mock_service.sync_from_qbo.side_effect = _sync_from_qbo
    monkeypatch.setattr(account_router, "service", mock_service)

    result = account_router.sync_qbo_accounts_router(body=_body(), current_user={})

    assert entered == ["enter", "exit"]
    assert result == {"data": [{"id": 1}], "count": 1}
    mock_service.sync_from_qbo.assert_called_once_with(
        realm_id=REALM_ID, last_updated_time=None
    )


def test_sync_denied_lock_raises_409_and_skips_sync(monkeypatch):
    """Two overlapping calls must serialize, not race: when the lock is
    busy, the handler raises 409 rather than letting the second caller run
    `sync_from_qbo` unlocked."""
    monkeypatch.setattr(account_router, "qbo_app_lock", mock_qbo_app_lock_denied)

    mock_service = MagicMock()
    monkeypatch.setattr(account_router, "service", mock_service)

    with pytest.raises(HTTPException) as exc_info:
        account_router.sync_qbo_accounts_router(body=_body(), current_user={})

    assert exc_info.value.status_code == 409
    assert REALM_ID in exc_info.value.detail
    mock_service.sync_from_qbo.assert_not_called()


def test_admin_and_api_account_sync_share_one_lock_resource(monkeypatch):
    """Codex Pass-1 P1 (confirmed, fixed): the API route and the admin
    `sync/qbo/{entity}` route (entity="account") must resolve to the exact
    same `qbo_app_lock` resource string, or a user-triggered sync and an
    admin/scheduler-triggered sync for the same entity can still race each
    other unlocked even though each individually now takes a lock."""
    seen_resources = []

    @contextmanager
    def _recording_lock(resource_name, timeout_ms=15000):
        seen_resources.append(resource_name)
        yield True

    monkeypatch.setattr(account_router, "qbo_app_lock", _recording_lock)
    mock_service = MagicMock()
    mock_service.sync_from_qbo.return_value = SyncOutcome.for_service_pull()
    monkeypatch.setattr(account_router, "service", mock_service)
    account_router.sync_qbo_accounts_router(body=_body(), current_user={})

    monkeypatch.setattr(admin_module, "qbo_app_lock", _recording_lock)
    monkeypatch.setattr(
        admin_module,
        "_qbo_sync_fn",
        lambda entity: (lambda: {"result": {"success": True}, "status_code": 200}),
    )
    asyncio.run(admin_module.sync_qbo_router(entity="account", attachments=True))

    assert seen_resources == [EXPECTED_LOCK_RESOURCE, EXPECTED_LOCK_RESOURCE]
