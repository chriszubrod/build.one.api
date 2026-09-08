"""U-410: login must not enforce a creation-time password policy.

`AuthLogin` carried `password: min_length=8` while the iOS client gated submit
at 6 characters. Any field user whose stored password was 6-7 characters got a
Pydantic 422 *before* the credential check ran — an error no retry could clear,
surfaced on-device as a bare "Request failed (HTTP 422)". These tests pin the
contract for both login surfaces (mobile and web share the `AuthLogin` model).
"""

import pytest
from fastapi.testclient import TestClient

import entities.auth.api.router as auth_router
from app import app
from entities.auth.api.schemas import (
    AdminSetCredentials,
    AuthCreate,
    AuthLogin,
    AuthSignup,
    ChangePasswordRequest,
)
from entities.auth.business.model import Auth, AuthToken, RefreshToken
from shared.rate_limit import (
    IP_BUCKET_CAPACITY,
    IP_REFILL_INTERVAL_SECONDS,
    LOGIN_BUCKET_CAPACITY,
    LOGIN_REFILL_INTERVAL_SECONDS,
    TokenBucketStore,
)

LOGIN_ROUTES = ["/api/v1/mobile/auth/login", "/api/v1/auth/login"]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_rate_limiters(monkeypatch):
    """Isolate every case from the shared in-proc buckets, so a short-password
    probe can never be throttled into a 429 and read as a passing 'not 422'."""
    monkeypatch.setattr(
        auth_router,
        "login_rate_limiter",
        TokenBucketStore(
            capacity=LOGIN_BUCKET_CAPACITY,
            refill_interval_seconds=LOGIN_REFILL_INTERVAL_SECONDS,
        ),
    )
    monkeypatch.setattr(
        auth_router,
        "login_ip_rate_limiter",
        TokenBucketStore(
            capacity=IP_BUCKET_CAPACITY,
            refill_interval_seconds=IP_REFILL_INTERVAL_SECONDS,
        ),
    )


def _stub_login_rejects(monkeypatch):
    """Credential check reached -> service raises -> route maps to 400."""

    def _reject(*, username, password):
        raise ValueError("Invalid credentials.")

    monkeypatch.setattr(auth_router.service, "login", _reject)


def _stub_login_accepts(monkeypatch):
    def _accept(*, username, password):
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
        return (
            auth,
            AuthToken(access_token="access", token_type="bearer", expires_in=900),
            RefreshToken(refresh_token="refresh", token_type="bearer", expires_in=86400),
        )

    monkeypatch.setattr(auth_router.service, "login", _accept)


@pytest.mark.parametrize("route", LOGIN_ROUTES)
@pytest.mark.parametrize("password", ["a", "ab", "12345", "abc1234"])
def test_short_password_reaches_credential_check_not_422(
    client, monkeypatch, route, password
):
    """A password under the 8-char creation policy must be *authenticated*
    (and here rejected as 400 Invalid credentials), never refused as a
    malformed body. `abc1234` is the exact 7-char shape from the incident."""
    _stub_login_rejects(monkeypatch)
    response = client.post(route, json={"username": "marciabm", "password": password})
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Invalid credentials."


@pytest.mark.parametrize("route", LOGIN_ROUTES)
def test_short_password_can_actually_log_in(client, monkeypatch, route):
    """The 400 above must not be the floor relocated into the service layer —
    with a valid credential, a 7-char password logs in."""
    _stub_login_accepts(monkeypatch)
    response = client.post(route, json={"username": "marciabm", "password": "abc1234"})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("route", LOGIN_ROUTES)
@pytest.mark.parametrize(
    "payload",
    [
        {"username": "marciabm", "password": ""},
        {"username": "", "password": "abc1234"},
        {"username": "marciabm"},
        {"password": "abc1234"},
    ],
    ids=["empty-password", "empty-username", "no-password", "no-username"],
)
def test_empty_or_missing_credentials_still_422(client, monkeypatch, route, payload):
    """Relaxing the floor must not relax it to nothing — a blank or absent
    field is still a malformed body, not an authentication attempt."""
    _stub_login_accepts(monkeypatch)
    assert client.post(route, json=payload).status_code == 422


@pytest.mark.parametrize("route", LOGIN_ROUTES)
def test_overlong_password_still_rejected(client, monkeypatch, route):
    """The 255-char ceiling is unchanged."""
    _stub_login_accepts(monkeypatch)
    response = client.post(
        route, json={"username": "marciabm", "password": "x" * 256}
    )
    assert response.status_code == 422


def test_login_carries_no_password_length_floor():
    """Guards the schema directly, so the floor can't be reintroduced without
    this failing even if the routes were stubbed differently."""
    assert AuthLogin.model_fields["password"].metadata[0].min_length == 1


@pytest.mark.parametrize(
    "model, field",
    [
        (AuthCreate, "password"),
        (AuthSignup, "password"),
        (AuthSignup, "confirm_password"),
        (AdminSetCredentials, "password"),
        (ChangePasswordRequest, "new_password"),
    ],
)
def test_password_policy_still_enforced_where_passwords_are_set(model, field):
    """The 8-char policy is not weakened — it stays on every model that
    *sets* a password. Only presenting one for authentication is exempt."""
    assert model.model_fields[field].metadata[0].min_length == 8
