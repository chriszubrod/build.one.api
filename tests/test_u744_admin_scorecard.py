"""U-744: GET /api/v1/admin/intelligence/bill-coding-scorecard - system-admin only."""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from shared.api import admin as admin_mod

PATH = "/api/v1/admin/intelligence/bill-coding-scorecard"


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.pop(get_current_user_api, None)


def _as_user(is_system_admin: bool):
    async def _fake():
        return {"sub": "s", "user_id": 17, "is_system_admin": is_system_admin, "company_id": 1}

    app.dependency_overrides[get_current_user_api] = _fake


def _row(created: datetime, pred: int = 10, **kw):
    base = {
        "prediction_id": 1,
        "bill_id": 2,
        "model_version": "u701b-text-v2",
        "coding_source": "primary",
        "coding_mode": "A",
        "intake_source": "pdf_drop",
        "pred_sub_cost_code_id": pred,
        "pred_project_id": 1,
        "final_sub_cost_code_id": 10,
        "final_project_id": 1,
        "finalized_datetime": datetime(2026, 10, 29, 12),
        "created_datetime": created,
    }
    base.update(kw)
    return base


@pytest.fixture
def rows(monkeypatch):
    calls = []
    data = [
        _row(datetime(2026, 10, 1)),
        _row(datetime(2026, 10, 29), pred=11),
        _row(datetime(2026, 10, 29), vendor_name="SECRET VENDOR", pred_bill_number="SECRET-NO"),
    ]

    def fake():
        calls.append(1)
        return data

    monkeypatch.setattr(admin_mod, "_bill_coding_scorecard_rows", fake)
    return calls


def test_route_requires_system_admin_dependency():
    route = next(r for r in admin_mod.router.routes if getattr(r, "path", "") == PATH)
    dep = inspect.signature(route.endpoint).parameters["current_user"].default
    assert isinstance(dep, DependsParam)
    assert dep.dependency.__qualname__ == "require_system_admin.<locals>._dependency"


def test_401_without_auth(client, rows):
    assert client.get(PATH).status_code == 401
    assert rows == []


def test_403_for_non_system_admin(client, rows):
    _as_user(False)
    assert client.get(PATH).status_code == 403
    assert rows == []


def test_envelope_and_counts(client, rows):
    _as_user(True)
    resp = client.get(PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data"}
    data = body["data"]
    assert data["n_finalized"] == 3
    assert data["overall"]["code"]["n"] == 3 and data["overall"]["code"]["right"] == 2
    assert data["since"] is None
    assert "caveat" in data and "backtest_reference" in data
    assert rows == [1]


def test_no_names_or_text_in_response(client, rows):
    _as_user(True)
    assert "SECRET" not in client.get(PATH).text


def test_since_filter(client, rows):
    _as_user(True)
    data = client.get(PATH, params={"since": "2026-10-29"}).json()["data"]
    assert data["since"] == "2026-10-29"
    assert data["n_finalized"] == 2
    assert data["overall"]["code"]["right"] == 1


def test_bad_since_is_422(client, rows):
    _as_user(True)
    assert client.get(PATH, params={"since": "not-a-date"}).status_code == 422
