"""Pure-logic unit tests for Box auth resilience (U-224). Covers: BoxMalformedResponseError
reclassification of the two malformed-HTTP-200 token-mint response branches."""
from unittest.mock import MagicMock, patch

import pytest

from integrations.box.auth.business.service import BoxAuthService
from integrations.box.base.errors import (
    BoxAuthError,
    BoxMalformedResponseError,
    BoxServerError,
)


def _make_settings(**overrides):
    settings = MagicMock()
    settings.box_client_id = "client-id"
    settings.box_client_secret = "client-secret"
    settings.box_as_user_id = "31760447449"
    settings.box_enterprise_id = None
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _http_200_response(*, json_side_effect=None, json_return=None):
    response = MagicMock()
    response.status_code = 200
    response.text = ""
    if json_side_effect is not None:
        response.json.side_effect = json_side_effect
    else:
        response.json.return_value = json_return
    return response


def test_mint_non_json_body_raises_box_malformed_response_error():
    svc = BoxAuthService(settings=_make_settings())
    bad_response = _http_200_response(json_side_effect=ValueError("not json"))

    with patch(
        "integrations.box.auth.business.service.httpx.post",
        return_value=bad_response,
    ), pytest.raises(BoxMalformedResponseError) as exc_info:
        svc._mint_and_cache()

    assert type(exc_info.value) is BoxMalformedResponseError
    assert exc_info.value.is_retryable is True


def test_mint_missing_access_token_raises_box_malformed_response_error():
    svc = BoxAuthService(settings=_make_settings())
    response = _http_200_response(json_return={"token_type": "bearer"})

    with patch(
        "integrations.box.auth.business.service.httpx.post",
        return_value=response,
    ), pytest.raises(BoxMalformedResponseError) as exc_info:
        svc._mint_and_cache()

    assert type(exc_info.value) is BoxMalformedResponseError
    assert exc_info.value.is_retryable is True


def test_mint_non_dict_json_body_raises_box_malformed_response_error():
    """JSON list has no access_token key — payload.get path yields None."""
    svc = BoxAuthService(settings=_make_settings())
    response = _http_200_response(json_return=["not", "a", "dict"])

    with patch(
        "integrations.box.auth.business.service.httpx.post",
        return_value=response,
    ), pytest.raises(BoxMalformedResponseError) as exc_info:
        svc._mint_and_cache()

    assert type(exc_info.value) is BoxMalformedResponseError
    assert exc_info.value.is_retryable is True


def test_mint_400_credentials_rejected_still_raises_plain_box_auth_error():
    svc = BoxAuthService(settings=_make_settings())
    response = MagicMock()
    response.status_code = 400
    response.text = '{"error":"invalid_client","error_description":"bad creds"}'
    response.json.return_value = {
        "error": "invalid_client",
        "error_description": "bad creds",
    }

    with patch(
        "integrations.box.auth.business.service.httpx.post",
        return_value=response,
    ), pytest.raises(BoxAuthError) as exc_info:
        svc._mint_and_cache()

    assert type(exc_info.value) is BoxAuthError
    assert exc_info.value.is_retryable is False


def test_box_malformed_response_error_hierarchy():
    assert issubclass(BoxMalformedResponseError, BoxServerError)
    assert not issubclass(BoxMalformedResponseError, BoxAuthError)
