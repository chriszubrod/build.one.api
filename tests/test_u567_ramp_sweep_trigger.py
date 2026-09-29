"""U-567 — the admin trigger for the Ramp chaser sweep.

Route-level only. The straggler counters this unit added are pinned in
tests/test_u549_ramp_chaser.py, driven through the public `run_chaser_sweep`
with the fakes that file already owns.
"""

import inspect

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app import app
from integrations.ramp.transaction.business.service import RampChaserSweepStats
from shared.api import admin


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def drain_secret_configured(monkeypatch):
    monkeypatch.setenv("DRAIN_SECRET", "unit-test-drain-secret")


def _populated_sweep_stats() -> RampChaserSweepStats:
    return RampChaserSweepStats(
        transactions_fetched=12,
        stragglers_refetched=2,
        stragglers_gone_from_ramp=1,
        upserted=5,
        resolved=3,
        unroutable_persisted=1,
    )


@pytest.fixture
def mock_ramp_sweep_service():
    """Patch the ENTITY façade the route calls — not the integration service.

    The route deliberately goes through RampTransactionFollowUpService, which is
    the composition root that binds the repo to the integration service. Patching
    anything below it would let a regression that bypasses the façade stay green.
    """
    mock_svc = MagicMock()
    mock_svc.run_chaser_sweep.return_value = _populated_sweep_stats()
    with patch(
        "entities.ramp_transaction_follow_up.business.service.RampTransactionFollowUpService",
        return_value=mock_svc,
    ) as ctor:
        yield mock_svc, ctor


def test_ramp_chaser_sweep_asdict_trap_not_silent_type_name(
    client, drain_secret_configured, mock_ramp_sweep_service
):
    """run_chaser_sweep returns a @dataclass and _timed silently discards those.

    `_timed` does `payload = result if isinstance(result, (dict, list, int, str,
    type(None))) else {"type": type(result).__name__}` — so returning the stats
    raw yields status=ok with every counter GONE. The route must asdict().
    """
    mock_svc, ctor = mock_ramp_sweep_service
    response = client.post(
        "/api/v1/admin/ramp-chaser/sweep",
        headers={"X-Drain-Secret": "unit-test-drain-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["job"] == "ramp_chaser.sweep"

    result = body["result"]
    assert result != {"type": "RampChaserSweepStats"}, "the _timed dataclass trap fired"
    assert result["transactions_fetched"] == 12
    assert result["upserted"] == 5
    assert result["resolved"] == 3
    assert result["stragglers_refetched"] == 2
    assert result["stragglers_gone_from_ramp"] == 1

    ctor.assert_called_once_with()
    mock_svc.run_chaser_sweep.assert_called_once_with()


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-header"),
        pytest.param({"X-Drain-Secret": "wrong-secret"}, id="wrong-secret"),
    ],
)
def test_ramp_chaser_sweep_401_and_sweep_never_runs(
    client, drain_secret_configured, mock_ramp_sweep_service, headers
):
    """Both shapes reach the same hmac.compare_digest mismatch — one branch, one test."""
    mock_svc, ctor = mock_ramp_sweep_service
    response = client.post("/api/v1/admin/ramp-chaser/sweep", headers=headers)
    assert response.status_code == 401
    ctor.assert_not_called()
    mock_svc.run_chaser_sweep.assert_not_called()


def test_ramp_chaser_sweep_route_takes_no_parameters():
    """The window comes from RAMP_CHASER_WINDOW_DAYS, never from the caller.

    Asserted on the signature rather than by sending an empty query string — the
    latter only restates what the test itself sent. A caller-supplied window would
    be an unbounded Ramp fan-out behind a single admin call.
    """
    assert inspect.signature(admin.ramp_chaser_sweep_router).parameters == {}
