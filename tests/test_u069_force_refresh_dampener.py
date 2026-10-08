"""
U-069: force_refresh redundant-refresh dampener (QBO + MS).

A 401 triggers a forced refresh carrying `stale_access_token` (the token the failed
request used). If the row already holds a different, unexpired token, a concurrent
caller refreshed after that 401, so the forced refresh is skipped. Without this, N
concurrent 401-recoveries each run a serialized real refresh against Intuit/Microsoft.
The behaviour change is T1: a forced refresh that finds a rotated row returns it.
Forced refresh with no stale token, or with the stale token still on the row, is unchanged.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.auth.business.model import AuthFailureKind, QboAuth
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.client import _TIMEOUT_TIERS as QBO_TIMEOUT_TIERS
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import QboAuthTransientError
from integrations.ms.auth.business.model import MsAuth
from integrations.ms.auth.business.service import MsAuthService
from integrations.ms.base.client import _TIMEOUT_TIERS as MS_TIMEOUT_TIERS
from integrations.ms.base.client import MsGraphClient
from integrations.ms.base.errors import MsAuthTransientError

REALM_ID = "realm-test"
TENANT_ID = "t-1"
QBO_REFRESH = "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh"
MS_REFRESH = "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh"
REFRESH_OK = {"status_code": 201, "message": "ok"}


def _qbo_auth(access_token):
    return QboAuth(
        id=1,
        public_id="auth-1",
        row_version="abc",
        created_datetime="2026-08-11 10:00:00",
        modified_datetime="2026-08-11 10:00:00",
        code="code",
        realm_id=REALM_ID,
        state="state",
        token_type="Bearer",
        id_token="id",
        access_token=access_token,
        expires_in=3600,
        refresh_token="refresh",
        x_refresh_token_expires_in=8640000,
    )


def _ms_auth(access_token):
    return MsAuth(
        id=1,
        public_id="auth-1",
        row_version="abc",
        created_datetime="2026-08-11 10:00:00",
        modified_datetime="2026-08-11 10:00:00",
        code="code",
        state="state",
        token_type="Bearer",
        access_token=access_token,
        expires_in=3600,
        refresh_token="refresh",
        scope="openid",
        tenant_id=TENANT_ID,
        user_id="user-1",
    )


def _expired_tokens(*tokens):
    def is_expired(auth, buffer_seconds=60):
        return auth.access_token in tokens

    return is_expired


@contextmanager
def _applock(target):
    """Patch `target` with a lock that is always granted."""

    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield True

    with patch(target, fake_lock):
        yield


def _qbo_applock():
    return _applock("integrations.intuit.qbo.base.locking.qbo_app_lock")


def _ms_applock():
    return _applock("integrations.ms.base.locking.ms_app_lock")


def _patch_discovery_prewarm():
    return patch(
        "integrations.intuit.qbo.base.helper.get_intuit_discovery_document",
        return_value={"token_endpoint": "https://oauth.intuit.com/token"},
    )


# --------------------------------------------------------------------------- #
# QBO — MS-equivalent cases T1-T4 plus pre-lock
# --------------------------------------------------------------------------- #


def test_qbo_forced_refresh_skipped_when_row_rotated_by_concurrent_caller():
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [_qbo_auth("X"), _qbo_auth("Y")]
    svc = QboAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _qbo_applock(), _patch_discovery_prewarm(), patch(QBO_REFRESH) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            realm_id=REALM_ID, force_refresh=True, stale_access_token="X"
        )

    assert result_auth.access_token == "Y"
    assert kind is AuthFailureKind.NONE
    refresh.assert_not_called()


def test_qbo_forced_refresh_proceeds_when_stale_token_still_on_row():
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [_qbo_auth("X"), _qbo_auth("X"), _qbo_auth("Z")]
    svc = QboAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _qbo_applock(), _patch_discovery_prewarm(), patch(QBO_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            realm_id=REALM_ID, force_refresh=True, stale_access_token="X"
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_qbo_forced_refresh_proceeds_when_rotated_token_is_expired():
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [_qbo_auth("X"), _qbo_auth("Y"), _qbo_auth("Z")]
    svc = QboAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens("Y")), _qbo_applock(), _patch_discovery_prewarm(), patch(QBO_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            realm_id=REALM_ID, force_refresh=True, stale_access_token="X"
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_qbo_forced_refresh_without_stale_token_is_unconditional():
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [_qbo_auth("Y"), _qbo_auth("Y"), _qbo_auth("Z")]
    svc = QboAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _qbo_applock(), _patch_discovery_prewarm(), patch(QBO_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            realm_id=REALM_ID, force_refresh=True
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_qbo_forced_refresh_skipped_before_lock_when_row_already_rotated():
    repo = MagicMock()
    repo.read_by_realm_id.return_value = _qbo_auth("Y")
    svc = QboAuthService(repo=repo)
    lock_entered = []

    @contextmanager
    def recording_lock(*_args, **_kwargs):
        lock_entered.append(True)
        yield True

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), patch(
        "integrations.intuit.qbo.base.locking.qbo_app_lock", recording_lock
    ), patch(QBO_REFRESH) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            realm_id=REALM_ID, force_refresh=True, stale_access_token="X"
        )

    assert result_auth.access_token == "Y"
    assert kind is AuthFailureKind.NONE
    assert lock_entered == []
    refresh.assert_not_called()


# --------------------------------------------------------------------------- #
# MS — T1-T4 plus pre-lock
# --------------------------------------------------------------------------- #


def test_ms_forced_refresh_skipped_when_row_rotated_by_concurrent_caller():
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [_ms_auth("X"), _ms_auth("Y")]
    svc = MsAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _ms_applock(), patch(MS_REFRESH) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            tenant_id=TENANT_ID, force_refresh=True, stale_access_token="X"
        )

    assert result_auth.access_token == "Y"
    assert kind is AuthFailureKind.NONE
    refresh.assert_not_called()


def test_ms_forced_refresh_proceeds_when_stale_token_still_on_row():
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [_ms_auth("X"), _ms_auth("X"), _ms_auth("Z")]
    svc = MsAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _ms_applock(), patch(MS_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            tenant_id=TENANT_ID, force_refresh=True, stale_access_token="X"
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_ms_forced_refresh_proceeds_when_rotated_token_is_expired():
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [_ms_auth("X"), _ms_auth("Y"), _ms_auth("Z")]
    svc = MsAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens("Y")), _ms_applock(), patch(MS_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            tenant_id=TENANT_ID, force_refresh=True, stale_access_token="X"
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_ms_forced_refresh_without_stale_token_is_unconditional():
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [_ms_auth("Y"), _ms_auth("Y"), _ms_auth("Z")]
    svc = MsAuthService(repo=repo)

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), _ms_applock(), patch(MS_REFRESH, return_value=REFRESH_OK) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            tenant_id=TENANT_ID, force_refresh=True
        )

    refresh.assert_called_once()
    assert result_auth.access_token == "Z"
    assert kind is AuthFailureKind.NONE


def test_ms_forced_refresh_skipped_before_lock_when_row_already_rotated():
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = _ms_auth("Y")
    svc = MsAuthService(repo=repo)
    lock_entered = []

    @contextmanager
    def recording_lock(*_args, **_kwargs):
        lock_entered.append(True)
        yield True

    with patch.object(svc, "is_token_expired", side_effect=_expired_tokens()), patch(
        "integrations.ms.base.locking.ms_app_lock", recording_lock
    ), patch(MS_REFRESH) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(
            tenant_id=TENANT_ID, force_refresh=True, stale_access_token="X"
        )

    assert result_auth.access_token == "Y"
    assert kind is AuthFailureKind.NONE
    assert lock_entered == []
    refresh.assert_not_called()


# --------------------------------------------------------------------------- #
# Clients — a 401 hands the failed request's token to the forced refresh
# --------------------------------------------------------------------------- #


def _ok_body_response(body=None):
    response = MagicMock(status_code=200, headers={}, text="{}")
    response.json.return_value = {} if body is None else body
    return response


def _qbo_client():
    return QboHttpClient(
        realm_id=REALM_ID,
        auth_service=MagicMock(),
        http_client=MagicMock(),
        api_budget=MagicMock(),
    )


def test_qbo_401_recovery_resolves_with_stale_token_from_failed_request():
    client = _qbo_client()
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (_qbo_auth("tok-failed"), AuthFailureKind.NONE),
        (_qbo_auth("tok-new"), AuthFailureKind.NONE),
    ]
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", side_effect=[unauthorized, _ok_body_response()]), patch.object(
        client, "_resolve_auth", wraps=client._resolve_auth
    ) as resolve:
        _qbo_send_once(client)

    assert resolve.call_args_list[-1].kwargs == {
        "force_refresh": True,
        "stale_access_token": "tok-failed",
    }
    assert client.auth_service.ensure_valid_token_classified.call_args_list[-1].kwargs[
        "stale_access_token"
    ] == "tok-failed"


def test_ms_401_recovery_resolves_with_stale_token_from_failed_request():
    client = MsGraphClient(auth_service=MagicMock(), http_client=MagicMock())
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (_ms_auth("tok-failed"), AuthFailureKind.NONE),
        (_ms_auth("tok-new"), AuthFailureKind.NONE),
    ]
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", side_effect=[unauthorized, _ok_body_response()]), patch.object(
        client, "_resolve_auth", wraps=client._resolve_auth
    ) as resolve:
        _ms_send_once(client)

    assert resolve.call_args_list[-1].kwargs == {
        "force_refresh": True,
        "stale_access_token": "tok-failed",
    }
    assert client.auth_service.ensure_valid_token_classified.call_args_list[-1].kwargs[
        "stale_access_token"
    ] == "tok-failed"


# --------------------------------------------------------------------------- #
# A 401 that survives token recovery is retryable: the recovered token may have
# been rotated by a concurrent caller and still be invalid.
# --------------------------------------------------------------------------- #


def _qbo_send_once(client):
    return client._send_once(
        method="GET",
        url=f"https://qbo/v3/company/{REALM_ID}/bill/1",
        request_path="bill/1",
        params={},
        json_body=None,
        files=None,
        timeout=QBO_TIMEOUT_TIERS["A"],
        correlation_id="corr-1",
        operation_name="GET bill/1",
    )


def _ms_send_once(client):
    return client._send_once(
        method="GET",
        url=f"{client.base_url}/sites/root",
        request_path="sites/root",
        params={},
        json_body=None,
        content=None,
        content_type=None,
        extra_headers=None,
        client_request_id=None,
        timeout=MS_TIMEOUT_TIERS["A"],
        correlation_id="corr-1",
        operation_name="GET sites/root",
    )


def _ms_send_once_raw(client):
    return client._send_once_raw(
        method="GET",
        url=f"{client.base_url}/drives/d/items/i/content",
        request_path="drives/d/items/i/content",
        params=None,
        extra_headers=None,
        timeout=MS_TIMEOUT_TIERS["A"],
        correlation_id="corr-1",
        operation_name="GET content",
    )


def test_qbo_401_still_401_after_recovery_raises_transient_auth_error():
    client = _qbo_client()
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(
        client,
        "_resolve_auth",
        side_effect=[
            (_qbo_auth("tok-failed"), AuthFailureKind.NONE),
            (_qbo_auth("tok-rotated"), AuthFailureKind.NONE),
        ],
    ), patch.object(client, "_send_http", side_effect=[unauthorized, unauthorized]):
        with pytest.raises(QboAuthTransientError) as exc_info:
            _qbo_send_once(client)

    assert exc_info.value.is_retryable is True


def test_ms_json_401_still_401_after_recovery_raises_transient_auth_error():
    client = MsGraphClient(auth_service=MagicMock(), http_client=MagicMock())
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(
        client,
        "_resolve_auth",
        side_effect=[
            (_ms_auth("tok-failed"), AuthFailureKind.NONE),
            (_ms_auth("tok-rotated"), AuthFailureKind.NONE),
        ],
    ), patch.object(client, "_send_http", side_effect=[unauthorized, unauthorized]):
        with pytest.raises(MsAuthTransientError) as exc_info:
            _ms_send_once(client)

    assert exc_info.value.is_retryable is True


def test_ms_download_401_still_401_after_recovery_raises_transient_auth_error():
    client = MsGraphClient(auth_service=MagicMock(), http_client=MagicMock())
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(
        client,
        "_resolve_auth",
        side_effect=[
            (_ms_auth("tok-failed"), AuthFailureKind.NONE),
            (_ms_auth("tok-rotated"), AuthFailureKind.NONE),
        ],
    ), patch.object(client, "_send_http", side_effect=[unauthorized, unauthorized]):
        with pytest.raises(MsAuthTransientError) as exc_info:
            _ms_send_once_raw(client)

    assert exc_info.value.is_retryable is True


def test_qbo_401_then_200_after_recovery_returns_parsed_body():
    client = _qbo_client()
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(
        client,
        "_resolve_auth",
        side_effect=[
            (_qbo_auth("tok-failed"), AuthFailureKind.NONE),
            (_qbo_auth("tok-new"), AuthFailureKind.NONE),
        ],
    ), patch.object(
        client,
        "_send_http",
        side_effect=[unauthorized, _ok_body_response({"Bill": {"Id": "1"}})],
    ):
        body = _qbo_send_once(client)

    assert body == {"Bill": {"Id": "1"}}


def test_ms_json_401_then_200_after_recovery_returns_parsed_body():
    client = MsGraphClient(auth_service=MagicMock(), http_client=MagicMock())
    unauthorized = MagicMock(status_code=401, text="", headers={})

    with patch.object(
        client,
        "_resolve_auth",
        side_effect=[
            (_ms_auth("tok-failed"), AuthFailureKind.NONE),
            (_ms_auth("tok-new"), AuthFailureKind.NONE),
        ],
    ), patch.object(
        client,
        "_send_http",
        side_effect=[unauthorized, _ok_body_response({"id": "site-1"})],
    ):
        body = _ms_send_once(client)

    assert body == {"id": "site-1"}
