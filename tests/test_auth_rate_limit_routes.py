import pytest
from fastapi.testclient import TestClient

import entities.auth.api.router as auth_router
from app import app
from entities.auth.business.model import Auth, AuthToken, RefreshToken
from shared.rate_limit import (
    LOGIN_BUCKET_CAPACITY,
    LOGIN_REFILL_INTERVAL_SECONDS,
    TokenBucketStore,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fresh_rate_limiter(monkeypatch):
    limiter = TokenBucketStore(
        capacity=LOGIN_BUCKET_CAPACITY,
        refill_interval_seconds=LOGIN_REFILL_INTERVAL_SECONDS,
    )
    monkeypatch.setattr(auth_router, "login_rate_limiter", limiter)
    return limiter


def _mock_login_success(*, username, password):
    auth = Auth(
        id=1,
        public_id="00000000-0000-0000-0000-000000000001",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        username=username,
        password_hash=None,
        user_id=1,
    )
    access = AuthToken(access_token="access", token_type="bearer", expires_in=900)
    refresh = RefreshToken(refresh_token="refresh", token_type="bearer", expires_in=86400)
    return auth, access, refresh


def test_successful_mobile_login_never_throttled(client, fresh_rate_limiter, monkeypatch):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_success)
    payload = {"username": "agent@example.com", "password": "password123"}
    for _ in range(LOGIN_BUCKET_CAPACITY + 5):
        response = client.post("/api/v1/mobile/auth/login", json=payload)
        assert response.status_code == 200, response.text


def test_failing_mobile_login_throttles_after_capacity(client, fresh_rate_limiter, monkeypatch):
    def _fail(*, username, password):
        raise ValueError("Invalid credentials.")

    monkeypatch.setattr(auth_router.service, "login", _fail)
    payload = {"username": "attacker@example.com", "password": "wrongpass1"}

    for _ in range(LOGIN_BUCKET_CAPACITY):
        response = client.post("/api/v1/mobile/auth/login", json=payload)
        assert response.status_code == 400, response.text

    response = client.post("/api/v1/mobile/auth/login", json=payload)
    assert response.status_code == 429, response.text
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


def test_rate_limit_429_not_swallowed_as_500(client, fresh_rate_limiter, monkeypatch):
    def _fail(*, username, password):
        raise ValueError("Invalid credentials.")

    monkeypatch.setattr(auth_router.service, "login", _fail)
    payload = {"username": "blocked@example.com", "password": "wrongpass1"}

    for _ in range(LOGIN_BUCKET_CAPACITY):
        client.post("/api/v1/mobile/auth/login", json=payload)

    response = client.post("/api/v1/mobile/auth/login", json=payload)
    assert response.status_code == 429
    assert response.status_code != 500
    assert "Retry-After" in response.headers
