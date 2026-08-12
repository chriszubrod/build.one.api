"""Pure-logic unit tests for QBO auth resilience (U-215).

Covers: token-refresh failure classification, QboAuthTransientError hierarchy,
shared-client auth seams, outbox retry vs dead-letter decisions, discovery-
document caching, stranded-row reclaim, bill enqueue durability, and the
unpark script prefix contract.
"""
import importlib.util
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.auth.business.model import AuthFailureKind, QboAuth
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import (
    QboAuthError,
    QboAuthTransientError,
    QboBudgetExceededError,
    is_retryable_error,
)
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import (
    DEFAULT_RECLAIM_AFTER_SECONDS,
    MAX_ATTEMPTS,
    QboOutboxWorker,
    reclaim_after_seconds,
)

REALM_ID = "realm-test"


def _make_auth(**overrides):
    defaults = {
        "id": 1,
        "public_id": "auth-1",
        "row_version": "abc",
        "created_datetime": "2026-08-11 10:00:00",
        "modified_datetime": "2026-08-11 10:00:00",
        "code": "code",
        "realm_id": REALM_ID,
        "state": "state",
        "token_type": "Bearer",
        "id_token": "id",
        "access_token": "access-tok",
        "expires_in": 3600,
        "refresh_token": "refresh",
        "x_refresh_token_expires_in": 8640000,
    }
    defaults.update(overrides)
    return QboAuth(**defaults)


def _auth_service_with_repo(repo):
    return QboAuthService(repo=repo)


@contextmanager
def _applock(acquired=True):
    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield acquired

    with patch(
        "integrations.intuit.qbo.base.locking.qbo_app_lock",
        fake_lock,
    ):
        yield


def _patch_discovery_prewarm():
    return patch(
        "integrations.intuit.qbo.base.helper.get_intuit_discovery_document",
        return_value={"token_endpoint": "https://oauth.intuit.com/token"},
    )


# --------------------------------------------------------------------------- #
# A. Failure classification — ensure_valid_token_classified
# --------------------------------------------------------------------------- #


def test_valid_token_returns_auth_none_without_refresh():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=False), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh"
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


def test_explicit_realm_id_missing_auth_classifies_permanent():
    """An unconfigured realm is never fixed by retrying — must dead-letter, not burn attempts."""
    repo = MagicMock()
    repo.read_by_realm_id.return_value = None
    svc = _auth_service_with_repo(repo)

    auth, kind = svc.ensure_valid_token_classified(realm_id="SOME_REALM")

    assert auth is None
    assert kind is AuthFailureKind.PERMANENT
    assert kind is not AuthFailureKind.TRANSIENT


def test_explicit_realm_id_missing_auth_after_lock_classifies_permanent():
    """Post-applock re-read returning None is also permanent — same rationale as pre-lock."""
    auth = _make_auth(realm_id="SOME_REALM")
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [auth, None]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified(realm_id="SOME_REALM")

    assert result_auth is None
    assert kind is AuthFailureKind.PERMANENT
    assert kind is not AuthFailureKind.TRANSIENT
    refresh.assert_not_called()


def test_refresh_applock_timeout_classifies_transient():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=False
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh"
    ) as refresh:
        result_auth, kind = svc.ensure_valid_token_classified()

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT
    refresh.assert_not_called()


def test_refresh_400_invalid_grant_classifies_permanent():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 400, "message": "invalid_grant"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.PERMANENT


def test_refresh_503_discovery_unavailable_classifies_transient():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 503, "message": "discovery unavailable"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_refresh_500_classifies_transient():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 500, "message": "server error"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_refresh_exception_classifies_transient():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        side_effect=RuntimeError("network blip"),
    ):
        result_auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_refresh_201_unreadable_auth_classifies_transient():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.side_effect = [auth, auth, None]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        return_value={"status_code": 201, "message": "ok"},
    ):
        result_auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert result_auth is None
    assert kind is AuthFailureKind.TRANSIENT


def test_ensure_valid_token_wrapper_returns_auth_only_on_success():
    auth = _make_auth()
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=False):
        result = svc.ensure_valid_token()

    assert result is auth
    assert not isinstance(result, tuple)


def test_ensure_valid_token_wrapper_returns_none_only_on_failure():
    repo = MagicMock()
    repo.read_all.return_value = []
    svc = _auth_service_with_repo(repo)

    result = svc.ensure_valid_token()

    assert result is None
    assert not isinstance(result, tuple)


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
    auth = _make_auth()
    repo = MagicMock()
    repo.read_by_realm_id.return_value = auth
    svc = _auth_service_with_repo(repo)

    with patch.object(svc, "is_token_expired", return_value=True), _applock(
        acquired=True
    ), _patch_discovery_prewarm(), patch(
        "integrations.intuit.qbo.auth.business.service.connect_intuit_oauth_2_token_endpoint_refresh",
        return_value={"status_code": status_code, "message": f"http {status_code}"},
    ):
        _auth, kind = svc.ensure_valid_token_classified(realm_id=REALM_ID)

    assert kind is expected_kind
    if expected_kind is AuthFailureKind.PERMANENT:
        assert status_code == 400
    else:
        assert status_code != 400


# --------------------------------------------------------------------------- #
# B. Error hierarchy — base/errors.py
# --------------------------------------------------------------------------- #


def test_qbo_auth_transient_error_is_retryable():
    assert QboAuthTransientError("transient").is_retryable is True


def test_qbo_auth_error_is_not_retryable():
    assert QboAuthError("permanent").is_retryable is False


def test_qbo_auth_transient_error_is_subclass_of_qbo_auth_error():
    assert issubclass(QboAuthTransientError, QboAuthError)


def test_qbo_auth_transient_error_caught_by_qbo_auth_error_handler():
    caught = False
    try:
        raise QboAuthTransientError("transient refresh failure")
    except QboAuthError:
        caught = True
    assert caught


def test_is_retryable_error_transient_auth_true_permanent_auth_false():
    assert is_retryable_error(QboAuthTransientError("transient")) is True
    assert is_retryable_error(QboAuthError("permanent")) is False


# --------------------------------------------------------------------------- #
# C. Client seam — QboHttpClient._send_once
# --------------------------------------------------------------------------- #


def _make_http_client(auth_service=None):
    return QboHttpClient(
        realm_id=REALM_ID,
        auth_service=auth_service or MagicMock(),
        http_client=MagicMock(),
        api_budget=MagicMock(),
    )


def _send_once_kwargs(client):
    return dict(
        method="GET",
        url=f"https://qbo/v3/company/{REALM_ID}/bill/1",
        request_path="bill/1",
        params={},
        json_body=None,
        correlation_id="corr-1",
        operation_name="GET bill/1",
    )


def test_send_once_transient_auth_failure_raises_qbo_auth_transient_error():
    client = _make_http_client()
    client.auth_service.ensure_valid_token_classified.return_value = (
        None,
        AuthFailureKind.TRANSIENT,
    )

    with pytest.raises(QboAuthTransientError):
        client._send_once(**_send_once_kwargs(client))


def test_send_once_permanent_auth_failure_raises_plain_qbo_auth_error():
    client = _make_http_client()
    client.auth_service.ensure_valid_token_classified.return_value = (
        None,
        AuthFailureKind.PERMANENT,
    )

    with pytest.raises(QboAuthError) as exc_info:
        client._send_once(**_send_once_kwargs(client))
    assert type(exc_info.value) is QboAuthError
    assert not isinstance(exc_info.value, QboAuthTransientError)


def test_send_once_401_recovery_transient_refresh_raises_qbo_auth_transient_error():
    client = _make_http_client()
    auth = MagicMock(access_token="tok")
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (auth, AuthFailureKind.NONE),
        (None, AuthFailureKind.TRANSIENT),
    ]
    resp_401 = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", return_value=resp_401), pytest.raises(
        QboAuthTransientError
    ):
        client._send_once(**_send_once_kwargs(client))


def test_send_once_401_recovery_permanent_refresh_raises_plain_qbo_auth_error():
    client = _make_http_client()
    auth = MagicMock(access_token="tok")
    client.auth_service.ensure_valid_token_classified.side_effect = [
        (auth, AuthFailureKind.NONE),
        (None, AuthFailureKind.PERMANENT),
    ]
    resp_401 = MagicMock(status_code=401, text="", headers={})

    with patch.object(client, "_send_http", return_value=resp_401), pytest.raises(
        QboAuthError
    ) as exc_info:
        client._send_once(**_send_once_kwargs(client))
    assert type(exc_info.value) is QboAuthError


def test_real_qbo_403_response_maps_to_non_retryable_qbo_auth_error():
    client = _make_http_client()
    auth = MagicMock(access_token="tok")
    client.auth_service.ensure_valid_token_classified.return_value = (
        auth,
        AuthFailureKind.NONE,
    )
    resp_403 = MagicMock()
    resp_403.status_code = 403
    resp_403.text = '{"Fault": {"Error": [{"Message": "Forbidden"}]}}'
    resp_403.headers = MagicMock()
    resp_403.headers.get.return_value = None
    resp_403.json.return_value = {"Fault": {"Error": [{"Message": "Forbidden"}]}}

    with patch.object(client, "_send_http", return_value=resp_403), pytest.raises(
        QboAuthError
    ) as exc_info:
        client._send_once(**_send_once_kwargs(client))
    assert exc_info.value.is_retryable is False
    assert type(exc_info.value) is QboAuthError


# --------------------------------------------------------------------------- #
# D. Outbox worker decision — _handle_qbo_error
# --------------------------------------------------------------------------- #


def _make_outbox_row(attempts=0):
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id="22222222-2222-2222-2222-222222222222",
        realm_id=REALM_ID,
        request_id="req-1",
        status="in_progress",
        attempts=attempts,
    )


def test_transient_auth_error_attempt_1_schedules_retry_not_dead_letter():
    """Headline invariant: transient auth failures retry, never dead-letter on attempt 1."""
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._handle_qbo_error(
        _make_outbox_row(attempts=0),
        QboAuthTransientError("transient refresh failure"),
    )
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


def test_permanent_auth_error_dead_letters_without_retry():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._handle_qbo_error(
        _make_outbox_row(attempts=0),
        QboAuthError("permanent — re-authorization required"),
    )
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_transient_auth_error_at_max_attempts_minus_one_dead_letters():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._handle_qbo_error(
        _make_outbox_row(attempts=MAX_ATTEMPTS - 1),
        QboAuthTransientError("transient but retries exhausted"),
    )
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_budget_error_still_parks_until_month_reset():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    error = QboBudgetExceededError(
        "budget exhausted",
        month_key="2026-08",
        call_count=475_001,
        budget=500_000,
    )
    worker._handle_qbo_error(_make_outbox_row(attempts=0), error)
    repo.mark_dead_letter.assert_not_called()
    repo.mark_failed.assert_called_once()
    parked_until = repo.mark_failed.call_args.kwargs["next_retry_at"]
    assert parked_until == datetime(2026, 9, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# E. Discovery document — base/helper.py
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    import integrations.intuit.qbo.base.helper as helper

    with helper._discovery_cache_lock:
        helper._discovery_cache = None
    yield
    with helper._discovery_cache_lock:
        helper._discovery_cache = None


def _discovery_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = '{"issuer":"https://oauth.intuit.com"}' if payload else "not-json"
    if payload is not None:
        import json

        resp.text = json.dumps(payload)
    return resp


def test_discovery_get_uses_bounded_connect_read_timeout_tuple():
    from integrations.intuit.qbo.base import helper

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get:
        mock_get.return_value = _discovery_response(payload)
        helper.get_intuit_discovery_document(force_refresh=True)

    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["timeout"] == (
        helper.DISCOVERY_CONNECT_TIMEOUT_SECONDS,
        helper.DISCOVERY_READ_TIMEOUT_SECONDS,
    )


def test_discovery_200_json_object_returns_dict():
    from integrations.intuit.qbo.base.helper import get_intuit_discovery_document

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    with patch(
        "integrations.intuit.qbo.base.helper.requests.get",
        return_value=_discovery_response(payload),
    ):
        result = get_intuit_discovery_document(force_refresh=True)

    assert result == payload


def test_discovery_cache_hit_skips_second_requests_get():
    from integrations.intuit.qbo.base import helper

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get, patch(
        "integrations.intuit.qbo.base.helper.time.monotonic", side_effect=[100.0, 100.5]
    ):
        mock_get.return_value = _discovery_response(payload)
        first = helper.get_intuit_discovery_document(force_refresh=True)
        second = helper.get_intuit_discovery_document()

    assert first == payload
    assert second == payload
    assert mock_get.call_count == 1


def test_discovery_force_refresh_issues_second_requests_get():
    from integrations.intuit.qbo.base import helper

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get, patch(
        "integrations.intuit.qbo.base.helper.time.monotonic", side_effect=[100.0, 100.5, 101.0]
    ):
        mock_get.return_value = _discovery_response(payload)
        helper.get_intuit_discovery_document(force_refresh=True)
        helper.get_intuit_discovery_document(force_refresh=True)

    assert mock_get.call_count == 2


@pytest.mark.parametrize(
    "setup",
    [
        "non_200",
        "unparseable_body",
        "request_exception",
        "non_object_json",
    ],
)
def test_discovery_failure_modes_return_none_or_dict_never_str(setup):
    from integrations.intuit.qbo.base.helper import get_intuit_discovery_document

    if setup == "non_200":
        side_effect = None
        response = _discovery_response({"x": 1}, status_code=503)
    elif setup == "unparseable_body":
        side_effect = None
        response = MagicMock(status_code=200, text="<<not-json>>")
    elif setup == "request_exception":
        import requests

        side_effect = requests.RequestException("dns failure")
        response = None
    else:
        side_effect = None
        response = MagicMock(status_code=200, text='"just-a-string"')

    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get:
        if side_effect is not None:
            mock_get.side_effect = side_effect
        else:
            mock_get.return_value = response
        result = get_intuit_discovery_document(force_refresh=True)

    assert isinstance(result, (dict, type(None)))


def test_discovery_failures_are_not_cached():
    from integrations.intuit.qbo.base.helper import get_intuit_discovery_document

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    bad = MagicMock(status_code=503, text="unavailable")
    good = _discovery_response(payload)

    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get:
        mock_get.side_effect = [bad, good]
        # The SECOND call deliberately does NOT pass force_refresh. If the failed
        # first call had been written to the cache, this call would short-circuit
        # on it and never reach requests.get. Passing force_refresh on BOTH calls
        # bypasses the cache entirely and makes this assertion vacuous — a
        # failure-caching mutation survived the suite that way.
        assert get_intuit_discovery_document(force_refresh=True) is None
        assert get_intuit_discovery_document() == payload
    assert mock_get.call_count == 2


def test_discovery_request_exception_is_not_cached():
    import requests

    from integrations.intuit.qbo.base.helper import get_intuit_discovery_document

    payload = {"token_endpoint": "https://oauth.intuit.com/token"}
    good = _discovery_response(payload)

    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get:
        mock_get.side_effect = [
            requests.RequestException("dns failure"),
            good,
        ]
        assert get_intuit_discovery_document(force_refresh=True) is None
        # Second call deliberately does NOT pass force_refresh — force_refresh
        # bypasses the cache read and would make call_count == 2 vacuous.
        assert get_intuit_discovery_document() == payload
    assert mock_get.call_count == 2


def test_auth_external_client_uses_helper_discovery_document_identity():
    from integrations.intuit.qbo.auth.external import client as auth_client
    from integrations.intuit.qbo.base.helper import get_intuit_discovery_document

    assert auth_client.get_intuit_discovery_document is get_intuit_discovery_document


_PRODUCTION_DISCOVERY_ENDPOINTS = (
    "authorization_endpoint",
    "token_endpoint",
    "revocation_endpoint",
)


@pytest.mark.parametrize("endpoint_name", _PRODUCTION_DISCOVERY_ENDPOINTS)
def test_get_intuit_endpoint_returns_url_when_key_present(endpoint_name):
    from integrations.intuit.qbo.base.helper import get_intuit_endpoint

    url = f"https://oauth.intuit.com/{endpoint_name.replace('_', '-')}"
    payload = {endpoint_name: url}
    with patch(
        "integrations.intuit.qbo.base.helper.requests.get",
        return_value=_discovery_response(payload),
    ):
        result = get_intuit_endpoint(endpoint_name)

    assert result == url
    assert result is not None


@pytest.mark.parametrize("setup", ["non_200", "request_exception"])
def test_get_intuit_endpoint_returns_none_when_document_unavailable(setup):
    from integrations.intuit.qbo.base.helper import get_intuit_endpoint

    if setup == "non_200":
        response = _discovery_response({"token_endpoint": "https://x"}, status_code=503)
        side_effect = None
    else:
        import requests

        response = None
        side_effect = requests.RequestException("dns failure")

    with patch("integrations.intuit.qbo.base.helper.requests.get") as mock_get:
        if side_effect is not None:
            mock_get.side_effect = side_effect
        else:
            mock_get.return_value = response
        result = get_intuit_endpoint("token_endpoint")

    assert result is None


@pytest.mark.parametrize("endpoint_name", _PRODUCTION_DISCOVERY_ENDPOINTS)
def test_get_intuit_endpoint_returns_none_when_key_missing(endpoint_name):
    from integrations.intuit.qbo.base.helper import get_intuit_endpoint

    payload = {"issuer": "https://oauth.intuit.com"}
    with patch(
        "integrations.intuit.qbo.base.helper.requests.get",
        return_value=_discovery_response(payload),
    ):
        result = get_intuit_endpoint(endpoint_name)

    assert result is None


def test_get_intuit_endpoint_returns_falsy_value_not_none_when_key_present():
    """Guards test key membership, not truthiness — empty string must not become None."""
    from integrations.intuit.qbo.base.helper import get_intuit_endpoint

    payload = {"token_endpoint": ""}
    with patch(
        "integrations.intuit.qbo.base.helper.requests.get",
        return_value=_discovery_response(payload),
    ):
        result = get_intuit_endpoint("token_endpoint")

    assert result == ""
    assert result is not None


# --------------------------------------------------------------------------- #
# F. Reclaim — worker.drain_once
# --------------------------------------------------------------------------- #


@contextmanager
def _drain_lock(acquired=True):
    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield acquired

    with patch(
        "integrations.intuit.qbo.outbox.business.worker.qbo_app_lock",
        fake_lock,
    ), patch(
        "integrations.intuit.qbo.outbox.business.worker.writes_allowed",
        return_value=True,
    ):
        yield


def _worker_with_unblocked_budget(repo):
    budget = MagicMock()
    budget.status.return_value = MagicMock(blocked=False)
    return QboOutboxWorker(repo=repo, api_budget=budget)


def test_reclaim_after_seconds_defaults_to_900(monkeypatch):
    monkeypatch.delenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", raising=False)
    assert reclaim_after_seconds() == 900


def test_reclaim_after_seconds_honors_env_override(monkeypatch):
    monkeypatch.setenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", "1200")
    assert reclaim_after_seconds() == 1200


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
def test_reclaim_after_seconds_invalid_env_falls_back_to_default(raw, monkeypatch):
    monkeypatch.setenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", raw)
    assert reclaim_after_seconds() == DEFAULT_RECLAIM_AFTER_SECONDS


def test_drain_once_reclaims_before_claim_inside_drain_lock():
    repo = MagicMock()
    order = []

    def reclaim_stranded(**_kwargs):
        order.append("reclaim_stranded")
        return []

    def claim_next_pending():
        order.append("claim_next_pending")
        return None

    repo.reclaim_stranded = reclaim_stranded
    repo.claim_next_pending = claim_next_pending
    worker = _worker_with_unblocked_budget(repo)

    with _drain_lock(acquired=True):
        assert worker.drain_once() is False

    assert order == ["reclaim_stranded", "claim_next_pending"]


def test_drain_once_skips_reclaim_when_drain_lock_not_acquired():
    repo = MagicMock()
    worker = _worker_with_unblocked_budget(repo)

    with _drain_lock(acquired=False):
        assert worker.drain_once() is False

    repo.reclaim_stranded.assert_not_called()
    repo.claim_next_pending.assert_not_called()


def test_drain_once_reclaim_failure_is_fail_open_claim_still_runs():
    repo = MagicMock()
    repo.reclaim_stranded.side_effect = RuntimeError("sproc missing")
    repo.claim_next_pending.return_value = None
    worker = _worker_with_unblocked_budget(repo)

    with _drain_lock(acquired=True):
        assert worker.drain_once() is False

    repo.claim_next_pending.assert_called_once()


def test_reclaim_stranded_receives_stale_before_and_max_attempts(monkeypatch):
    repo = MagicMock()
    repo.reclaim_stranded.return_value = []
    repo.claim_next_pending.return_value = None
    # Never set an env override to the same value as the default under test — that
    # cannot distinguish "override honored" from "default used and env ignored".
    monkeypatch.setenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", "120")
    worker = _worker_with_unblocked_budget(repo)
    fixed_now = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

    with _drain_lock(acquired=True), patch(
        "integrations.intuit.qbo.outbox.business.worker.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        worker.drain_once()

    kwargs = repo.reclaim_stranded.call_args.kwargs
    assert kwargs["max_attempts"] == MAX_ATTEMPTS
    stale_before = kwargs["stale_before"]
    delta = abs((fixed_now - stale_before).total_seconds() - 120)
    assert delta <= 1.0


def test_reclaim_stranded_uses_default_stale_window_when_env_unset(monkeypatch):
    repo = MagicMock()
    repo.reclaim_stranded.return_value = []
    repo.claim_next_pending.return_value = None
    monkeypatch.delenv("QBO_OUTBOX_RECLAIM_AFTER_SECONDS", raising=False)
    worker = _worker_with_unblocked_budget(repo)
    fixed_now = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)

    with _drain_lock(acquired=True), patch(
        "integrations.intuit.qbo.outbox.business.worker.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        worker.drain_once()

    kwargs = repo.reclaim_stranded.call_args.kwargs
    assert kwargs["max_attempts"] == MAX_ATTEMPTS
    stale_before = kwargs["stale_before"]
    delta = abs((fixed_now - stale_before).total_seconds() - DEFAULT_RECLAIM_AFTER_SECONDS)
    assert delta <= 1.0


def test_reclaimed_row_emits_warning_log_record(caplog):
    repo = MagicMock()
    repo.reclaim_stranded.return_value = [
        {
            "public_id": "outbox-reclaimed",
            "entity_type": "Bill",
            "entity_public_id": "33333333-3333-3333-3333-333333333333",
            "kind": "sync_bill_to_qbo",
            "attempts": 1,
            "started_at": "2026-08-11 11:00:00",
            "status": "failed",
        }
    ]
    repo.claim_next_pending.return_value = None
    worker = _worker_with_unblocked_budget(repo)

    with _drain_lock(acquired=True), caplog.at_level("WARNING"):
        worker.drain_once()

    assert any(
        "qbo.outbox.row.reclaimed_stranded" in record.message
        or getattr(record, "event_name", "") == "qbo.outbox.row.reclaimed_stranded"
        for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# G. Bill enqueue durability — BillService._enqueue_qbo_sync
# --------------------------------------------------------------------------- #


def test_enqueue_qbo_sync_still_enqueues_when_token_unusable():
    """Defect: gating enqueue on token health silently dropped the push with no durable row."""
    from entities.bill.business.service import BillService

    bill = MagicMock(public_id="44444444-4444-4444-4444-444444444444")
    expired_auth = _make_auth(
        modified_datetime="2020-01-01 00:00:00",
        expires_in=1,
        access_token=None,
    )
    svc = BillService()
    svc._qbo_auth_service = MagicMock()
    svc._qbo_auth_service.read_all.return_value = [expired_auth]
    svc._qbo_auth_service.ensure_valid_token = MagicMock()

    outbox_row = MagicMock(public_id="55555555-5555-5555-5555-555555555555")
    with patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as outbox_cls:
        outbox_cls.return_value.enqueue.return_value = outbox_row
        result = svc._enqueue_qbo_sync(bill)

    outbox_cls.return_value.enqueue.assert_called_once_with(
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id=str(bill.public_id),
        realm_id=REALM_ID,
    )
    assert result["qbo_sync_queued"] is True
    svc._qbo_auth_service.ensure_valid_token.assert_not_called()


def test_enqueue_qbo_sync_never_calls_ensure_valid_token():
    from entities.bill.business.service import BillService

    bill = MagicMock(public_id="44444444-4444-4444-4444-444444444444")
    svc = BillService()
    svc._qbo_auth_service = MagicMock()
    svc._qbo_auth_service.read_all.return_value = [_make_auth()]
    svc._qbo_auth_service.ensure_valid_token = MagicMock()

    with patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as outbox_cls:
        outbox_cls.return_value.enqueue.return_value = MagicMock(public_id="outbox")
        svc._enqueue_qbo_sync(bill)

    svc._qbo_auth_service.ensure_valid_token.assert_not_called()


def test_enqueue_qbo_sync_skipped_when_no_auth_record():
    from entities.bill.business.service import BillService

    bill = MagicMock(public_id="44444444-4444-4444-4444-444444444444")
    svc = BillService()
    svc._qbo_auth_service = MagicMock()
    svc._qbo_auth_service.read_all.return_value = []

    with patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as outbox_cls:
        result = svc._enqueue_qbo_sync(bill)

    outbox_cls.assert_not_called()
    assert result["qbo_sync_queued"] is False
    # This method has a catch-all except that manufactures the same success=False
    # shape, so a shape-only assertion is vacuous.
    assert result["message"] == "No QBO auth record configured"
    assert result["errors"] == [
        {"step": "qbo_enqueue", "error": "No QBO auth record configured"}
    ]
    assert not result["message"].startswith("Failed to enqueue QBO sync:")


# --------------------------------------------------------------------------- #
# H. Unpark script contract
# --------------------------------------------------------------------------- #


_UNPARK_SCRIPT_MODULE_NAME = "_test_load_unpark_qbo_outbox_budget"


def _load_unpark_script_module():
    """Load unpark script via importlib without leaking sys.path or sys.modules."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "unpark_qbo_outbox_budget.py"
    # Parallel work in this repo shares one test session; a leaked sys.path entry
    # from the script's `sys.path.insert(0, ".")` is cross-file contamination
    # that is painful to trace back here.
    saved_path = sys.path.copy()
    saved_module = sys.modules.get(_UNPARK_SCRIPT_MODULE_NAME)
    try:
        spec = importlib.util.spec_from_file_location(_UNPARK_SCRIPT_MODULE_NAME, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_UNPARK_SCRIPT_MODULE_NAME] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        if saved_module is None:
            sys.modules.pop(_UNPARK_SCRIPT_MODULE_NAME, None)
        else:
            sys.modules[_UNPARK_SCRIPT_MODULE_NAME] = saved_module


def _fake_parked_outbox_rows(count=2):
    rows = []
    for i in range(count):
        rows.append(
            (
                100 + i,
                "sync_bill_to_qbo",
                "Bill",
                f"22222222-2222-2222-2222-2222222222{i:02d}",
                1 + i,
                "2026-09-01 00:00:00",
                "Parked: monthly QBO API budget exhausted (budget exhausted)",
            )
        )
    return rows


def _run_unpark_main(argv, *, rows, rowcount=None):
    unpark = _load_unpark_script_module()
    if rowcount is None:
        rowcount = len(rows)

    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount

    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_get_connection():
        yield conn

    with patch.object(unpark, "get_connection", fake_get_connection), patch(
        "sys.argv", argv
    ):
        exit_code = unpark.main()

    return unpark, cursor, conn, exit_code


def _find_outbox_update_call(cursor):
    for call in cursor.execute.call_args_list:
        sql = call.args[0]
        if "UPDATE qbo.Outbox" in sql:
            return call
    pytest.fail("No UPDATE qbo.Outbox execute call found")


def _assert_update_toctou_guards(update_call, unpark_module, expected_ids):
    sql = update_call.args[0]
    params = update_call.args[1:]
    assert "Status = 'failed'" in sql
    assert "NextRetryAt > SYSUTCDATETIME()" in sql
    assert "LastError LIKE" in sql
    # assert POSITION, never membership; a membership assertion passed a fully scrambled bind.
    placeholder_count = sql.count("?")
    assert placeholder_count == len(params), (
        f"SQL has {placeholder_count} '?' placeholders but {len(params)} bound parameters"
    )
    now_value = params[0]
    expected_params = (
        now_value,
        now_value,
        *expected_ids,
        unpark_module.PARKED_LAST_ERROR_PREFIX,
    )
    assert params == expected_params


def test_unpark_script_parked_prefix_matches_worker_budget_park_last_error():
    """If PARKED_LAST_ERROR_PREFIX drifts from the worker f-string the script silently no-ops."""
    unpark = _load_unpark_script_module()
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    error = QboBudgetExceededError(
        "cap reached",
        month_key="2026-08",
        call_count=475_000,
        budget=500_000,
    )
    worker._handle_qbo_error(_make_outbox_row(attempts=0), error)
    repo.mark_failed.assert_called_once()
    worker_last_error = repo.mark_failed.call_args.kwargs["last_error"]
    # This test must derive the string FROM THE WORKER, never restate it, because
    # a literal copy here only catches drift on the script side and leaves the
    # worker side — the likelier drift — undetected. It silently turns the unpark
    # script into a permanent no-op.
    script_prefix = unpark.PARKED_LAST_ERROR_PREFIX.rstrip("%")
    assert worker_last_error.startswith(script_prefix)


def test_unpark_script_parked_last_error_prefix_is_like_pattern():
    unpark = _load_unpark_script_module()
    assert unpark.PARKED_LAST_ERROR_PREFIX.endswith("%")


def test_unpark_script_apply_update_reasserts_toctou_predicate():
    rows = _fake_parked_outbox_rows()
    unpark, cursor, conn, exit_code = _run_unpark_main(
        ["unpark_qbo_outbox_budget.py", "--apply"],
        rows=rows,
    )

    assert exit_code == 0
    update_call = _find_outbox_update_call(cursor)
    expected_ids = [row[0] for row in rows]
    _assert_update_toctou_guards(update_call, unpark, expected_ids)
    # The absence assertion is the half that matters — a fragment that is
    # accidentally unconditional is invisible to a presence-only test.
    assert "Attempts" not in update_call.args[0]
    conn.commit.assert_called_once()


def test_unpark_script_reset_attempts_update_reasserts_toctou_predicate():
    rows = _fake_parked_outbox_rows()
    unpark, cursor, conn, exit_code = _run_unpark_main(
        ["unpark_qbo_outbox_budget.py", "--apply", "--reset-attempts"],
        rows=rows,
    )

    assert exit_code == 0
    update_call = _find_outbox_update_call(cursor)
    expected_ids = [row[0] for row in rows]
    _assert_update_toctou_guards(update_call, unpark, expected_ids)
    assert "Attempts = 0" in update_call.args[0]
    conn.commit.assert_called_once()


def test_unpark_script_dry_run_issues_no_update_or_commit():
    rows = _fake_parked_outbox_rows()
    _unpark, cursor, conn, exit_code = _run_unpark_main(
        ["unpark_qbo_outbox_budget.py"],
        rows=rows,
    )

    assert exit_code == 0
    update_calls = [
        call for call in cursor.execute.call_args_list if "UPDATE" in call.args[0]
    ]
    assert update_calls == []
    conn.commit.assert_not_called()


def test_unpark_script_rowcount_mismatch_prints_warning(capsys):
    rows = _fake_parked_outbox_rows(count=2)
    _unpark, cursor, conn, exit_code = _run_unpark_main(
        ["unpark_qbo_outbox_budget.py", "--apply"],
        rows=rows,
        rowcount=1,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "WARNING: matched 2 row(s) but updated 1" in out
    assert "1 row(s) changed state between the scan and the update" in out
    assert "Unparked 1 row(s)" in out
