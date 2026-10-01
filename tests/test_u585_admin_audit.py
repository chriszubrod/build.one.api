"""U-585 — admin audit log service, auth/user_role audit hooks, SQL discipline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bcrypt
import pytest

# tests/ must be on sys.path for `from conftest import ...` (house pattern).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import actor_context  # noqa: E402

from entities.admin_audit_log.business.service import (
    AdminAuditLogService,
    sanitize_detail,
)
from entities.auth.business.model import Auth
from entities.auth.business.service import AuthService
from entities.user.business.model import User
from entities.user_role.business.model import UserRole
from entities.user_role.business.service import UserRoleService

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "entities/admin_audit_log/sql/dbo.admin_audit_log.sql"


def test_sanitize_detail_drops_sensitive_keys_at_every_level():
    raw = {
        "username": "ada",
        "password": "secret-plain",
        "Password": "also-secret",
        "new_password": "nope",
        "password_hash": "$2b$...",
        "refresh_token": "rt-123",
        "nested": {
            "ok": "yes",
            "current_password": "old",
            "deep": {"access_token": "tok", "note": "keep"},
        },
        "items": [{"secret": "drop-me", "label": "keep"}],
    }
    cleaned = sanitize_detail(raw)
    assert cleaned == {
        "username": "ada",
        "nested": {"ok": "yes", "deep": {"note": "keep"}},
        "items": [{"label": "keep"}],
    }
    blob = json.dumps(cleaned)
    assert "secret-plain" not in blob
    assert "rt-123" not in blob


def test_sanitize_detail_non_dict_input_persists_as_null():
    """Contract pinned after a Pass-2 re-check: a caller handing a non-dict
    (e.g. a list) must land as SQL NULL, never as serialized JSON."""
    assert sanitize_detail(None) is None
    assert sanitize_detail(["not", "a", "dict"]) is None  # type: ignore[arg-type]
    assert sanitize_detail({"username": "ada"}) == {"username": "ada"}


def test_record_admin_action_logs_and_reraises_on_repo_failure():
    repo = MagicMock()
    repo.create.side_effect = RuntimeError("db down")
    service = AdminAuditLogService(repo=repo)
    import entities.admin_audit_log.business.service as svc_mod

    with actor_context(17, True), patch.object(svc_mod.logger, "error") as log_error:
        with pytest.raises(RuntimeError, match="db down"):
            service.record_admin_action(
                action="auth.set_credentials",
                target_user_id=9,
                detail={"username": "ada", "password": "never-logged"},
            )
        log_error.assert_called_once()
        logged = str(log_error.call_args)
        # The app-log line is the FALLBACK trail for the post-commit window:
        # it must carry the marker, the actor, the target and the sanitized
        # detail — and never the password.
        assert "AUDIT-FALLBACK" in logged
        assert "auth.set_credentials" in logged
        assert "17" in logged and "9" in logged
        assert "ada" in logged
        assert "never-logged" not in logged


def test_set_credentials_update_branch_audits_without_password():
    plaintext = "long-enough"
    user = User(
        id=55,
        public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        firstname="T",
        lastname="U",
    )
    existing_auth = Auth(
        id=9,
        public_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        username="old",
        password_hash="oldhash",
        user_id=55,
    )
    updated_auth = Auth(
        id=9,
        public_id=existing_auth.public_id,
        row_version="BBBB",
        created_datetime=None,
        modified_datetime=None,
        username="newuser",
        password_hash="$2b$12$newhash",
        user_id=55,
    )
    repo = MagicMock()
    repo.read_by_user_id.return_value = existing_auth
    repo.update_by_id.return_value = updated_auth
    token_repo = MagicMock()
    audit_repo = MagicMock()
    audit_repo.create.return_value = MagicMock()

    service = AuthService(repo=repo, token_repo=token_repo)
    from entities.user.business.service import UserService

    with patch.object(AuthService, "read_by_username", return_value=None), patch.object(
        UserService,
        "read_by_public_id",
        return_value=user,
    ), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        audit_svc = audit_cls.return_value
        with patch.object(service, "revoke_all_refresh_tokens_for_auth", return_value=3):
            result = service.set_credentials_for_user(
                user_public_id=str(user.public_id),
                username="newuser",
                password=plaintext,
            )
    assert result.username == "newuser"
    audit_svc.record_admin_action.assert_called_once()
    kwargs = audit_svc.record_admin_action.call_args.kwargs
    assert kwargs["action"] == "auth.set_credentials"
    assert kwargs["target_user_id"] == 55
    assert kwargs["detail"]["created"] is False
    assert kwargs["detail"]["refresh_tokens_revoked"] == 3
    assert plaintext not in json.dumps(kwargs["detail"])
    assert plaintext not in str(audit_svc.record_admin_action.call_args)
    stored_hash = repo.update_by_id.call_args[0][0].password_hash
    assert stored_hash.startswith("$2b$")
    assert stored_hash != plaintext
    assert bcrypt.checkpw(plaintext.encode(), stored_hash.encode())


def test_set_credentials_create_branch_audits_without_password():
    plaintext = "long-enough"
    user = User(
        id=56,
        public_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        firstname="V",
        lastname="W",
    )
    created_auth = Auth(
        id=10,
        public_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        username="brand",
        password_hash="$2b$12$x",
        user_id=None,
    )
    linked_auth = Auth(
        id=10,
        public_id=created_auth.public_id,
        row_version="BBBB",
        created_datetime=None,
        modified_datetime=None,
        username="brand",
        password_hash=created_auth.password_hash,
        user_id=56,
    )
    repo = MagicMock()
    repo.read_by_user_id.return_value = None
    repo.create.return_value = created_auth
    repo.update_by_id.return_value = linked_auth

    service = AuthService(repo=repo, token_repo=MagicMock())
    from entities.user.business.service import UserService

    with patch.object(AuthService, "read_by_username", return_value=None), patch.object(
        UserService,
        "read_by_public_id",
        return_value=user,
    ), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        audit_svc = audit_cls.return_value
        service.set_credentials_for_user(
            user_public_id=str(user.public_id),
            username="brand",
            password=plaintext,
        )
    audit_svc.record_admin_action.assert_called_once()
    kwargs = audit_svc.record_admin_action.call_args.kwargs
    assert kwargs["action"] == "auth.set_credentials"
    assert kwargs["target_user_id"] == 56
    assert kwargs["detail"]["created"] is True
    assert kwargs["detail"]["refresh_tokens_revoked"] == 0
    assert plaintext not in json.dumps(kwargs["detail"])
    stored_hash = repo.create.call_args.kwargs["password_hash"]
    assert stored_hash.startswith("$2b$")
    assert bcrypt.checkpw(plaintext.encode(), stored_hash.encode())


def test_user_role_create_records_assign_audit():
    repo = MagicMock()
    created = UserRole(
        id=1,
        public_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        row_version="x",
        created_datetime=None,
        modified_datetime=None,
        user_id=88,
        role_id=3,
        company_id=1,
    )
    repo.create.return_value = created
    service = UserRoleService(repo=repo)
    with patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        audit_svc = audit_cls.return_value
        with patch(
            "entities.user_role.business.service.current_company_id"
        ) as cid, patch(
            "entities.user_role.business.service.current_user_id"
        ) as uid:
            cid.get.return_value = 1
            uid.get.return_value = 17
            service.create(user_id=88, role_id=3)
    audit_svc.record_admin_action.assert_called_once_with(
        action="user_role.assign",
        target_user_id=88,
        detail={
            "role_id": 3,
            "user_role_public_id": str(created.public_id),
            "company_id": 1,
        },
    )


def test_user_role_delete_records_remove_audit():
    existing = UserRole(
        id=2,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version="x",
        created_datetime=None,
        modified_datetime=None,
        user_id=90,
        role_id=4,
        company_id=2,
    )
    repo = MagicMock()
    repo.delete_by_id.return_value = existing
    service = UserRoleService(repo=repo)
    with patch.object(service, "read_by_public_id", return_value=existing), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        audit_svc = audit_cls.return_value
        result = service.delete_by_public_id(str(existing.public_id))
    assert result is existing
    audit_svc.record_admin_action.assert_called_once_with(
        action="user_role.remove",
        target_user_id=90,
        detail={
            "role_id": 4,
            "user_role_public_id": str(existing.public_id),
            "company_id": 2,
        },
    )


def test_user_role_delete_missing_row_records_nothing():
    repo = MagicMock()
    service = UserRoleService(repo=repo)
    with patch.object(service, "read_by_public_id", return_value=None), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        assert service.delete_by_public_id("missing-id") is None
        audit_cls.return_value.record_admin_action.assert_not_called()
    repo.delete_by_id.assert_not_called()


def test_admin_audit_log_sql_set_nocount_and_no_cascade():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert "ON DELETE CASCADE" not in text.upper()
    proc_blocks = text.split("CREATE OR ALTER PROCEDURE")[1:]
    assert len(proc_blocks) == 3
    for block in proc_blocks:
        body = block.split("GO")[0]
        assert "SET NOCOUNT ON" in body


def test_admin_audit_log_sql_adds_no_foreign_keys():
    """Pass-1 P2 (U-585): an FK to dbo.User would make the pre-existing
    DeleteUserById fail for any user that was ever an admin actor/target, and
    ON DELETE SET NULL would rewrite history. The audit table must not ADD one."""
    import re

    text = SQL_PATH.read_text(encoding="utf-8")
    assert not re.search(r"ADD\s+CONSTRAINT[^;]*FOREIGN\s+KEY", text, re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Fix-round regression tests (Codex Pass-1, U-585)
# ---------------------------------------------------------------------------


def test_record_admin_action_sends_sanitized_json_to_repo():
    """P3: the redaction must hold at the persistence boundary, not just in
    the helper — assert on the Detail string the repo actually receives."""
    repo = MagicMock()
    repo.create.return_value = MagicMock()
    service = AdminAuditLogService(repo=repo)
    plaintext = "plain-secret-xyz"
    with actor_context(17, True):
        service.record_admin_action(
            action="auth.set_credentials",
            target_user_id=55,
            detail={
                "username": "ada",
                "password": plaintext,
                "nested": {"token": "tok-123", "keep": "yes"},
            },
        )
    kwargs = repo.create.call_args.kwargs
    assert kwargs["actor_user_id"] == 17
    assert kwargs["actor_is_system_admin"] is True
    assert kwargs["action"] == "auth.set_credentials"
    assert kwargs["target_user_id"] == 55
    assert plaintext not in kwargs["detail"]
    assert "tok-123" not in kwargs["detail"]
    assert json.loads(kwargs["detail"]) == {"username": "ada", "nested": {"keep": "yes"}}


def test_user_role_delete_lost_race_records_nothing():
    """P2: two concurrent removes — the second reads the row but the sproc
    deletes nothing (returns no row). No removal happened, so no audit row."""
    existing = UserRole(
        id=3,
        public_id="22222222-2222-2222-2222-222222222222",
        row_version="x",
        created_datetime=None,
        modified_datetime=None,
        user_id=91,
        role_id=5,
        company_id=1,
    )
    repo = MagicMock()
    repo.delete_by_id.return_value = None
    service = UserRoleService(repo=repo)
    with patch.object(service, "read_by_public_id", return_value=existing), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls:
        assert service.delete_by_public_id(str(existing.public_id)) is None
        audit_cls.return_value.record_admin_action.assert_not_called()
    repo.delete_by_id.assert_called_once_with(3)


def _admin_target_fixture(*, target_is_admin: bool):
    user = User(
        id=70,
        public_id="77777777-7777-7777-7777-777777777777",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        firstname="Root",
        lastname="Admin",
        is_system_admin=target_is_admin,
    )
    existing_auth = Auth(
        id=12,
        public_id="88888888-8888-8888-8888-888888888888",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        username="root",
        password_hash="oldhash",
        user_id=70,
    )
    repo = MagicMock()
    repo.read_by_user_id.return_value = existing_auth
    repo.update_by_id.return_value = existing_auth
    return user, repo


@pytest.mark.parametrize(
    "actor_is_admin,target_is_admin,expect_allowed",
    [
        (False, True, False),   # the P0: Users.can_update holder vs a system admin
        (True, True, True),     # admin resets admin — allowed
        (False, False, True),   # non-admin actor vs ordinary user — unchanged behavior
    ],
)
def test_set_credentials_system_admin_target_requires_system_admin_actor(
    actor_is_admin, target_is_admin, expect_allowed
):
    """P0 (U-585): a caller holding only Users.can_update must not be able to
    take over a system-admin account by resetting its password."""
    from entities.user.business.service import UserService

    user, repo = _admin_target_fixture(target_is_admin=target_is_admin)
    service = AuthService(repo=repo, token_repo=MagicMock())
    with patch.object(AuthService, "read_by_username", return_value=None), patch.object(
        UserService, "read_by_public_id", return_value=user
    ), actor_context(5, actor_is_admin), patch(
        "entities.admin_audit_log.business.service.AdminAuditLogService",
        autospec=True,
    ) as audit_cls, patch.object(
        service, "revoke_all_refresh_tokens_for_auth", return_value=0
    ):
        if expect_allowed:
            service.set_credentials_for_user(
                user_public_id=str(user.public_id), username="root", password="long-enough"
            )
            repo.update_by_id.assert_called_once()
            audit_cls.return_value.record_admin_action.assert_called_once()
        else:
            with pytest.raises(PermissionError):
                service.set_credentials_for_user(
                    user_public_id=str(user.public_id), username="root", password="long-enough"
                )
            repo.update_by_id.assert_not_called()
            repo.create.assert_not_called()
            audit_cls.return_value.record_admin_action.assert_not_called()


def test_assert_actor_can_manage_user_privilege_ceiling():
    """The ceiling itself, independent of the Auth flow that calls it."""
    from shared.authz.privilege import assert_actor_can_manage_user

    admin_target = SimpleNamespace(is_system_admin=True)
    plain_target = SimpleNamespace(is_system_admin=False)
    with actor_context(5, False):
        with pytest.raises(PermissionError, match="system administrator"):
            assert_actor_can_manage_user(admin_target)
        assert assert_actor_can_manage_user(plain_target) is None
    with actor_context(5, True):
        assert assert_actor_can_manage_user(admin_target) is None


def test_set_credentials_route_maps_permission_error_to_403():
    """The existing route keeps its Users.can_update gate (Gate-1 decision);
    the service's PermissionError must surface as 403, never 400 or 500."""
    from fastapi.testclient import TestClient

    import entities.auth.api.router as auth_router_mod
    from app import app
    from entities.auth.business.service import get_current_user_api

    async def _fake_admin_caller():
        # is_system_admin=True bypasses the module-permission DB resolve; the
        # service-level ceiling is what we are exercising, via the patch below.
        return {"sub": "caller", "user_id": 5, "is_system_admin": True, "company_id": 1}

    app.dependency_overrides[get_current_user_api] = _fake_admin_caller
    try:
        with patch.object(
            auth_router_mod.service,
            "set_credentials_for_user",
            side_effect=PermissionError("Only a system administrator can set credentials for a system administrator."),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/admin/auth/set-credentials/77777777-7777-7777-7777-777777777777",
                json={"username": "root", "password": "long-enough"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user_api, None)
    assert response.status_code == 403
    assert "system administrator" in response.json()["detail"].lower()
