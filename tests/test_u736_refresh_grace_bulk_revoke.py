"""U-736: revoked refresh grace applies only to rotation (ReplacedByTokenJti set).

Bulk revoke (password change, sign out all devices) and single logout leave
ReplacedByTokenJti NULL; those tokens must not mint new sessions within grace.
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


def _record(
    *,
    expires: datetime,
    revoked: datetime | None = None,
    replaced_by_token_jti: str | None = None,
) -> AuthRefreshTokenRecord:
    return AuthRefreshTokenRecord(
        id=1,
        auth_id=7,
        token_hash="h",
        token_jti="old-jti",
        issued_datetime=_naive_utc_now() - timedelta(days=1),
        expires_datetime=expires,
        revoked_datetime=revoked,
        replaced_by_token_jti=replaced_by_token_jti,
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
        svc._store_refresh_token = lambda **_: None
        return svc, token_repo

    return _make


def test_rotation_revoked_within_grace_issues_new_tokens(make_service):
    now = _naive_utc_now()
    svc, token_repo = make_service(
        _record(
            expires=now + timedelta(days=10),
            revoked=now - timedelta(seconds=5),
            replaced_by_token_jti="successor-jti",
        )
    )

    access, refresh = svc.refresh_access_token(refresh_token="old-refresh")

    assert access == "new-access"
    assert refresh.refresh_token == "new-refresh"
    assert token_repo.revoked_calls == []


def test_password_change_after_rotation_cannot_refresh_old_token_via_grace(make_service):
    """Reviewer P1: A rotated to B, bulk revoke revokes B and strips A's ReplacedByTokenJti."""
    now = _naive_utc_now()
    svc, token_repo = make_service(
        _record(
            expires=now + timedelta(days=10),
            revoked=now - timedelta(seconds=5),
            replaced_by_token_jti=None,
        )
    )

    with pytest.raises(ValueError, match=r"^Refresh token has been revoked\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")
    assert token_repo.revoked_calls == []


def test_bulk_or_logout_revoke_within_grace_is_rejected_immediately(make_service):
    now = _naive_utc_now()
    svc, token_repo = make_service(
        _record(
            expires=now + timedelta(days=10),
            revoked=now - timedelta(seconds=5),
            replaced_by_token_jti=None,
        )
    )

    with pytest.raises(ValueError, match=r"^Refresh token has been revoked\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")
    assert token_repo.revoked_calls == []


def test_rotation_revoked_past_grace_is_rejected(make_service):
    now = _naive_utc_now()
    svc, _ = make_service(
        _record(
            expires=now + timedelta(days=10),
            revoked=now - timedelta(seconds=20),
            replaced_by_token_jti="successor-jti",
        )
    )

    with pytest.raises(ValueError, match=r"^Refresh token has been revoked\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")


def test_live_token_rotates_normally(make_service):
    svc, token_repo = make_service(_record(expires=_naive_utc_now() + timedelta(days=10)))

    access, refresh = svc.refresh_access_token(refresh_token="old-refresh")

    assert access == "new-access"
    assert refresh.refresh_token == "new-refresh"
    assert len(token_repo.revoked_calls) == 1
    assert token_repo.revoked_calls[0]["replaced_by_token_jti"] == "new-jti"


def test_expired_token_is_rejected(make_service):
    svc, token_repo = make_service(_record(expires=_naive_utc_now() - timedelta(seconds=5)))

    with pytest.raises(ValueError, match=r"^Refresh token has expired\.$"):
        svc.refresh_access_token(refresh_token="old-refresh")
    assert token_repo.revoked_calls == []
