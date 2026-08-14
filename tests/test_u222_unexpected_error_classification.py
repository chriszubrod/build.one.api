"""U-222 — unexpected outbox handler errors: retry vs dead-letter classification."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.errors import (
    QboBudgetExceededError,
    QboValidationError,
)
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.test_u218b_bill_push_surface import _make_outbox_row, REALM_ID

TRANSIENT_SQL_MESSAGE = "[08S01] Communication link failure"
QUERY_TIMEOUT_MESSAGE = "query timeout expired"


def _worker_with_failing_handler(repo, exc):
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    worker._dispatch_table["sync_bill_to_qbo"] = MagicMock(side_effect=exc)
    return worker


def test_permanent_value_error_dead_letters_on_first_attempt():
    repo = MagicMock()
    error = ValueError("BillLineItem 12345 has no sub_cost_code_id")
    worker = _worker_with_failing_handler(repo, error)

    worker._process_inner(_make_outbox_row(attempts=0))

    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_unknown_non_value_error_retries_rather_than_dead_letters():
    repo = MagicMock()
    error = RuntimeError("something nobody has seen before")
    worker = _worker_with_failing_handler(repo, error)

    worker._process_inner(_make_outbox_row(attempts=0))

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


@patch("integrations.intuit.qbo.outbox.business.worker.compute_backoff_seconds")
def test_transient_sql_error_schedules_retry_not_dead_letter(mock_backoff):
    mock_backoff.return_value = 5.0
    repo = MagicMock()
    error = Exception(TRANSIENT_SQL_MESSAGE)
    worker = _worker_with_failing_handler(repo, error)
    before = datetime.now(timezone.utc)

    worker._process_inner(_make_outbox_row(attempts=0))

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    mock_backoff.assert_called_once()
    assert mock_backoff.call_args.kwargs["attempt"] == 1
    kwargs = repo.mark_failed.call_args.kwargs
    assert kwargs["last_error"].startswith("Unexpected Exception:")
    assert kwargs["next_retry_at"] > before


@patch("integrations.intuit.qbo.outbox.business.worker.compute_backoff_seconds")
def test_fourth_attempt_retries_with_backoff_attempt_four(mock_backoff):
    mock_backoff.return_value = 5.0
    repo = MagicMock()
    worker = _worker_with_failing_handler(repo, Exception(TRANSIENT_SQL_MESSAGE))

    worker._process_inner(_make_outbox_row(attempts=3))

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    assert mock_backoff.call_args.kwargs["attempt"] == 4


def test_pyodbc_query_timeout_schedules_retry():
    repo = MagicMock()
    error = Exception(f"ODBC Driver: {QUERY_TIMEOUT_MESSAGE}")
    worker = _worker_with_failing_handler(repo, error)

    worker._process_inner(_make_outbox_row(attempts=0))

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


def test_chained_cause_transient_error_still_retries():
    repo = MagicMock()
    db_err = Exception(TRANSIENT_SQL_MESSAGE)
    wrapped = ValueError("bill push failed while writing local mapping")
    wrapped.__cause__ = db_err
    worker = _worker_with_failing_handler(repo, wrapped)

    worker._process_inner(_make_outbox_row(attempts=0))

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


def test_transient_error_dead_letters_once_max_attempts_exhausted():
    repo = MagicMock()
    error = Exception(TRANSIENT_SQL_MESSAGE)
    worker = _worker_with_failing_handler(repo, error)

    worker._process_inner(_make_outbox_row(attempts=4))

    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()
    kwargs = repo.mark_dead_letter.call_args.kwargs
    assert kwargs["last_error"].startswith("Retries exhausted after 5:")


def test_qbo_validation_error_does_not_route_through_unexpected_error_handler():
    repo = MagicMock()
    error = QboValidationError("bad payload")
    worker = _worker_with_failing_handler(repo, error)

    with patch.object(worker, "_handle_unexpected_error", MagicMock()) as unexpected_mock:
        worker._process_inner(_make_outbox_row(attempts=0))

    unexpected_mock.assert_not_called()
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_qbo_budget_exceeded_does_not_route_through_unexpected_error_handler():
    repo = MagicMock()
    error = QboBudgetExceededError(
        "budget exhausted",
        month_key="2026-08",
        call_count=1,
        budget=1,
    )
    worker = _worker_with_failing_handler(repo, error)

    with patch.object(worker, "_handle_unexpected_error", MagicMock()) as unexpected_mock:
        worker._process_inner(_make_outbox_row(attempts=0))

    unexpected_mock.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    kwargs = repo.mark_failed.call_args.kwargs
    assert kwargs["last_error"].startswith("Parked: monthly QBO API budget exhausted")
