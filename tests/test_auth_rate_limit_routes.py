import pytest
from fastapi.testclient import TestClient

import entities.auth.api.router as auth_router
from app import app
from entities.auth.business.model import Auth, AuthToken, RefreshToken
from shared.rate_limit import (
    IP_BUCKET_CAPACITY,
    IP_REFILL_INTERVAL_SECONDS,
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


@pytest.fixture
def fresh_ip_rate_limiter(monkeypatch):
    limiter = TokenBucketStore(
        capacity=IP_BUCKET_CAPACITY,
        refill_interval_seconds=IP_REFILL_INTERVAL_SECONDS,
    )
    monkeypatch.setattr(auth_router, "login_ip_rate_limiter", limiter)
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


def _mock_login_failure(*, username, password):
    raise ValueError("Invalid credentials.")


def test_successful_mobile_login_never_throttled(client, fresh_rate_limiter, monkeypatch):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_success)
    payload = {"username": "agent@example.com", "password": "password123"}
    for _ in range(LOGIN_BUCKET_CAPACITY + 5):
        response = client.post("/api/v1/mobile/auth/login", json=payload)
        assert response.status_code == 200, response.text


def test_failing_mobile_login_throttles_after_capacity(client, fresh_rate_limiter, monkeypatch):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
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
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    payload = {"username": "blocked@example.com", "password": "wrongpass1"}

    for _ in range(LOGIN_BUCKET_CAPACITY):
        client.post("/api/v1/mobile/auth/login", json=payload)

    response = client.post("/api/v1/mobile/auth/login", json=payload)
    assert response.status_code == 429
    assert response.status_code != 500
    assert "Retry-After" in response.headers


@pytest.mark.parametrize(
    "login_path", ["/api/v1/mobile/auth/login", "/api/v1/auth/login"]
)
def test_credential_spray_trips_ip_key(
    login_path, client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    headers = {"X-Client-IP": "6.6.6.6, 9.9.9.9"}
    for i in range(IP_BUCKET_CAPACITY):
        response = client.post(
            login_path,
            json={"username": f"spray{i}@example.com", "password": "wrongpass1"},
            headers=headers,
        )
        assert response.status_code == 400, response.text
    response = client.post(
        login_path,
        json={"username": f"spray{IP_BUCKET_CAPACITY}@example.com", "password": "wrongpass1"},
        headers=headers,
    )
    assert response.status_code == 429, response.text
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


def test_ip_keys_are_independent(
    client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    trip_headers = {"X-Client-IP": "6.6.6.6, 9.9.9.9"}
    for i in range(IP_BUCKET_CAPACITY + 1):
        client.post(
            "/api/v1/mobile/auth/login",
            json={"username": f"trip{i}@example.com", "password": "wrongpass1"},
            headers=trip_headers,
        )
    response = client.post(
        "/api/v1/mobile/auth/login",
        json={"username": "fresh@example.com", "password": "wrongpass1"},
        headers={"X-Client-IP": "6.6.6.6, 8.8.8.8"},
    )
    assert response.status_code == 400, response.text


def test_success_refunds_ip_bucket(
    client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_success)
    payload = {"username": "agent@example.com", "password": "password123"}
    headers = {"X-Client-IP": "1.2.3.4"}
    for _ in range(IP_BUCKET_CAPACITY + 5):
        response = client.post(
            "/api/v1/mobile/auth/login",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 200, response.text


def test_username_denied_attempts_do_not_consume_ip_tokens(
    client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    headers = {"X-Client-IP": "5.5.5.5"}
    username = "hammered@example.com"
    payload = {"username": username, "password": "wrongpass1"}

    for _ in range(LOGIN_BUCKET_CAPACITY):
        response = client.post(
            "/api/v1/mobile/auth/login", json=payload, headers=headers
        )
        assert response.status_code == 400, response.text

    for _ in range(3):
        response = client.post(
            "/api/v1/mobile/auth/login", json=payload, headers=headers
        )
        assert response.status_code == 429, response.text

    remaining_ip_attempts = IP_BUCKET_CAPACITY - LOGIN_BUCKET_CAPACITY
    for i in range(remaining_ip_attempts):
        response = client.post(
            "/api/v1/mobile/auth/login",
            json={"username": f"fresh{i}@example.com", "password": "wrongpass1"},
            headers=headers,
        )
        assert response.status_code == 400, response.text

    response = client.post(
        "/api/v1/mobile/auth/login",
        json={"username": "one-more@example.com", "password": "wrongpass1"},
        headers=headers,
    )
    assert response.status_code == 429, response.text


def test_no_trusted_header_fails_open(
    client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    for i in range(IP_BUCKET_CAPACITY + 5):
        response = client.post(
            "/api/v1/mobile/auth/login",
            json={"username": f"nohdr{i}@example.com", "password": "wrongpass1"},
        )
        assert response.status_code == 400, response.text


def test_duplicate_header_lines_key_on_last_token(
    client, fresh_rate_limiter, fresh_ip_rate_limiter, monkeypatch
):
    monkeypatch.setattr(auth_router.service, "login", _mock_login_failure)
    duplicate_headers = [
        ("X-Client-IP", "6.6.6.6"),
        ("X-Client-IP", "5.5.5.5, 3.3.3.3"),
    ]
    for i in range(IP_BUCKET_CAPACITY):
        response = client.post(
            "/api/v1/mobile/auth/login",
            json={"username": f"duphdr{i}@example.com", "password": "wrongpass1"},
            headers=duplicate_headers,
        )
        assert response.status_code == 400, response.text
    response = client.post(
        "/api/v1/mobile/auth/login",
        json={"username": f"duphdr{IP_BUCKET_CAPACITY}@example.com", "password": "wrongpass1"},
        headers=duplicate_headers,
    )
    assert response.status_code == 429, response.text

    response = client.post(
        "/api/v1/mobile/auth/login",
        json={"username": "never-charged@example.com", "password": "wrongpass1"},
        headers={"X-Client-IP": "6.6.6.6"},
    )
    assert response.status_code == 400, response.text

    response = client.post(
        "/api/v1/mobile/auth/login",
        json={"username": "charged-key@example.com", "password": "wrongpass1"},
        headers={"X-Client-IP": "3.3.3.3"},
    )
    assert response.status_code == 429, response.text
