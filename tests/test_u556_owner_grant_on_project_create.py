"""
U-556 Part 2 (Option A) — ProjectService.create seeds the Owner's UserProject row.

Why this exists: review-notification routing buckets 'Project Manager' -> To and
'Owner' -> Cc. A project with no Owner row emits an empty Cc on every review
email it ever generates, and the Cc must exist BEFORE the project's first bill.
Bill 40638 on project 203 drafted with an empty To AND Cc for exactly this
reason.

The load-bearing contract is not "it writes a row" — it is:
  1. it writes a row tagged RoleId = Owner (a NULL RoleId drops the user from
     routing entirely, so an untagged row is as broken as no row),
  2. the Owner set is resolved from data, scoped to the PROJECT's company,
  3. it NEVER lets a grant failure roll back project creation,
  4. it is idempotent against an already-granted owner.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from entities.project.business.service import (
    ProjectService,
    _grant_owner_project_access,
)


OWNER_USER_ID = 20
OWNER_ROLE_ID = 2
PROJECT_ID = 9001


def _conn_returning(rows):
    """Build a get_connection() context manager whose cursor returns `rows`."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx, cursor


# ─── 1. The happy path writes a row TAGGED Owner ──────────────────────────

def test_grant_writes_a_userproject_row_tagged_with_the_owner_role():
    ctx, _ = _conn_returning([(OWNER_USER_ID, OWNER_ROLE_ID)])
    svc = MagicMock()
    svc.read_by_project_id.return_value = []

    with patch("shared.database.get_connection", return_value=ctx), \
         patch("entities.user_project.business.service.UserProjectService", return_value=svc):
        _grant_owner_project_access(PROJECT_ID)

    svc.create.assert_called_once()
    kwargs = svc.create.call_args.kwargs
    assert kwargs["user_id"] == OWNER_USER_ID
    assert kwargs["project_id"] == PROJECT_ID
    # The whole point: an untagged row is invisible to review routing.
    assert kwargs["role_id"] == OWNER_ROLE_ID, (
        "RoleId must be the Owner role — a NULL RoleId silently drops the "
        "user from review-notification Cc routing"
    )


def test_every_company_owner_is_granted_not_just_the_first():
    ctx, _ = _conn_returning([(20, OWNER_ROLE_ID), (55, OWNER_ROLE_ID)])
    svc = MagicMock()
    svc.read_by_project_id.return_value = []

    with patch("shared.database.get_connection", return_value=ctx), \
         patch("entities.user_project.business.service.UserProjectService", return_value=svc):
        _grant_owner_project_access(PROJECT_ID)

    granted = {c.kwargs["user_id"] for c in svc.create.call_args_list}
    assert granted == {20, 55}


# ─── 2. The Owner set is resolved from data, scoped to the project ────────

def test_owner_lookup_is_scoped_to_the_projects_own_company():
    """
    Must join through the Project's CompanyId rather than reading the caller's
    ambient company — a connector/system path carries no company context.
    """
    ctx, cursor = _conn_returning([])

    with patch("shared.database.get_connection", return_value=ctx):
        _grant_owner_project_access(PROJECT_ID)

    sql, params = cursor.execute.call_args.args
    normalized = " ".join(sql.split()).lower()
    assert "dbo.[userrole]" in normalized.replace(" ", "") or "userrole" in normalized
    assert "companyid" in normalized.replace(" ", "")
    assert "p.[id] = ?" in normalized or "p.[id]=?" in normalized.replace(" ", "")
    # Parameterised, never interpolated.
    assert params == ("Owner", PROJECT_ID)


def test_no_company_owner_is_a_logged_noop_not_a_crash(caplog):
    ctx, _ = _conn_returning([])
    with caplog.at_level(logging.INFO), \
         patch("shared.database.get_connection", return_value=ctx):
        _grant_owner_project_access(PROJECT_ID)
    assert "project.owner_grant.no_owner" in caplog.text


# ─── 3. Failure isolation — the contract that protects project creation ───

def test_a_grant_failure_never_propagates():
    """A Project must remain creatable when the grant cannot be written."""
    ctx, _ = _conn_returning([(OWNER_USER_ID, OWNER_ROLE_ID)])
    svc = MagicMock()
    svc.read_by_project_id.return_value = []
    svc.create.side_effect = RuntimeError("UQ_UserProject_UserId_ProjectId violation")

    with patch("shared.database.get_connection", return_value=ctx), \
         patch("entities.user_project.business.service.UserProjectService", return_value=svc):
        _grant_owner_project_access(PROJECT_ID)  # must not raise


def test_a_lookup_failure_never_propagates():
    with patch("shared.database.get_connection", side_effect=RuntimeError("db down")):
        _grant_owner_project_access(PROJECT_ID)  # must not raise


def test_one_owner_failing_does_not_deny_the_others():
    ctx, _ = _conn_returning([(20, OWNER_ROLE_ID), (55, OWNER_ROLE_ID)])
    svc = MagicMock()
    svc.read_by_project_id.return_value = []
    svc.create.side_effect = [RuntimeError("boom"), MagicMock()]

    with patch("shared.database.get_connection", return_value=ctx), \
         patch("entities.user_project.business.service.UserProjectService", return_value=svc):
        _grant_owner_project_access(PROJECT_ID)

    assert svc.create.call_count == 2, "the second owner must still be attempted"


def test_project_create_still_returns_the_project_when_the_grant_blows_up():
    """End-to-end contract: create() returns normally even if the hook fails."""
    project = MagicMock()
    project.id = PROJECT_ID
    repo = MagicMock()
    repo.read_by_name.return_value = None
    repo.create.return_value = project

    with patch(
        "entities.project.business.service._grant_owner_project_access",
        side_effect=RuntimeError("hook exploded"),
    ):
        with pytest.raises(RuntimeError):
            # Guard the guard: if the hook is ever called OUTSIDE its own
            # try/except this is what the caller would see.
            ProjectService(repo=repo).create(
                name="X", description="", status="active"
            )

    # And with the real (isolated) hook, the same failure is absorbed.
    repo.create.return_value = project
    ctx, _ = _conn_returning([])
    with patch("shared.database.get_connection", side_effect=RuntimeError("db down")):
        result = ProjectService(repo=repo).create(
            name="X", description="", status="active"
        )
    assert result is project


# ─── 4. Idempotency ───────────────────────────────────────────────────────

def test_an_already_granted_owner_is_skipped():
    existing = MagicMock()
    existing.user_id = OWNER_USER_ID
    ctx, _ = _conn_returning([(OWNER_USER_ID, OWNER_ROLE_ID)])
    svc = MagicMock()
    svc.read_by_project_id.return_value = [existing]

    with patch("shared.database.get_connection", return_value=ctx), \
         patch("entities.user_project.business.service.UserProjectService", return_value=svc):
        _grant_owner_project_access(PROJECT_ID)

    svc.create.assert_not_called()


# ─── 5. The hook is actually wired into create() ──────────────────────────

def test_create_invokes_the_grant_with_the_new_projects_id():
    project = MagicMock()
    project.id = PROJECT_ID
    repo = MagicMock()
    repo.read_by_name.return_value = None
    repo.create.return_value = project

    with patch("entities.project.business.service._grant_owner_project_access") as hook:
        ProjectService(repo=repo).create(name="N", description="", status="active")

    hook.assert_called_once_with(PROJECT_ID)


def test_create_does_not_call_the_grant_when_the_repo_returns_nothing():
    repo = MagicMock()
    repo.read_by_name.return_value = None
    repo.create.return_value = None

    with patch("entities.project.business.service._grant_owner_project_access") as hook:
        ProjectService(repo=repo).create(name="N", description="", status="active")

    hook.assert_not_called()
