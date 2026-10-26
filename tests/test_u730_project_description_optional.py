"""U-730: a Project with an empty description must still be updatable.

`ProjectUpdate.description` was a required `str` with `min_length=1`, but the
column is nullable and 135 of the 140 production rows hold an empty string. A
read-modify-write PUT therefore failed two ways — send the stored `""` and
pydantic raised `string_too_short`, omit it and pydantic raised `missing` — so
no caller could change a project's abbreviation without inventing a
description. Verified live 2026-10-26 on project 14 (`GWT - 5250 Granny White
Trace`), whose abbreviation had to be set through the sproc instead.

Pure-logic, no live DB: the schemas are exercised directly and the service is
driven with a stub repo.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from entities.project.api.schemas import ProjectCreate, ProjectUpdate
from entities.project.business.service import ProjectService

_RV = "AAAAAAAiLo0="


class _StubRepo:
    """Records what the service hands the persistence layer."""

    def __init__(self, existing=None, by_name=None):
        self._existing = existing
        self._by_name = by_name
        self.updated = None
        self.created_kwargs = None
        self.update_calls = 0

    # update path
    def read_by_public_id(self, *_a, **_k):
        return self._existing

    def update_by_id(self, project):
        self.update_calls += 1
        self.updated = project
        return project

    # create path
    def read_by_name(self, *_a, **_k):
        return self._by_name

    def create(self, **kwargs):
        self.created_kwargs = kwargs
        # id=None so the service skips _grant_owner_project_access (needs a DB)
        return SimpleNamespace(id=None, **{k: v for k, v in kwargs.items() if k != "tenant_id"})


def _stored_project():
    return SimpleNamespace(
        id=14,
        public_id="298a6256-3cb7-47cf-934c-9f09aeb84496",
        row_version=_RV,
        name="GWT - 5250 Granny White Trace",
        description="",           # the shape 135 of 140 prod rows actually hold
        status="active",
        customer_id=6,
        abbreviation=None,
        notes=None,
        is_cost_plus=True,
    )


# --------------------------------------------------------------------------- #
# schema: the two shapes that used to 422
# --------------------------------------------------------------------------- #

def test_update_accepts_the_stored_empty_description():
    """Round-tripping a project whose description is "" must validate."""
    body = ProjectUpdate(row_version=_RV, name="N", description="", status="active")
    assert body.description == ""


def test_update_accepts_an_omitted_description():
    body = ProjectUpdate(row_version=_RV, name="N", status="active")
    assert body.description is None


def test_create_accepts_an_omitted_description():
    body = ProjectCreate(name="N", status="active")
    assert body.description is None


def test_create_accepts_the_stored_empty_description():
    assert ProjectCreate(name="N", description="", status="active").description == ""


@pytest.mark.parametrize("model", [ProjectCreate, ProjectUpdate])
def test_description_is_not_required(model):
    """Pins the regression: description must never go back to required."""
    assert model.model_fields["description"].is_required() is False


@pytest.mark.parametrize("model", [ProjectCreate, ProjectUpdate])
def test_description_still_bounded_at_500(model):
    """Relaxing min_length must not drop the column-width ceiling.

    Asserts the error is the description length specifically — a bare
    `raises(ValidationError)` would also pass if some unrelated required
    field were added later, leaving max_length unpinned.
    """
    kwargs = {"name": "N", "status": "active", "description": "x" * 501}
    if model is ProjectUpdate:
        kwargs["row_version"] = _RV

    with pytest.raises(ValidationError) as exc:
        model(**kwargs)

    errors = exc.value.errors()
    assert [e["loc"] for e in errors] == [("description",)], errors
    assert errors[0]["type"] == "string_too_long", errors


# --------------------------------------------------------------------------- #
# service: the read-merge that makes an omitted field safe
# --------------------------------------------------------------------------- #

def test_update_only_abbreviation_leaves_every_other_field_untouched():
    """The live case: set the abbreviation, change nothing else."""
    stored = _stored_project()
    repo = _StubRepo(existing=stored)
    svc = ProjectService(repo=repo)

    svc.update_by_public_id(
        public_id=str(stored.public_id), row_version=_RV, abbreviation="GWT"
    )

    got = repo.updated
    assert got.abbreviation == "GWT"
    assert got.description == ""          # preserved, not nulled
    assert got.notes is None
    assert got.name == "GWT - 5250 Granny White Trace"
    assert got.status == "active"
    assert got.customer_id == 6
    assert got.is_cost_plus is True


def test_update_with_explicit_empty_description_stores_empty_not_none():
    stored = _stored_project()
    stored.description = "Renovation"
    repo = _StubRepo(existing=stored)

    ProjectService(repo=repo).update_by_public_id(
        public_id="p", row_version=_RV, description=""
    )

    assert repo.updated.description == ""


def test_update_with_a_new_description_overwrites():
    repo = _StubRepo(existing=_stored_project())
    ProjectService(repo=repo).update_by_public_id(
        public_id="p", row_version=_RV, description="Renovation"
    )
    assert repo.updated.description == "Renovation"


def test_update_of_an_unknown_public_id_returns_none_without_touching_the_repo():
    """Used to hand the repo None and raise, surfacing as a 500 not a 404."""
    repo = _StubRepo(existing=None)

    result = ProjectService(repo=repo).update_by_public_id(
        public_id="does-not-exist", row_version=_RV, abbreviation="X"
    )

    assert result is None
    assert repo.update_calls == 0


# --------------------------------------------------------------------------- #
# service: create normalises an omitted description
# --------------------------------------------------------------------------- #

def test_create_without_a_description_persists_empty_string_not_null():
    """All 140 prod rows hold a string; don't mint the first NULL."""
    repo = _StubRepo(by_name=None)

    ProjectService(repo=repo).create(name="NEW - 1 Some Rd", status="active")

    assert repo.created_kwargs["description"] == ""


def test_create_passes_a_supplied_description_through():
    repo = _StubRepo(by_name=None)
    ProjectService(repo=repo).create(
        name="NEW - 1 Some Rd", description="Renovation", status="active"
    )
    assert repo.created_kwargs["description"] == "Renovation"


# --------------------------------------------------------------------------- #
# router: a None service result must become 404, not a 200 with a null body
# --------------------------------------------------------------------------- #

@pytest.fixture
def project_client():
    """The PUT route with authz stubbed out; ProcessEngine is patched per test."""
    import inspect

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shared.api.errors import install_error_handlers
    from entities.project.api.router import (
        router as project_router,
        update_project_by_public_id_router,
    )

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(project_router)
    dep = inspect.signature(update_project_by_public_id_router).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[dep] = lambda: {"id": 33, "tenant_id": 1, "sub": "agent"}
    return TestClient(app, raise_server_exceptions=False)


_PUT_BODY = {"row_version": _RV, "name": "GWT - 5250 Granny White Trace", "status": "active"}


def test_put_unknown_public_id_returns_404_not_a_null_200(project_client):
    """The workflow reports success=True with no data when the row is absent.

    Without the router's explicit check that surfaces as 200 + a null body and
    the caller believes a non-existent project was updated.
    """
    from unittest.mock import patch

    engine = MagicMock()
    engine.return_value.execute_synchronous.return_value = {"success": True}

    with patch("entities.project.api.router.ProcessEngine", engine):
        resp = project_client.put(
            "/api/v1/update/project/00000000-0000-0000-0000-0000000000ff", json=_PUT_BODY
        )

    assert resp.status_code == 404, resp.text


def test_put_a_real_project_still_returns_200_with_the_row(project_client):
    """The 404 guard must not swallow a genuine update."""
    from unittest.mock import patch

    engine = MagicMock()
    engine.return_value.execute_synchronous.return_value = {
        "success": True,
        "data": {"public_id": "298a6256-3cb7-47cf-934c-9f09aeb84496", "abbreviation": "GWT"},
    }

    with patch("entities.project.api.router.ProcessEngine", engine):
        resp = project_client.put(
            "/api/v1/update/project/298a6256-3cb7-47cf-934c-9f09aeb84496", json=_PUT_BODY
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["abbreviation"] == "GWT"


def test_put_omitting_description_reaches_the_workflow_as_none(project_client):
    """End-to-end on the shape that used to 422 before it ever got here."""
    from unittest.mock import patch

    engine = MagicMock()
    engine.return_value.execute_synchronous.return_value = {
        "success": True,
        "data": {"public_id": "p"},
    }

    with patch("entities.project.api.router.ProcessEngine", engine):
        resp = project_client.put(
            "/api/v1/update/project/298a6256-3cb7-47cf-934c-9f09aeb84496",
            json={**_PUT_BODY, "abbreviation": "GWT"},
        )

    assert resp.status_code == 200, resp.text
    context = engine.return_value.execute_synchronous.call_args[0][0]
    assert context.payload["description"] is None
    assert context.payload["abbreviation"] == "GWT"
