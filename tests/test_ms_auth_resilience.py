"""Pure-logic unit tests for MS auth resilience (U-224). Covers: token-refresh failure
classification, MsAuthTransientError hierarchy, shared-client auth seams, outbox retry vs dead-letter decisions,
the dict-envelope classification round-trip (is_retryable/is_auth_error), tenant_id-resolution fixes, and the
shared classify_failure/AuthFailureKind helpers."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.auth.business.model import MsAuth
from integrations.ms.auth.business.service import MsAuthService
from integrations.ms.base.client import MsGraphClient
from integrations.ms.base.errors import (
    MsAuthError,
    MsAuthTransientError,
    MsServerError,
    MsWriteRefusedError,
    build_error_envelope,
)
from integrations.ms.mail.external.client import _error_response as mail_error_response
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.worker import MAX_ATTEMPTS, MsOutboxWorker
from integrations.ms.reconciliation.business.excel_detector import ExcelMissingRowDetector
from integrations.ms.sharepoint.external.client import _error_response as sharepoint_error_response
from shared.auth_failure import AuthFailureKind, classify_failure

TENANT_ID = "t-1"


def _make_ms_auth(**overrides):
    defaults = {
        "id": 1,
        "public_id": "auth-1",
        "row_version": "abc",
        "created_datetime": "2026-08-11 10:00:00",
        "modified_datetime": "2026-08-11 10:00:00",
        "code": "code",
        "state": "state",
        "token_type": "Bearer",
        "access_token": "access-tok",
        "expires_in": 3600,
        "refresh_token": "refresh",
        "scope": "openid",
        "tenant_id": TENANT_ID,
        "user_id": "user-1",
    }
    defaults.update(overrides)
    return MsAuth(**defaults)


def _auth_service_with_repo(repo):
    return MsAuthService(repo=repo)


@contextmanager
def _applock(acquired=True):
    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield acquired

    with patch(
        "integrations.ms.base.locking.ms_app_lock",
        fake_lock,
    ):
        yield


# --------------------------------------------------------------------------- #
# A. Failure classification — MsAuthService.ensure_valid_token_classified
# --------------------------------------------------------------------------- #


def test_valid_token_returns_auth_none_without_refresh():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=False), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified()

    assert result_auth is auth
    assert kind is AuthFailureKind.NONE
    refresh.assert_not_called()


def test_no_auth_records_classifies_permanent():
    repo = MagicMock()
    repo.read_all.return_value = []
    svc = _auth_service_with_repo(repo)

    auth, kind = svc.ensure_valid_token_classified()

    assert auth is None
    assert kind is AuthFailureKind.PERMANENT


def test_explicit_tenant_id_missing_auth_classifies_permanent():
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = None
    svc = _auth_service_with_repo(repo)

    auth, kind = svc.ensure_valid_token_classified(tenant_id="SOME_TENANT")

    assert auth is None
    assert kind is AuthFailureKind.PERMANENT
    assert kind is not AuthFailureKind.TRANSIENT


def test_explicit_tenant_id_missing_auth_after_lock_classifies_permanent():
    auth = _make_ms_auth(tenant_id="SOME_TENANT")
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [auth, None]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id="SOME_TENANT")

    assert result_auth is None
    assert kind is AuthFailureKind.PERMANENT
    assert kind is not AuthFailureKind.TRANSIENT
    refresh.assert_not_called()


def test_refresh_applock_timeout_classifies_transient():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=False
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified()

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT
    refresh.assert_not_called()


def test_refresh_400_invalid_grant_classifies_permanent():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 400, "message": "invalid_grant"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.PERMANENT


def test_refresh_exception_classifies_transient():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh",
        side_effect=RuntimeError("network blip"),
    ):
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_refresh_201_unreadable_auth_classifies_transient():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.side_effect = [auth, auth, None]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 201, "message": "ok"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_concurrent_refresh_skips_ms_call_when_token_fresh_after_lock():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", side_effect=[True, False]), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert result_auth is auth
    assert kind is AuthFailureKind.NONE
    refresh.assert_not_called()


def test_refresh_non_dict_result_classifies_transient():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh",
        return_value="unexpected string response",
    ):
        result_auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


@pytest.mark.parametrize(
    "status_code,expected_kind",
    [
        (400, AuthFailureKind.PERMANENT),
        (401, AuthFailureKind.TRANSIENT),
        (403, AuthFailureKind.TRANSIENT),
        (429, AuthFailureKind.TRANSIENT),
        (500, AuthFailureKind.TRANSIENT),
        (502, AuthFailureKind.TRANSIENT),
        (503, AuthFailureKind.TRANSIENT),
    ],
)
def test_only_400_invalid_grant_is_permanent_everything_else_transient(
    status_code, expected_kind
):
    """SAFE DEFAULT IS TRANSIENT: only HTTP 400 (invalid_grant) is permanent."""
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), patch(
        "integrations.ms.auth.business.service.connect_ms_oauth_2_token_endpoint_refresh",
        return_value={"status_code": status_code, "message": f"http {status_code}"},
    ):
        _auth, kind = svc.ensure_valid_token_classified(tenant_id=TENANT_ID)

    assert kind is expected_kind
    if expected_kind is AuthFailureKind.PERMANENT:
        assert status_code == 400
    else:
        assert status_code != 400


# --------------------------------------------------------------------------- #
# B. ensure_valid_token wrapper (backward compat)
# --------------------------------------------------------------------------- #


def test_ensure_valid_token_wrapper_returns_auth_only_on_success():
    auth = _make_ms_auth()
    repo = MagicMock()
    svc = _auth_service_with_repo(repo)

    with patch.object(
        svc, "ensure_valid_token_classified", return_value=(auth, AuthFailureKind.NONE)
    ):
        result = svc.ensure_valid_token()

    assert result is auth
    assert not isinstance(result, tuple)


def test_ensure_valid_token_wrapper_returns_none_only_on_failure():
    repo = MagicMock()
    svc = _auth_service_with_repo(repo)

    with patch.object(
        svc,
        "ensure_valid_token_classified",
        return_value=(None, AuthFailureKind.TRANSIENT),
    ):
        result = svc.ensure_valid_token()

    assert result is None
    assert not isinstance(result, tuple)


# --------------------------------------------------------------------------- #
# C. MsAuthTransientError exception hierarchy
# --------------------------------------------------------------------------- #


def test_ms_auth_transient_error_is_retryable():
    assert MsAuthTransientError("transient").is_retryable is True


def test_ms_auth_error_is_not_retryable():
    assert MsAuthError("permanent").is_retryable is False


def test_ms_auth_transient_error_is_subclass_of_ms_auth_error():
    assert issubclass(MsAuthTransientError, MsAuthError)


def test_ms_auth_transient_error_caught_by_ms_auth_error_handler():
    caught = False
    try:
        raise MsAuthTransientError("transient refresh failure")
    except MsAuthError:
        caught = True
    assert caught


# --------------------------------------------------------------------------- #
# D. Client seam — MsGraphClient._send_once / _send_once_raw
# --------------------------------------------------------------------------- #


def _make_http_client(auth_service=None):
    return MsGraphClient(
        auth_service=auth_service or MagicMock(),
        http_client=MagicMock(),
    )


def _send_once_kwargs(client):
    from integrations.ms.base.client import _TIMEOUT_TIERS

    return dict(
        method="GET",
        url=f"{client.base_url}/sites/root",
        request_path="sites/root",
        params={},
        json_body=None,
        content=None,
        content_type=None,
        extra_headers=None,
        client_request_id=None,
        timeout=_TIMEOUT_TIERS["A"],
        correlation_id="corr-1",
        operation_name="GET sites/root",
    )


def _send_once_raw_kwargs(client):
    from integrations.ms.base.client import _TIMEOUT_TIERS

    return dict(
        method="GET",
        url=f"{client.base_url}/drives/d1/items/i1/content",
        request_path="drives/d1/items/i1/content",
        params={},
        extra_headers=None,
        timeout=_TIMEOUT_TIERS["C"],
        correlation_id="corr-1",
        operation_name="GET drives/d1/items/i1/content",
    )


@pytest.mark.parametrize("method_name,kwargs_builder", [
    ("_send_once", _send_once_kwargs),
    ("_send_once_raw", _send_once_raw_kwargs),
])
def test_send_once_transient_auth_failure_raises_ms_auth_transient_error(
    method_name, kwargs_builder
):
    client = _make_http_client()
    client.auth_service.ensure_valid_token_classified.return_value = (
        None,
        AuthFailureKind.TRANSIENT,
    )

    with pytest.raises(MsAuthTransientError):
        getattr(client, method_name)(**kwargs_builder(client))


@pytest.mark.parametrize("method_name,kwargs_builder", [
    ("_send_once", _send_once_kwargs),
    ("_send_once_raw", _send_once_raw_kwargs),
])
def test_send_once_permanent_auth_failure_raises_plain_ms_auth_error(
    method_name, kwargs_builder
):
    client = _make_http_client()
    client.auth_service.ensure_valid_token_classified.return_value = (
        None,
        AuthFailureKind.PERMANENT,
    )

    with pytest.raises(MsAuthError) as exc_info:
        getattr(client, method_name)(**kwargs_builder(client))
    assert type(exc_info.value) is MsAuthError
    assert not isinstance(exc_info.value, MsAuthTransientError)


@pytest.mark.parametrize("method_name,kwargs_builder", [
    ("_send_once", _send_once_kwargs),
    ("_send_once_raw", _send_once_raw_kwargs),
])
def test_send_once_401_recovery_transient_refresh_raises_ms_auth_transient_error(
    method_name, kwargs_builder
):
    client = _make_http_client()
    auth = MagicMock(access_token="tok")
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (auth, AuthFailureKind.NONE),
        (None, AuthFailureKind.TRANSIENT),
    ]
    resp_401 = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", return_value=resp_401), pytest.raises(
        MsAuthTransientError
    ):
        getattr(client, method_name)(**kwargs_builder(client))


@pytest.mark.parametrize("method_name,kwargs_builder", [
    ("_send_once", _send_once_kwargs),
    ("_send_once_raw", _send_once_raw_kwargs),
])
def test_send_once_401_recovery_permanent_refresh_raises_plain_ms_auth_error(
    method_name, kwargs_builder
):
    client = _make_http_client()
    auth = MagicMock(access_token="tok")
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (auth, AuthFailureKind.NONE),
        (None, AuthFailureKind.PERMANENT),
    ]
    resp_401 = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", return_value=resp_401), pytest.raises(
        MsAuthError
    ) as exc_info:
        getattr(client, method_name)(**kwargs_builder(client))
    assert type(exc_info.value) is MsAuthError


# --------------------------------------------------------------------------- #
# E. Worker retry/dead-letter — MsOutboxWorker._handle_ms_error
# --------------------------------------------------------------------------- #


def _make_outbox_row(**overrides):
    defaults = {
        "id": 1,
        "public_id": "outbox-1",
        "row_version": "abc",
        "kind": "append_excel_row",
        "entity_type": "Bill",
        "entity_public_id": "22222222-2222-2222-2222-222222222222",
        "tenant_id": TENANT_ID,
        "request_id": "req-1",
        "status": "in_progress",
        "attempts": 0,
    }
    defaults.update(overrides)
    return MsOutbox(**defaults)


def test_transient_auth_error_attempt_1_schedules_retry_not_dead_letter():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    worker._handle_ms_error(
        _make_outbox_row(attempts=0),
        MsAuthTransientError("transient refresh failure"),
    )
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


def test_permanent_auth_error_dead_letters_without_retry():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    worker._handle_ms_error(
        _make_outbox_row(attempts=0),
        MsAuthError("permanent — re-authorization required"),
    )
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_transient_auth_error_at_max_attempts_minus_one_dead_letters():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    worker._handle_ms_error(
        _make_outbox_row(attempts=MAX_ATTEMPTS - 1),
        MsAuthTransientError("transient but retries exhausted"),
    )
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


# --------------------------------------------------------------------------- #
# F. Envelope round-trip regression tests
# --------------------------------------------------------------------------- #


def _classify_via_worker(status_code, is_retryable, is_auth_error):
    result = {"status_code": status_code, "message": "m"}
    if is_retryable is not None:
        result["is_retryable"] = is_retryable
    if is_auth_error is not None:
        result["is_auth_error"] = is_auth_error
    return result


def test_envelope_401_retryable_auth_raises_ms_auth_transient_error():
    result = _classify_via_worker(401, is_retryable=True, is_auth_error=True)
    with pytest.raises(MsAuthTransientError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsAuthTransientError


def test_envelope_401_non_retryable_auth_raises_plain_ms_auth_error():
    result = _classify_via_worker(401, is_retryable=False, is_auth_error=True)
    with pytest.raises(MsAuthError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsAuthError


def test_envelope_500_permanent_auth_raises_plain_ms_auth_error():
    result = _classify_via_worker(500, is_retryable=False, is_auth_error=True)
    with pytest.raises(MsAuthError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsAuthError


def test_envelope_500_non_retryable_non_auth_raises_ms_server_error():
    result = _classify_via_worker(500, is_retryable=False, is_auth_error=False)
    with pytest.raises(MsServerError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsServerError


def test_envelope_500_missing_hints_raises_ms_server_error():
    result = {"status_code": 500, "message": "m"}
    with pytest.raises(MsServerError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsServerError


def test_envelope_500_retryable_non_auth_raises_ms_server_error():
    result = _classify_via_worker(500, is_retryable=True, is_auth_error=False)
    with pytest.raises(MsServerError) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), result)
    assert type(exc_info.value) is MsServerError


def _assert_raise_if_external_error_type(envelope, expected_type):
    with pytest.raises(expected_type) as exc_info:
        MsOutboxWorker._raise_if_external_error(_make_outbox_row(), envelope)
    assert type(exc_info.value) is expected_type


@pytest.mark.parametrize("error_response_fn", [sharepoint_error_response, mail_error_response])
def test_error_response_transient_auth_401_round_trips_to_ms_auth_transient_error(
    error_response_fn,
):
    error = MsAuthTransientError("transient auth at Graph", http_status=401)
    envelope = error_response_fn(error)
    _assert_raise_if_external_error_type(envelope, MsAuthTransientError)


@pytest.mark.parametrize("error_response_fn", [sharepoint_error_response, mail_error_response])
def test_error_response_permanent_auth_initial_resolve_round_trips_to_ms_auth_error(
    error_response_fn,
):
    error = MsAuthError("no valid MS auth token available")
    envelope = error_response_fn(error)
    _assert_raise_if_external_error_type(envelope, MsAuthError)


def test_build_error_envelope_write_refused_round_trips_to_ms_server_error():
    error = MsWriteRefusedError("MS write refused: ALLOW_MS_WRITES not true")
    envelope = build_error_envelope(error)
    _assert_raise_if_external_error_type(envelope, MsServerError)


# --------------------------------------------------------------------------- #
# G. Tenant-id resolution fixes — never touch the network just to read an identity field
# --------------------------------------------------------------------------- #


def test_resolve_tenant_id_outbox_service_never_calls_ensure_valid_token():
    from integrations.ms.outbox.business.service import _resolve_tenant_id

    with patch.object(
        MsAuthService,
        "ensure_valid_token",
        side_effect=AssertionError("must not be called"),
    ), patch.object(
        MsAuthService,
        "read_all",
        return_value=[_make_ms_auth(tenant_id="t-1")],
    ):
        result = _resolve_tenant_id()

    assert result == "t-1"


def test_resolve_tenant_id_excel_detector_never_calls_ensure_valid_token():
    with patch.object(
        MsAuthService,
        "ensure_valid_token",
        side_effect=AssertionError("must not be called"),
    ), patch.object(
        MsAuthService,
        "read_all",
        return_value=[_make_ms_auth(tenant_id="t-1")],
    ):
        result = ExcelMissingRowDetector._resolve_tenant_id()

    assert result == "t-1"


def test_resolve_tenant_id_outbox_service_empty_read_all_returns_none():
    from integrations.ms.outbox.business.service import _resolve_tenant_id

    with patch.object(MsAuthService, "read_all", return_value=[]):
        assert _resolve_tenant_id() is None


def test_resolve_tenant_id_excel_detector_empty_read_all_returns_none():
    with patch.object(MsAuthService, "read_all", return_value=[]):
        assert ExcelMissingRowDetector._resolve_tenant_id() is None


def test_resolve_tenant_id_outbox_service_read_all_exception_returns_none():
    from integrations.ms.outbox.business.service import _resolve_tenant_id

    with patch.object(MsAuthService, "read_all", side_effect=RuntimeError("db down")):
        assert _resolve_tenant_id() is None


def test_resolve_tenant_id_excel_detector_read_all_exception_returns_none():
    with patch.object(MsAuthService, "read_all", side_effect=RuntimeError("db down")):
        assert ExcelMissingRowDetector._resolve_tenant_id() is None


# --------------------------------------------------------------------------- #
# H. MsAuthService._read_auth_by_tenant_id_or_first and shared classify_failure
# --------------------------------------------------------------------------- #


def test_read_auth_by_tenant_id_or_first_with_tenant_id_uses_read_by_tenant_id():
    auth = _make_ms_auth(tenant_id="t-1")
    repo = MagicMock()
    repo.read_by_tenant_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    result = svc._read_auth_by_tenant_id_or_first("t-1")

    assert result is auth
    repo.read_by_tenant_id.assert_called_once_with("t-1")
    repo.read_all.assert_not_called()


def test_read_auth_by_tenant_id_or_first_without_tenant_id_uses_read_all_first():
    auth = _make_ms_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    result = svc._read_auth_by_tenant_id_or_first(None)

    assert result is auth
    repo.read_all.assert_called_once()
    repo.read_by_tenant_id.assert_not_called()


def test_read_auth_by_tenant_id_or_first_empty_read_all_returns_none():
    repo = MagicMock()
    repo.read_all.return_value = []
    svc = _auth_service_with_repo(repo)

    assert svc._read_auth_by_tenant_id_or_first(None) is None


def test_classify_failure_transient_logs_error_and_returns_tuple():
    logger = MagicMock()
    result = classify_failure(logger, "msg", AuthFailureKind.TRANSIENT)
    assert result == (None, AuthFailureKind.TRANSIENT)
    logger.error.assert_called_once()
    log_msg = logger.error.call_args.args[0]
    assert "msg" in log_msg
    assert "transient" in log_msg
    logger.exception.assert_not_called()


def test_classify_failure_permanent_with_exc_info_logs_exception():
    logger = MagicMock()
    result = classify_failure(
        logger, "msg", AuthFailureKind.PERMANENT, exc_info=True
    )
    assert result == (None, AuthFailureKind.PERMANENT)
    logger.exception.assert_called_once()
    log_msg = logger.exception.call_args.args[0]
    assert "msg" in log_msg
    assert "permanent" in log_msg
    logger.error.assert_not_called()
