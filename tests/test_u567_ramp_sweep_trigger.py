"""U-567 — admin trigger for Ramp chaser sweep + straggler refetch counters."""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from integrations.ramp.transaction.business.service import (
    RampChaserSweepStats,
    RampTransactionService,
)


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
        skipped_approval_only=0,
        refreshed_tracked_approval_only=0,
        skipped_inactive_cardholder=0,
        unroutable_persisted=1,
        flag_unknown=0,
    )


@pytest.fixture
def mock_ramp_sweep_service():
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
    """_timed drops dataclass bodies — route must asdict() or result is useless."""
    mock_svc, _ = mock_ramp_sweep_service
    response = client.post(
        "/api/v1/admin/ramp-chaser/sweep",
        headers={"X-Drain-Secret": "unit-test-drain-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["job"] == "ramp_chaser.sweep"
    result = body["result"]
    assert result != {"type": "RampChaserSweepStats"}
    assert result["transactions_fetched"] == 12
    assert result["upserted"] == 5
    assert result["resolved"] == 3
    assert result["stragglers_refetched"] == 2
    assert result["stragglers_gone_from_ramp"] == 1
    mock_svc.run_chaser_sweep.assert_called_once_with()


def test_ramp_chaser_sweep_401_without_drain_secret_header(
    client, drain_secret_configured, mock_ramp_sweep_service
):
    mock_svc, _ = mock_ramp_sweep_service
    response = client.post("/api/v1/admin/ramp-chaser/sweep")
    assert response.status_code == 401
    mock_svc.run_chaser_sweep.assert_not_called()


def test_ramp_chaser_sweep_401_on_wrong_drain_secret(
    client, drain_secret_configured, mock_ramp_sweep_service
):
    mock_svc, _ = mock_ramp_sweep_service
    response = client.post(
        "/api/v1/admin/ramp-chaser/sweep",
        headers={"X-Drain-Secret": "wrong-secret"},
    )
    assert response.status_code == 401
    mock_svc.run_chaser_sweep.assert_not_called()


def test_ramp_chaser_sweep_no_query_params_service_called_bare(
    client, drain_secret_configured, mock_ramp_sweep_service
):
    mock_svc, ctor = mock_ramp_sweep_service
    response = client.post(
        "/api/v1/admin/ramp-chaser/sweep",
        headers={"X-Drain-Secret": "unit-test-drain-secret"},
    )
    assert response.status_code == 200
    assert response.request.url.query == b""
    mock_svc.run_chaser_sweep.assert_called_once_with()
    ctor.assert_called_once()


class _StragglerRepo:
    def __init__(self, unresolved_ids: List[str]) -> None:
        self._ids = unresolved_ids

    def read_unresolved_ramp_transaction_ids(
        self, *, conn: Optional[Any] = None
    ) -> List[str]:
        return list(self._ids)

    def upsert_open_item(self, *, conn: Optional[Any] = None, **kwargs: Any) -> Any:
        return kwargs

    def mark_resolved(
        self, *, ramp_transaction_id: str, conn: Optional[Any] = None
    ) -> Any:
        return None


class _StragglerTxClient:
    def __init__(self, responses: Dict[str, Optional[Dict[str, Any]]]) -> None:
        self._responses = responses
        self.calls: List[str] = []

    def get_transaction(self, ramp_transaction_id: str) -> Optional[Dict[str, Any]]:
        self.calls.append(ramp_transaction_id)
        return self._responses.get(ramp_transaction_id)


def _minimal_txn(txn_id: str) -> Dict[str, Any]:
    return {
        "id": txn_id,
        "all_requirements_met_and_approved": False,
        "memo": "",
        "receipts": [],
        "merchant_name": "M",
        "amount": "1.00",
        "user_transaction_time": "2026-09-01T12:00:00Z",
        "card_holder": {"user_id": "u1", "first_name": "A", "last_name": "B"},
    }


def test_stragglers_refetched_counts_every_straggler_iteration():
    repo = _StragglerRepo(["gone-1", "extra-1", "extra-2"])
    window: Dict[str, Dict[str, Any]] = {"in-window": _minimal_txn("in-window")}
    tx_client = _StragglerTxClient(
        {
            "gone-1": None,
            "extra-1": _minimal_txn("extra-1"),
            "extra-2": _minimal_txn("extra-2"),
        }
    )
    svc = RampTransactionService(
        settings=MagicMock(ramp_chaser_window_days=90, ramp_api_base_url="https://api.ramp.com"),
        auth_service=MagicMock(),
        http_client=MagicMock(),
        transaction_client=tx_client,
        user_service=MagicMock(),
    )
    stats = RampChaserSweepStats()
    svc._process_chaser_window(
        repo=repo,
        conn=None,
        roster={"u1": MagicMock(is_active=True, email="a@b.com")},
        window=window,
        stats=stats,
    )
    assert stats.stragglers_refetched == 3
    assert set(tx_client.calls) == {"gone-1", "extra-1", "extra-2"}


def test_stragglers_gone_from_ramp_only_falsy_and_not_in_window():
    repo = _StragglerRepo(["missing-a", "missing-b", "found-1"])
    window: Dict[str, Dict[str, Any]] = {}
    tx_client = _StragglerTxClient(
        {
            "missing-a": None,
            "missing-b": {},
            "found-1": _minimal_txn("found-1"),
        }
    )
    svc = RampTransactionService(
        settings=MagicMock(ramp_chaser_window_days=90, ramp_api_base_url="https://api.ramp.com"),
        auth_service=MagicMock(),
        http_client=MagicMock(),
        transaction_client=tx_client,
        user_service=MagicMock(),
    )
    stats = RampChaserSweepStats()
    svc._process_chaser_window(
        repo=repo,
        conn=None,
        roster={"u1": MagicMock(is_active=True, email="a@b.com")},
        window=window,
        stats=stats,
    )
    assert stats.stragglers_gone_from_ramp == 2
    assert "missing-a" not in window
    assert "missing-b" not in window
    assert "found-1" in window


def test_stragglers_counters_zero_when_no_stragglers():
    repo = _StragglerRepo([])
    window = {"only": _minimal_txn("only")}
    tx_client = _StragglerTxClient({})
    svc = RampTransactionService(
        settings=MagicMock(ramp_chaser_window_days=90, ramp_api_base_url="https://api.ramp.com"),
        auth_service=MagicMock(),
        http_client=MagicMock(),
        transaction_client=tx_client,
        user_service=MagicMock(),
    )
    stats = RampChaserSweepStats()
    svc._process_chaser_window(
        repo=repo,
        conn=None,
        roster={"u1": MagicMock(is_active=True, email="a@b.com")},
        window=window,
        stats=stats,
    )
    assert stats.stragglers_refetched == 0
    assert stats.stragglers_gone_from_ramp == 0
    assert tx_client.calls == []
