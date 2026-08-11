"""U-216: QboClient API serialization must never expose client_secret."""

from __future__ import annotations

import json

import pytest

from integrations.intuit.qbo.client.api import router as qbo_client_router
from integrations.intuit.qbo.client.api.schemas import QboClientCreate, QboClientUpdate
from integrations.intuit.qbo.client.business.model import QboClient

_SENTINEL = "SUPERSECRET-DO-NOT-LEAK"


def _make_qbo_client() -> QboClient:
    return QboClient(app="prod", client_id="cid", client_secret=_SENTINEL)


class _FakeQboClientService:
    def create(self, *, app: str, client_id: str, client_secret: str) -> QboClient:
        return _make_qbo_client()

    def read_all(self) -> list[QboClient]:
        return [_make_qbo_client()]

    def read_by_app(self, app: str) -> QboClient:
        return _make_qbo_client()

    def update_by_app(self, app: str, client_id: str, client_secret: str) -> QboClient:
        return _make_qbo_client()

    def delete_by_app(self, app: str) -> QboClient:
        return _make_qbo_client()


def _assert_no_secret_in_response(response: dict) -> None:
    serialized = json.dumps(response, default=str)
    assert _SENTINEL not in serialized
    assert '"client_secret"' not in serialized


@pytest.fixture
def fake_qbo_client_service(monkeypatch: pytest.MonkeyPatch) -> _FakeQboClientService:
    fake = _FakeQboClientService()
    monkeypatch.setattr(qbo_client_router, "service", fake)
    return fake


def test_to_dict_omits_client_secret_key():
    client = QboClient(app="prod", client_id="cid", client_secret=_SENTINEL)
    assert "client_secret" not in client.to_dict()


def test_json_dumps_does_not_contain_secret_plaintext():
    client = QboClient(app="prod", client_id="cid", client_secret=_SENTINEL)
    serialized = json.dumps(client.to_dict())
    assert _SENTINEL not in serialized


def test_client_secret_set_true_when_secret_present():
    client = QboClient(app="prod", client_id="cid", client_secret=_SENTINEL)
    assert client.to_dict()["client_secret_set"] is True


def test_client_secret_set_false_when_secret_none():
    client = QboClient(app="prod", client_id="cid", client_secret=None)
    assert client.to_dict()["client_secret_set"] is False


def test_client_secret_set_false_when_secret_empty_string():
    client = QboClient(app="prod", client_id="cid", client_secret="")
    assert client.to_dict()["client_secret_set"] is False


def test_client_secret_attribute_still_returns_plaintext():
    client = QboClient(app="prod", client_id="cid", client_secret=_SENTINEL)
    assert client.client_secret == _SENTINEL


def test_to_dict_carries_app_and_client_id_unchanged():
    client = QboClient(app="prod", client_id="my-client-id", client_secret=_SENTINEL)
    d = client.to_dict()
    assert d["app"] == "prod"
    assert d["client_id"] == "my-client-id"


def test_create_router_redacts_client_secret(fake_qbo_client_service: _FakeQboClientService):
    body = QboClientCreate(app="prod", client_id="cid", client_secret="request-body-secret")
    response = qbo_client_router.create_qbo_client_router(body=body, current_user={})
    _assert_no_secret_in_response(response)


def test_read_all_router_redacts_client_secret(fake_qbo_client_service: _FakeQboClientService):
    response = qbo_client_router.get_qbo_clients_router(current_user={})
    _assert_no_secret_in_response(response)


def test_read_by_app_router_redacts_client_secret(fake_qbo_client_service: _FakeQboClientService):
    response = qbo_client_router.get_qbo_client_by_app_router(app="prod", current_user={})
    _assert_no_secret_in_response(response)


def test_update_router_redacts_client_secret(fake_qbo_client_service: _FakeQboClientService):
    body = QboClientUpdate(app="prod", client_id="cid", client_secret="request-body-secret")
    response = qbo_client_router.update_qbo_client_by_app_router(app="prod", body=body, current_user={})
    _assert_no_secret_in_response(response)


def test_delete_router_redacts_client_secret(fake_qbo_client_service: _FakeQboClientService):
    response = qbo_client_router.delete_qbo_client_by_app_router(app="prod", current_user={})
    _assert_no_secret_in_response(response)
