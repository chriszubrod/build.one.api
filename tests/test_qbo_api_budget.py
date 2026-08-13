"""Pure-logic unit tests for the QBO API meter + monthly-budget breaker (U-211).

Covers: threshold math and enforcement gate in budget.py, fail-open on meter
errors, month-key/reset-date computation, the shared client's _send_http
refuse-before-send behavior, and the outbox worker's park-don't-dead-letter
handling of QboBudgetExceededError.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.budget import (
    BudgetStatus,
    QboApiBudget,
    current_month_key,
    monthly_call_budget,
    next_month_start,
    reset_at_for_month,
)
from integrations.intuit.qbo.base.client import QboHttpClient, _TIMEOUT_TIERS
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import MAX_ATTEMPTS, QboOutboxWorker

REALM_ID = "realm-test"


def _budget_with_count(count):
    """QboApiBudget whose repo reports `count` after increment and on read."""
    repo = MagicMock()
    repo.increment.return_value = count
    repo.read_month_total.return_value = count
    return QboApiBudget(repo=repo)


def _make_status(*, blocked=False, call_count=None):
    """blocked/warning are derived properties — steer them via call_count."""
    if call_count is None:
        call_count = 475_001 if blocked else 0
    return BudgetStatus(
        month_key="2026-08",
        call_count=call_count,
        budget=500_000,
        block_threshold=475_000,
        warn_threshold=400_000,
        enforced=True,
    )


def _budget_exceeded_error():
    return QboBudgetExceededError(
        "budget exhausted",
        month_key="2026-08",
        call_count=475_001,
        budget=500_000,
    )


# --------------------------------------------------------------------------- #
# Threshold math + enforcement gate
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_budget_env(monkeypatch):
    """Default-threshold tests must not inherit ambient QBO_* env overrides."""
    for var in (
        "QBO_MONTHLY_CALL_BUDGET",
        "QBO_BUDGET_BLOCK_PCT",
        "QBO_BUDGET_WARN_PCT",
        "QBO_BUDGET_ENFORCE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_record_call_under_thresholds_is_clean():
    status = _budget_with_count(100).record_call(REALM_ID)
    assert status.call_count == 100
    assert not status.blocked
    assert not status.warning
    assert not status.meter_unavailable


def test_record_call_warn_band_sets_warning_not_blocked():
    # Default thresholds: warn at 80% (400,000), block at 95% (475,000).
    status = _budget_with_count(400_000).record_call(REALM_ID)
    assert status.warning
    assert not status.blocked


def test_record_call_at_block_threshold_blocks():
    status = _budget_with_count(475_000).record_call(REALM_ID)
    assert status.blocked


def test_enforcement_kill_switch_disables_blocking_but_not_warning():
    with patch.dict("os.environ", {"QBO_BUDGET_ENFORCE": "false"}):
        status = _budget_with_count(499_999).record_call(REALM_ID)
    assert not status.blocked
    assert status.warning
    assert not status.enforced


def test_custom_budget_and_fractions_from_env():
    env = {
        "QBO_MONTHLY_CALL_BUDGET": "1000",
        "QBO_BUDGET_BLOCK_PCT": "0.9",
        "QBO_BUDGET_WARN_PCT": "0.5",
    }
    with patch.dict("os.environ", env):
        status = _budget_with_count(900).record_call(REALM_ID)
        assert status.budget == 1000
        assert status.block_threshold == 900
        assert status.warn_threshold == 500
        assert status.blocked


def test_malformed_env_values_fall_back_to_defaults():
    env = {
        "QBO_MONTHLY_CALL_BUDGET": "not-a-number",
        "QBO_BUDGET_BLOCK_PCT": "95",  # out of 0-1 range → default
    }
    with patch.dict("os.environ", env):
        assert monthly_call_budget() == 500_000
        status = _budget_with_count(1).record_call(REALM_ID)
        assert status.block_threshold == 475_000


# --------------------------------------------------------------------------- #
# Fail-open: a broken meter must never block QBO sync
# --------------------------------------------------------------------------- #


def test_record_call_fails_open_on_repo_error():
    repo = MagicMock()
    repo.increment.side_effect = RuntimeError("db down")
    status = QboApiBudget(repo=repo).record_call(REALM_ID)
    assert status.meter_unavailable
    assert not status.blocked


def test_status_fails_open_on_repo_error():
    repo = MagicMock()
    repo.read_month_total.side_effect = RuntimeError("db down")
    status = QboApiBudget(repo=repo).status()
    assert status.meter_unavailable
    assert not status.blocked


def test_increment_returns_post_increment_count():
    """The sproc's OUTPUT-INTO + final-SELECT shape returns exactly one
    result set — increment() is a plain fetchone."""
    from integrations.intuit.qbo.base.budget import QboApiUsageRepository

    cursor = MagicMock()
    cursor.fetchone.return_value = (5,)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    with patch("shared.database.get_connection", return_value=cm), patch(
        "shared.database.call_procedure"
    ):
        assert QboApiUsageRepository().increment(REALM_ID, "2026-08") == 5


def test_record_call_or_raise_blocked_raises_with_status_payload():
    budget = _budget_with_count(475_000)
    with pytest.raises(QboBudgetExceededError) as exc_info:
        budget.record_call_or_raise(REALM_ID, method="GET", path="/v3/company/x/bill/1")
    assert exc_info.value.call_count == 475_000
    assert exc_info.value.month_key == current_month_key()
    assert exc_info.value.is_retryable is False


def test_record_call_or_raise_unblocked_returns_status():
    status = _budget_with_count(42).record_call_or_raise(
        REALM_ID, method="GET", path="/v3/company/x/bill/1"
    )
    assert status.call_count == 42
    assert not status.blocked


# --------------------------------------------------------------------------- #
# Calendar math
# --------------------------------------------------------------------------- #


def test_current_month_key_format():
    assert current_month_key(datetime(2026, 8, 7, tzinfo=timezone.utc)) == "2026-08"


def test_next_month_start_mid_year():
    assert next_month_start(datetime(2026, 8, 7, 15, 30, tzinfo=timezone.utc)) == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    )


def test_next_month_start_december_rollover():
    assert next_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)) == datetime(
        2027, 1, 1, tzinfo=timezone.utc
    )


def test_reset_at_for_month_uses_exhausted_month_not_wall_clock():
    assert reset_at_for_month("2026-08") == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert reset_at_for_month("2026-12") == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_reset_at_for_month_falls_back_to_wall_clock():
    fallback = next_month_start()
    assert reset_at_for_month(None) == fallback
    assert reset_at_for_month("garbage") == fallback


# --------------------------------------------------------------------------- #
# Shared client: refuse-before-send at the HTTP choke point
# --------------------------------------------------------------------------- #


def _make_client(budget):
    http = MagicMock()
    client = QboHttpClient(
        realm_id=REALM_ID,
        auth_service=MagicMock(),
        http_client=http,
        api_budget=budget,
    )
    return client, http


def test_send_http_blocked_raises_without_sending():
    budget = MagicMock()
    budget.record_call_or_raise.side_effect = _budget_exceeded_error()
    client, http = _make_client(budget)
    with pytest.raises(QboBudgetExceededError):
        client._send_http(
            "GET", "https://qbo/v3/company/x/bill/1", "tok", {}, None, None, _TIMEOUT_TIERS["A"]
        )
    http.request.assert_not_called()


def test_send_http_unblocked_meters_and_sends():
    budget = MagicMock()
    budget.record_call_or_raise.return_value = _make_status(call_count=42)
    client, http = _make_client(budget)
    client._send_http(
        "GET", "https://qbo/v3/company/x/bill/1", "tok", {}, None, None, _TIMEOUT_TIERS["A"]
    )
    budget.record_call_or_raise.assert_called_once_with(
        REALM_ID, method="GET", path="https://qbo/v3/company/x/bill/1"
    )
    http.request.assert_called_once()


# --------------------------------------------------------------------------- #
# Attachable client: routed through QboHttpClient (U-218e)
# --------------------------------------------------------------------------- #


def _make_attachable_client(http_mock, budget):
    from integrations.intuit.qbo.attachable.external.client import QboAttachableClient

    auth = MagicMock()
    auth.ensure_valid_token_classified.return_value = (MagicMock(access_token="tok"), None)
    shared = QboHttpClient(
        realm_id=REALM_ID,
        auth_service=auth,
        http_client=http_mock,
        api_budget=budget,
    )
    return QboAttachableClient(realm_id=REALM_ID, http_client=shared), shared


def test_attachable_get_blocked_raises_without_sending():
    budget = MagicMock()
    budget.record_call_or_raise.side_effect = _budget_exceeded_error()
    http = MagicMock()
    client, _ = _make_attachable_client(http, budget)
    with pytest.raises(QboBudgetExceededError):
        client.get_attachable("att-1")
    http.request.assert_not_called()


def test_attachable_get_unblocked_meters_once_and_sends():
    budget = MagicMock()
    budget.record_call_or_raise.return_value = _make_status(call_count=42)
    http = MagicMock()
    http.request.return_value = MagicMock(
        status_code=200,
        text='{"Attachable":{"Id":"1","SyncToken":"0"}}',
        json=lambda: {"Attachable": {"Id": "1", "SyncToken": "0"}},
    )
    client, _ = _make_attachable_client(http, budget)
    client.get_attachable("att-1")
    budget.record_call_or_raise.assert_called_once()
    http.request.assert_called_once()


def test_attachable_query_blocked_raises_without_sending():
    budget = MagicMock()
    budget.record_call_or_raise.side_effect = _budget_exceeded_error()
    http = MagicMock()
    client, _ = _make_attachable_client(http, budget)
    with pytest.raises(QboBudgetExceededError):
        client.query_attachables()
    http.request.assert_not_called()


def test_attachable_upload_blocked_raises_before_upload(monkeypatch):
    monkeypatch.delenv("ALLOW_QBO_WRITES", raising=False)
    budget = MagicMock()
    http = MagicMock()
    client, _ = _make_attachable_client(http, budget)
    with pytest.raises(QboWriteRefusedError):
        client.upload_attachable(
            file_content=b"pdf",
            filename="a.pdf",
            content_type="application/pdf",
            entity_type="Bill",
            entity_id="123",
        )
    http.request.assert_not_called()
    budget.record_call_or_raise.assert_not_called()


def test_attachable_download_records_zero_metered_calls():
    from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
    from integrations.intuit.qbo.attachable.external.schemas import QboAttachable

    budget = MagicMock()
    shared = QboHttpClient(
        realm_id=REALM_ID,
        auth_service=MagicMock(),
        http_client=MagicMock(),
        api_budget=budget,
    )
    client = QboAttachableClient(realm_id=REALM_ID, http_client=shared)
    attachable = QboAttachable(
        id="1",
        sync_token="0",
        temp_download_uri="https://download.example/file",
    )

    with patch("integrations.intuit.qbo.attachable.external.client.httpx.Client") as dl_cls:
        dl_cls.return_value.__enter__.return_value.get.return_value = MagicMock(
            status_code=200, content=b"file-bytes"
        )
        result = client.download_attachable(attachable)

    assert result == b"file-bytes"
    budget.record_call_or_raise.assert_not_called()


# --------------------------------------------------------------------------- #
# Outbox worker: park until reset, never dead-letter on budget
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


def test_budget_error_parks_row_until_reset_of_exhausted_month():
    """The park date derives from the error's month_key, not handler wall
    clock — a row raised in month M but handled after the UTC rollover must
    park until the 1st of M+1, not M+2."""
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._handle_qbo_error(_make_outbox_row(), _budget_exceeded_error())
    repo.mark_dead_letter.assert_not_called()
    repo.mark_failed.assert_called_once()
    parked_until = repo.mark_failed.call_args.kwargs["next_retry_at"]
    assert parked_until == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_budget_error_never_dead_letters_even_at_max_attempts():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._handle_qbo_error(_make_outbox_row(attempts=MAX_ATTEMPTS + 3), _budget_exceeded_error())
    repo.mark_dead_letter.assert_not_called()
    repo.mark_failed.assert_called_once()


def test_drain_once_skips_claim_when_blocked():
    repo = MagicMock()
    budget = MagicMock()
    budget.status.return_value = _make_status(blocked=True, call_count=475_001)
    worker = QboOutboxWorker(repo=repo, api_budget=budget)
    assert worker.drain_once() is False
    repo.claim_next_pending.assert_not_called()


def _unblocked_budget_mock():
    budget = MagicMock()
    budget.status.return_value = _make_status(blocked=False)
    return budget


def test_drain_once_skips_claim_when_writes_disabled(caplog):
    """U-218b pre-claim guard: rows stay pending; no attempt burned."""
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=_unblocked_budget_mock())

    # The drain lock must be patched here as well as in the writes-allowed
    # sibling below: stranded-row reclaim now runs BEFORE the write gate (a
    # deploy restart during a writes-off window would otherwise strand rows
    # nothing releases), so this path acquires sp_getapplock and is no longer
    # pure-logic. Without the patch this test needs a live DB.
    with patch(
        "integrations.intuit.qbo.outbox.business.worker.writes_allowed",
        return_value=False,
    ), patch(
        "integrations.intuit.qbo.outbox.business.worker.qbo_app_lock"
    ) as lock_mock:
        lock_mock.return_value.__enter__.return_value = True
        with caplog.at_level("ERROR"):
            assert worker.drain_once() is False

    repo.claim_next_pending.assert_not_called()
    assert any(
        "qbo.outbox.drain.skipped_writes_disabled" in record.message
        or getattr(record, "event_name", "") == "qbo.outbox.drain.skipped_writes_disabled"
        for record in caplog.records
    )


def test_drain_once_claims_when_writes_allowed():
    """Pins polarity of the U-218b pre-claim guard (must fail if inverted)."""
    repo = MagicMock()
    repo.claim_next_pending.return_value = None
    worker = QboOutboxWorker(repo=repo, api_budget=_unblocked_budget_mock())

    with patch(
        "integrations.intuit.qbo.outbox.business.worker.writes_allowed",
        return_value=True,
    ), patch(
        "integrations.intuit.qbo.outbox.business.worker.qbo_app_lock"
    ) as lock_mock:
        lock_mock.return_value.__enter__.return_value = True
        assert worker.drain_once() is False

    repo.claim_next_pending.assert_called_once()


def test_drain_all_stops_on_blocked_budget():
    repo = MagicMock()
    budget = MagicMock()
    budget.status.return_value = _make_status(blocked=True, call_count=475_001)
    worker = QboOutboxWorker(repo=repo, api_budget=budget)
    assert worker.drain_all(max_rows=50) == 0
    repo.claim_next_pending.assert_not_called()
