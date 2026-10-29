"""U-701d: GET /api/v1/admin/llm/spend — system-admin read of dbo.LlmDailySpend."""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

from app import app
from entities.auth.business.service import get_current_user_api
from shared.api import admin as admin_mod

PATH = "/api/v1/admin/llm/spend"


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for var in ("FOUNDRY_SPEND_ALERT_USD", "FOUNDRY_DAILY_SPEND_CAP_USD", "CLAUDE_DAILY_SPEND_CAP_USD"):
        monkeypatch.delenv(var, raising=False)
    yield
    app.dependency_overrides.pop(get_current_user_api, None)


def _as_user(is_system_admin: bool):
    async def _fake():
        return {"sub": "s", "user_id": 17, "is_system_admin": is_system_admin, "company_id": 1}

    app.dependency_overrides[get_current_user_api] = _fake


def _row(d: date, cost: str, calls: int):
    return SimpleNamespace(SpendDate=d, CostUsd=Decimal(cost), CallCount=calls)


@pytest.fixture
def rows(monkeypatch):
    today = datetime.now(timezone.utc).date()
    data = {
        "anthropic": [_row(today, "1.250000", 10), _row(today - timedelta(days=2), "0.500000", 3)],
        "foundry": [_row(today, "0.040000", 7), _row(today - timedelta(days=1), "0.010000", 2)],
    }
    calls = []

    def fake(from_date, to_date, provider):
        calls.append((from_date, to_date, provider))
        return data[provider]

    monkeypatch.setattr(admin_mod, "_llm_spend_rows", fake)
    return SimpleNamespace(today=today, calls=calls)


def test_route_requires_system_admin_dependency():
    route = next(r for r in admin_mod.router.routes if getattr(r, "path", "") == PATH)
    dep = inspect.signature(route.endpoint).parameters["current_user"].default
    assert isinstance(dep, DependsParam)
    assert dep.dependency.__qualname__ == "require_system_admin.<locals>._dependency"


def test_401_without_auth(client, rows):
    assert client.get(PATH).status_code == 401
    assert rows.calls == []


def test_403_for_non_system_admin(client, rows):
    _as_user(False)
    resp = client.get(PATH)
    assert resp.status_code == 403
    assert rows.calls == []


def test_merges_providers_and_zero_fills(client, rows):
    _as_user(True)
    resp = client.get(PATH, params={"days": 4})
    assert resp.status_code == 200
    body = resp.json()["data"]
    t = rows.today
    assert [d["spend_date"] for d in body["days"]] == [
        (t - timedelta(days=i)).isoformat() for i in (3, 2, 1, 0)
    ]
    by = {d["spend_date"]: d for d in body["days"]}
    assert by[t.isoformat()] == {
        "spend_date": t.isoformat(), "anthropic_usd": 1.25, "anthropic_calls": 10,
        "foundry_usd": 0.04, "foundry_calls": 7,
    }
    assert by[(t - timedelta(days=1)).isoformat()]["anthropic_usd"] == 0.0
    assert by[(t - timedelta(days=1)).isoformat()]["foundry_calls"] == 2
    assert by[(t - timedelta(days=2)).isoformat()]["anthropic_calls"] == 3
    assert by[(t - timedelta(days=3)).isoformat()] == {
        "spend_date": (t - timedelta(days=3)).isoformat(), "anthropic_usd": 0.0,
        "anthropic_calls": 0, "foundry_usd": 0.0, "foundry_calls": 0,
    }
    assert body["claude_cap_usd"] == 6.45
    assert body["foundry_alert_usd"] == 2.0
    assert body["foundry_cap_usd"] is None
    assert sorted(p for _, _, p in rows.calls) == ["anthropic", "foundry"]
    assert all(f == t - timedelta(days=3) and to == t for f, to, _ in rows.calls)


def test_default_is_14_days(client, rows):
    _as_user(True)
    body = client.get(PATH).json()["data"]
    assert len(body["days"]) == 14


@pytest.mark.parametrize("days,status", [(0, 422), (91, 422), (1, 200), (90, 200), ("x", 422)])
def test_days_bounds(client, rows, days, status):
    _as_user(True)
    resp = client.get(PATH, params={"days": days})
    assert resp.status_code == status
    if status == 200:
        assert len(resp.json()["data"]["days"]) == days


def test_configured_cap_and_disabled_alert(client, rows, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "3.5")
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0")
    _as_user(True)
    body = client.get(PATH, params={"days": 1}).json()["data"]
    assert body["foundry_cap_usd"] == 3.5
    assert body["foundry_alert_usd"] is None
