"""U-652d4 item 10: the confirm router checks the recode write gate BEFORE the claim.

A claim is a durable ClaimedByUserId/ClaimedAt write. With the gate closed the old order claimed, then
confirm() refused and released; a failed release stranded the claim for 900 s. Every test names the
mutant it kills. Nothing opens a DB/HTTP connection: the service is a MagicMock (see the f2 helpers)."""
from unittest.mock import patch

import pytest

from tests.test_u551bf1f2_confirm_status_guard import PUBLIC_ID, _route, _route_service

ROUTER_GATE = "entities.coding_suggestion.api.router.recode_write_gate_reason"


@pytest.mark.parametrize("reason", ["qbo_writes_disabled", "recode_writes_disabled"])
def test_closed_gate_is_422_with_the_plain_reason_and_zero_claim_calls(reason):
    # kills: the old order (claim, then confirm refuses) - a claim is written before the refusal
    service = _route_service({"status": "enqueued"})
    response, err = _route(service, gate=reason)
    assert response is None
    assert err is not None and err.status_code == 422 and err.detail == reason
    service.claim.assert_not_called()
    service.confirm.assert_not_called()
    service.release.assert_not_called()


def test_closed_gate_refuses_even_when_the_caller_already_holds_the_claim():
    # kills: a gate check placed after the already-claimed branch (it would confirm under a held claim)
    service = _route_service({"status": "enqueued"}, claimed_by=17)
    response, err = _route(service, gate="qbo_writes_disabled")
    assert response is None and err.status_code == 422 and err.detail == "qbo_writes_disabled"
    service.confirm.assert_not_called()


def test_open_gate_still_claims_then_confirms():
    # kills: a gate check that refuses an OPEN gate (or skips the claim when it is open)
    service = _route_service({"status": "enqueued"})
    response, err = _route(service)
    assert err is None and response is not None
    service.claim.assert_called_once()
    service.confirm.assert_called_once()
