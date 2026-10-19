"""U-677: refresh rotation compared DB timestamps with an aware ``now``.

``AuthRefreshToken.ExpiresDatetime`` / ``RevokedDatetime`` are DATETIME2, which
pyodbc reads back as NAIVE UTC datetimes. ``refresh_access_token`` compared
them against ``datetime.now(timezone.utc)``, so every refresh of a stored token
raised ``TypeError: can't compare offset-naive and offset-aware datetimes`` —
re-raised as ``ValueError("Token refresh failed: ...")`` and turned into a 401
by both ``/api/v1/auth/refresh`` and ``/api/v1/mobile/auth/refresh``.

The fake repo below returns naive datetimes exactly as pyodbc does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from entities.auth.business import service as auth_service
from entities.auth.business.model import AuthRefreshTokenRecord
from entities.auth.business.service import AuthService


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _FakeAuthRepo:
    def __init__(self, auth):
        self.auth = auth

    def read_by_public_id(self, *, public_id):
        return self.auth if public_id == self.auth.public_id else None


class _FakeTokenRepo:
    def __init__(self, stored: AuthRefreshTokenRecord | None):
        self.stored = stored
        self.revoked_calls: list[dict] = []
        self.created: list[dict] = []

    def read_by_hash(self, token_hash):
        return self.stored

    def revoke_by_hash(self, **kwargs):
        self.revoked_calls.append(kwargs)
        return self.stored

    def create_refresh_token(self, **kwargs):
        self.created.append(kwargs)
        return None


def _record(*, expires: datetime, revoked: datetime | None = None) -> AuthRefreshTokenRecord:
    return AuthRefreshTokenRecord(
        id=1,
        auth_id=7,
        token_hash="h",
        token_jti="old-jti",
        issued_datetime=_naive_utc_now() - timedelta(days=1),
        expires_datetime=expires,
        revoked_datetime=revoked,
        replaced_by_token_jti=None,
    )


@pytest.fixture
def make_service(monkeypatch):
    auth = SimpleNamespace(id=7, public_id="auth-pub", username="worker", user_id=None)
    monkeypatch.setattr(
        auth_service,
        "verify_token",
        lambda **_: {"sub": auth.public_id, "token_type": "refresh"},
    )

    def _make(stored):
        token_repo = _FakeTokenRepo(stored)
        svc = AuthService(repo=_FakeAuthRepo(auth), token_repo=token_repo)
        svc._hash_refresh_token = lambda token: "h"
        svc._resolve_user_for_auth = lambda a: (None, None, False)
        svc.generate_auth_token = lambda **_: "new-access"
        svc._issue_refresh_token = lambda **_: (
            SimpleNamespace(refresh_token="new-refresh"),
            {
                "issued_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
                "jti": "new-jti",
            },
        )
        return svc, token_repo

    return _make


def test_live_stored_token_with_naive_db_expiry_rotates(make_service):
    svc, token_repo = make_service(_record(expires=_naive_utc_now() + timedelta(days=10)))

    access, refresh = svc.refresh_access_token(refresh_token="old-refresh")

    assert access == "new-access"
    assert refresh.refresh_token == "new-refresh"
    assert len(token_repo.revoked_calls) == 1
    assert token_repo.revoked_calls[0]["replaced_by_token_jti"] == "new-jti"


def test_expired_stored_token_with_naive_db_expiry_is_rejected_as_expired(make_service):
    svc, token_repo = make_service(_record(expires=_naive_utc_now() - timedelta(seconds=5)))

    with pytest.raises(ValueError, match=r"^Refresh token has expired\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")
    assert token_repo.revoked_calls == []


def test_recently_revoked_naive_token_is_allowed_within_grace(make_service):
    now = _naive_utc_now()
    svc, token_repo = make_service(
        _record(expires=now + timedelta(days=10), revoked=now - timedelta(seconds=3))
    )

    access, _ = svc.refresh_access_token(refresh_token="old-refresh")

    assert access == "new-access"
    assert token_repo.revoked_calls == []  # within grace: the revoke step is skipped


def test_revoked_naive_token_past_grace_is_rejected_as_revoked(make_service):
    now = _naive_utc_now()
    svc, _ = make_service(
        _record(expires=now + timedelta(days=10), revoked=now - timedelta(minutes=5))
    )

    with pytest.raises(ValueError, match=r"^Refresh token has been revoked\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")


def test_aware_db_values_are_still_compared_correctly(make_service):
    # Belt-and-braces: should the column ever become DATETIMEOFFSET (aware reads),
    # an expiry in the past must still be rejected, not raise a TypeError.
    expired = datetime.now(timezone(timedelta(hours=-5))) - timedelta(seconds=5)
    svc, _ = make_service(_record(expires=expired))

    with pytest.raises(ValueError, match=r"^Refresh token has expired\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")
