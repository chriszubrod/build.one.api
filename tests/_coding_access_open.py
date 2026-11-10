"""U-652d4: shared autouse fixture for pins that drive the REAL CodingSuggestionService.

The service's assert_accessible (row access) and the router's recode write gate are not what these pins
test. The fixture makes the access helpers pass and checks the router gate through the client module's
CURRENT attribute, so a test that patches the gate at integrations.intuit.qbo.base.client keeps its
meaning. Import it into a test module (it is autouse there); never from conftest."""
from unittest.mock import MagicMock, patch

import pytest

import integrations.intuit.qbo.base.client as _client


def _router_gate():
    """Open unless the test patched the client gate (then honour that patch). The router imports the
    gate by name, so a client-level patch alone never reaches it."""
    if isinstance(_client.recode_write_gate_reason, MagicMock):
        return _client.recode_write_gate_reason()
    return None


@pytest.fixture(autouse=True)
def coding_access_open():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    with patch.object(CodingSuggestionService, "assert_accessible", return_value=None), \
            patch("entities.coding_suggestion.api.router.recode_write_gate_reason", side_effect=_router_gate):
        yield
