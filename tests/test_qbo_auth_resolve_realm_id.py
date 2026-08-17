"""Pure-logic tests for QboAuthService.resolve_realm_id (U-254)."""
from unittest.mock import MagicMock

import pytest

from integrations.intuit.qbo.auth.business.service import QboAuthService


def test_resolve_realm_id_empty_raises():
    repo = MagicMock()
    repo.read_all.return_value = []
    service = QboAuthService(repo=repo)

    with pytest.raises(ValueError, match="No QBO authentication found. Please connect your QuickBooks account first."):
        service.resolve_realm_id()


def test_resolve_realm_id_custom_no_auth_message():
    repo = MagicMock()
    repo.read_all.return_value = []
    service = QboAuthService(repo=repo)

    with pytest.raises(ValueError, match="custom text"):
        service.resolve_realm_id(no_auth_message="custom text")


def test_resolve_realm_id_returns_first_realm_id():
    auth = MagicMock()
    auth.realm_id = "realm-123"
    repo = MagicMock()
    repo.read_all.return_value = [auth]
    service = QboAuthService(repo=repo)

    assert service.resolve_realm_id() == "realm-123"
