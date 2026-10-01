"""U-585 — system-admin user list/detail routes and filter_and_page_users."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from shared.api.admin_users import filter_and_page_users, router


def _full_path(route_path: str) -> str:
    if route_path.startswith(router.prefix):
        return route_path
    prefix = router.prefix.rstrip("/")
    path = route_path if route_path.startswith("/") else f"/{route_path}"
    return f"{prefix}{path}"


def _endpoint_for(method: str, path: str):
    for route in router.routes:
        if getattr(route, "path", None) is None:
            continue
        if _full_path(route.path) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route.endpoint
    return None


def _assert_system_admin_dep(endpoint) -> None:
    sig = inspect.signature(endpoint)
    assert "current_user" in sig.parameters
    param = sig.parameters["current_user"]
    assert param.default is not inspect.Parameter.empty
    dep = param.default
    assert isinstance(dep, DependsParam)
    inner = dep.dependency
    assert inner.__qualname__ == "require_system_admin.<locals>._dependency", inner.__qualname__


USER_PUBLIC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

# The admin surface, as route templates. Both the route-gate test and the
# 401/403 tests derive from this one list.
ADMIN_PATHS = [
    "/api/v1/admin/users",
    "/api/v1/admin/user/{user_public_id}",
    "/api/v1/admin/user/{user_public_id}/audit",
]
_EXPECTED_ROUTES = [("GET", path) for path in ADMIN_PATHS]
_REQUEST_PATHS = [path.format(user_public_id=USER_PUBLIC_ID) for path in ADMIN_PATHS]


@pytest.mark.parametrize("method,path", _EXPECTED_ROUTES)
def test_admin_users_routes_require_system_admin(method: str, path: str):
    endpoint = _endpoint_for(method, path)
    assert endpoint is not None
    _assert_system_admin_dep(endpoint)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_auth_overrides():
    yield
    app.dependency_overrides.pop(get_current_user_api, None)


def _override_user(is_system_admin: bool):
    async def _fake_user():
        return {
            "sub": "admin-sub",
            "user_id": 17,
            "is_system_admin": is_system_admin,
            "company_id": 1,
        }

    app.dependency_overrides[get_current_user_api] = _fake_user


@pytest.fixture
def mock_admin_user_services():
    summary = {
        "public_id": USER_PUBLIC_ID,
        "id": 42,
        "firstname": "Ada",
        "lastname": "Lovelace",
        "is_system_admin": False,
        "is_agent": False,
        "username": "ada",
        "has_auth": True,
        "email": "ada@example.com",
        "roles": [],
        "created_datetime": "2026-01-01T00:00:00",
    }
    audit_row = {
        "id": 1,
        "public_id": str(uuid4()),
        "created_datetime": "2026-01-02T00:00:00",
        "actor_user_id": 17,
        "actor_is_system_admin": True,
        "action": "auth.set_credentials",
        "target_user_id": 42,
        "detail": {"username": "ada"},
    }
    with patch(
        "shared.api.admin_users._summaries_for_all_users",
        return_value=[summary],
    ), patch(
        "shared.api.admin_users._summary_for_public_id",
        return_value=summary,
    ), patch(
        "shared.api.admin_users.UserService",
    ) as user_cls, patch(
        "shared.api.admin_users.AdminAuditLogService",
    ) as audit_cls:
        user_svc = user_cls.return_value
        user_svc.read_by_public_id.return_value = MagicMock(id=42)
        user_svc.read_by_id.return_value = MagicMock(firstname="Chris", lastname="Z")

        audit_entry = MagicMock()
        audit_entry.actor_user_id = 17
        audit_entry.to_dict.return_value = dict(audit_row)
        audit_cls.return_value.read_for_user.return_value = [audit_entry]

        yield {"summary": summary}


@pytest.mark.parametrize("path", _REQUEST_PATHS)
def test_admin_users_routes_401_without_auth(client, clear_auth_overrides, path):
    response = client.get(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path", _REQUEST_PATHS)
def test_admin_users_routes_403_for_non_system_admin(
    client, clear_auth_overrides, path
):
    _override_user(is_system_admin=False)
    response = client.get(path)
    assert response.status_code == 403
    assert "System administrator" in response.json()["detail"]


def test_list_users_200_envelope(client, clear_auth_overrides, mock_admin_user_services):
    _override_user(is_system_admin=True)
    response = client.get("/api/v1/admin/users")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["public_id"] == USER_PUBLIC_ID


def test_get_user_200_envelope(client, clear_auth_overrides, mock_admin_user_services):
    _override_user(is_system_admin=True)
    response = client.get(f"/api/v1/admin/user/{USER_PUBLIC_ID}")
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "ada"


def test_get_user_audit_200_envelope(client, clear_auth_overrides, mock_admin_user_services):
    _override_user(is_system_admin=True)
    response = client.get(f"/api/v1/admin/user/{USER_PUBLIC_ID}/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["action"] == "auth.set_credentials"
    assert body["data"][0]["actor_name"] == "Chris Z"


def test_list_users_enriches_only_the_page_slice(client, clear_auth_overrides):
    """With no search term the page is resolved off the User rows first, so the
    per-user auth / contact / role lookups run `limit` times — not once per row
    in the table — while count and payload shape stay exactly as before."""
    users = [
        SimpleNamespace(
            id=i,
            public_id=f"0000000{i}-0000-0000-0000-00000000000{i}",
            firstname=f"F{i}",
            lastname=f"L{i}",
            is_system_admin=False,
            is_agent=False,
            created_datetime="2026-01-01T00:00:00",
        )
        for i in range(1, 7)
    ]
    with patch("shared.api.admin_users.UserService") as user_cls, patch(
        "shared.api.admin_users.AuthService"
    ) as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls, patch(
        "shared.api.admin_users.RoleService"
    ) as role_cls, patch(
        "shared.api.admin_users.UserRoleService"
    ) as user_role_cls:
        user_cls.return_value.read_all.return_value = users
        auth_cls.return_value.read_by_user_id.return_value = None
        contact_cls.return_value.read_by_user_id.return_value = []
        role_cls.return_value.read_all.return_value = []
        user_role_cls.return_value.read_all_by_user_id.return_value = []

        _override_user(is_system_admin=True)
        response = client.get("/api/v1/admin/users?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 6
    assert [row["id"] for row in body["data"]] == [1, 2]
    # `roles` is still present on every returned row, in its original position.
    assert list(body["data"][0]) == [
        "public_id",
        "id",
        "firstname",
        "lastname",
        "is_system_admin",
        "is_agent",
        "username",
        "has_auth",
        "email",
        "roles",
        "created_datetime",
    ]
    assert all(row["roles"] == [] for row in body["data"])
    # The whole point: bounded by `limit`, not by the size of the table.
    assert auth_cls.return_value.read_by_user_id.call_count == 2
    assert contact_cls.return_value.read_by_user_id.call_count == 2
    assert user_role_cls.return_value.read_all_by_user_id.call_count == 2


@pytest.fixture
def user_summaries_for_filter():
    return [
        {
            "id": 1,
            "firstname": "Zed",
            "lastname": "Zulu",
            "username": "other1",
            "email": "z@example.com",
        },
        {
            "id": 2,
            "firstname": "Amy",
            "lastname": "Alpha",
            "username": "findme_one",
            "email": "a@example.com",
        },
        {
            "id": 3,
            "firstname": "Ben",
            "lastname": "Beta",
            "username": "findme_two",
            "email": "b@example.com",
        },
        {
            "id": 4,
            "firstname": "Cara",
            "lastname": "findme",
            "email": "c@example.com",
            "username": None,
        },
        {
            "id": 5,
            "firstname": None,
            "lastname": "Gamma",
            "username": "gamma",
            "email": "findme@corp.example.com",
        },
        {
            "id": 6,
            "firstname": "Dan",
            "lastname": None,
            "username": "delta",
            "email": None,
        },
    ]


def test_filter_and_page_users_search_matches_expected_set(user_summaries_for_filter):
    page, total = filter_and_page_users(user_summaries_for_filter, "findme", limit=50, offset=0)
    matched_ids = {row["id"] for row in page}
    assert matched_ids == {2, 3, 4, 5}
    assert total == 4


def test_filter_and_page_users_paging(user_summaries_for_filter):
    _, total = filter_and_page_users(user_summaries_for_filter, "findme", limit=50, offset=0)
    assert total == 4
    page, _ = filter_and_page_users(user_summaries_for_filter, "findme", limit=2, offset=2)
    assert [row["id"] for row in page] == [4, 5]


def test_filter_and_page_users_empty_search_returns_all(user_summaries_for_filter):
    page, total = filter_and_page_users(user_summaries_for_filter, "", limit=100, offset=0)
    assert total == len(user_summaries_for_filter)
    assert len(page) == len(user_summaries_for_filter)


def test_filter_and_page_users_none_fields_do_not_raise(user_summaries_for_filter):
    page, total = filter_and_page_users(user_summaries_for_filter, "gamma", limit=10, offset=0)
    assert total == 1
    assert page[0]["id"] == 5
