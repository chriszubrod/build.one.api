"""U-590 — admin users list bulk enrichment (pure logic, services mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4


from shared.api.admin_users import (
    _build_user_summary,
    _enrich_identity,
    _enrich_roles,
    _summaries_for_all_users,
)


def _user(
    *,
    id: int,
    firstname: str = "F",
    lastname: str = "L",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        public_id=str(uuid4()),
        firstname=firstname,
        lastname=lastname,
        is_system_admin=False,
        is_agent=False,
        created_datetime="2026-01-01T00:00:00",
    )


def _auth(*, id: int, user_id: int, username: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        public_id=str(uuid4()),
        username=username,
        user_id=user_id,
    )


def _contact(*, id: int, user_id: int, email: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=id, user_id=user_id, email=email)


def _user_role(*, id: int, user_id: int, role_id: int, company_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        public_id=str(uuid4()),
        user_id=user_id,
        role_id=role_id,
        company_id=company_id,
    )


def _role(*, id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=id, public_id=str(uuid4()), name=name)


def test_bulk_identity_mapping_three_users_auth_present_or_absent():
    rows = [
        _build_user_summary(_user(id=1, firstname="A")),
        _build_user_summary(_user(id=2, firstname="B")),
        _build_user_summary(_user(id=3, firstname="C")),
    ]
    auths = [
        _auth(id=10, user_id=1, username="user_a"),
        _auth(id=20, user_id=3, username="user_c"),
    ]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls:
        auth_cls.return_value.read_all.return_value = auths
        contact_cls.return_value.read_all.return_value = []
        _enrich_identity(rows)

    assert auth_cls.return_value.read_by_user_id.call_count == 0
    assert rows[0]["username"] == "user_a" and rows[0]["has_auth"] is True
    assert rows[1]["username"] is None and rows[1]["has_auth"] is False
    assert rows[2]["username"] == "user_c" and rows[2]["has_auth"] is True


def test_bulk_first_email_skips_null_lower_id_contact():
    row = [_build_user_summary(_user(id=1))]
    contacts = [
        _contact(id=1, user_id=1, email=None),
        _contact(id=2, user_id=1, email="second@x"),
    ]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls:
        auth_cls.return_value.read_all.return_value = []
        contact_cls.return_value.read_all.return_value = contacts
        _enrich_identity(row)

    assert row[0]["email"] == "second@x"


def test_bulk_first_email_uses_lower_id_when_it_has_email():
    row = [_build_user_summary(_user(id=1))]
    contacts = [
        _contact(id=1, user_id=1, email="first@x"),
        _contact(id=2, user_id=1, email="other@x"),
    ]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls:
        auth_cls.return_value.read_all.return_value = []
        contact_cls.return_value.read_all.return_value = contacts
        _enrich_identity(row)

    assert row[0]["email"] == "first@x"


def test_bulk_roles_resorted_by_user_role_id_not_sproc_order():
    row = [_build_user_summary(_user(id=99))]
    roles = [
        _role(id=1, name="Alpha"),
        _role(id=2, name="Beta"),
        _role(id=3, name="Gamma"),
    ]
    # ReadUserRoles returns ORDER BY UserId, RoleId — so role_id 1, 2, 3 in that
    # order — while the per-user sproc (whose order we must reproduce) is ORDER BY
    # UserRole.Id. Chosen so that sort-by-role_id, sproc order and sort-by-id all
    # give DIFFERENT sequences: (role_id, ur_id) = (1,30), (2,10), (3,20) → by id = [2, 3, 1].
    user_roles = [
        _user_role(id=30, user_id=99, role_id=1),
        _user_role(id=10, user_id=99, role_id=2),
        _user_role(id=20, user_id=99, role_id=3),
    ]
    empty_row = [_build_user_summary(_user(id=100))]

    with patch("shared.api.admin_users.RoleService") as role_cls, patch(
        "shared.api.admin_users.UserRoleService"
    ) as ur_cls:
        role_cls.return_value.read_all.return_value = roles
        ur_cls.return_value.read_all.return_value = user_roles
        _enrich_roles(row)
        _enrich_roles(empty_row)

    assert [r["user_role_public_id"] for r in row[0]["roles"]] == [
        str(ur.public_id) for ur in sorted(user_roles, key=lambda ur: ur.id)
    ]
    assert [r["role_id"] for r in row[0]["roles"]] == [2, 3, 1]
    assert [r["role_name"] for r in row[0]["roles"]] == ["Beta", "Gamma", "Alpha"]
    assert empty_row[0]["roles"] == []


def test_bulk_duplicate_auth_lowest_id_wins():
    row = [_build_user_summary(_user(id=5))]
    # Order chosen so FIRST-wins → id 9, LAST-wins → id 7, and only lowest-Id
    # → id 4: a fixture where any wrong rule agrees with the right one proves
    # nothing (this one was green under a last-wins mutation before the reorder).
    auths = [
        _auth(id=9, user_id=5, username="from_nine"),
        _auth(id=4, user_id=5, username="from_four"),
        _auth(id=7, user_id=5, username="from_seven"),
    ]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls:
        auth_cls.return_value.read_all.return_value = auths
        contact_cls.return_value.read_all.return_value = []
        _enrich_identity(row)

    assert row[0]["username"] == "from_four"


def test_summaries_call_budget_five_reads_with_and_without_search():
    users = [_user(id=i) for i in range(1, 7)]
    read_counts: dict[str, int] = {}

    def _track(name: str, mock_instance: MagicMock) -> MagicMock:
        mock_instance.read_all = MagicMock(side_effect=lambda *a, **k: read_counts.__setitem__(name, read_counts.get(name, 0) + 1) or [])
        return mock_instance

    with patch("shared.api.admin_users.UserService") as user_cls, patch(
        "shared.api.admin_users.AuthService"
    ) as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls, patch(
        "shared.api.admin_users.RoleService"
    ) as role_cls, patch(
        "shared.api.admin_users.UserRoleService"
    ) as ur_cls:
        user_cls.return_value.read_all.return_value = users
        _track("auth", auth_cls.return_value)
        _track("contact", contact_cls.return_value)
        _track("role", role_cls.return_value)
        _track("user_role", ur_cls.return_value)

        _summaries_for_all_users(
            include_agents=False, search=None, limit=2, offset=0
        )
        assert read_counts == {"auth": 1, "contact": 1, "role": 1, "user_role": 1}
        assert user_cls.return_value.read_all.call_count == 1

        read_counts.clear()
        user_cls.return_value.read_all.reset_mock()
        auth_cls.return_value.read_all.reset_mock()
        contact_cls.return_value.read_all.reset_mock()
        role_cls.return_value.read_all.reset_mock()
        ur_cls.return_value.read_all.reset_mock()

        _summaries_for_all_users(
            include_agents=False, search="findme", limit=2, offset=0
        )
        assert user_cls.return_value.read_all.call_count == 1
        assert auth_cls.return_value.read_all.call_count == 1
        assert contact_cls.return_value.read_all.call_count == 1
        assert role_cls.return_value.read_all.call_count == 1
        assert ur_cls.return_value.read_all.call_count == 1


def test_response_row_key_order_pinned_after_bulk_enrichment():
    """Key ORDER is part of the wire contract. Drive the row through BOTH bulk
    enrichment passes (not just the bare builder) so a rewrite that rebuilt the
    dict, or appended keys, would be caught."""
    rows = [_build_user_summary(_user(id=1))]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls, patch("shared.api.admin_users.RoleService") as role_cls, patch(
        "shared.api.admin_users.UserRoleService"
    ) as ur_cls:
        auth_cls.return_value.read_all.return_value = [_auth(id=1, user_id=1, username="u1")]
        contact_cls.return_value.read_all.return_value = [_contact(id=1, user_id=1, email="u1@x")]
        role_cls.return_value.read_all.return_value = [_role(id=1, name="R")]
        ur_cls.return_value.read_all.return_value = [_user_role(id=1, user_id=1, role_id=1)]
        _enrich_identity(rows)
        _enrich_roles(rows)
    summary = rows[0]
    assert (summary["username"], summary["has_auth"], summary["email"], len(summary["roles"])) == ("u1", True, "u1@x", 1)
    expected = [
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
    assert list(summary.keys()) == expected


def test_bulk_identity_ignores_orphan_auth_rows_with_null_user_id():
    """An Auth row with UserId NULL (orphan) must neither crash the grouping nor
    be attributed to any user — and must not shadow a real row."""
    rows = [_build_user_summary(_user(id=7)), _build_user_summary(_user(id=8))]
    auths = [
        _auth(id=1, user_id=None, username="orphan"),
        _auth(id=2, user_id=7, username="seven"),
    ]
    with patch("shared.api.admin_users.AuthService") as auth_cls, patch(
        "shared.api.admin_users.ContactService"
    ) as contact_cls:
        auth_cls.return_value.read_all.return_value = auths
        contact_cls.return_value.read_all.return_value = [_contact(id=1, user_id=None, email="nobody@x")]
        _enrich_identity(rows)
    assert (rows[0]["username"], rows[0]["has_auth"]) == ("seven", True)
    assert (rows[1]["username"], rows[1]["has_auth"], rows[1]["email"]) == (None, False, None)
    assert rows[0]["email"] is None  # the orphan contact is attributed to no one

