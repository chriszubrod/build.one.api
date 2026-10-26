"""U-703: reclaim stale in_progress MS outbox rows (mock repo + ms_app_lock)."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_APPEND_EXCEL_ROW,
    KIND_EXPENSE_PULL_FANOUT,
    KIND_INSERT_EXCEL_ROW,
    KIND_SEND_MAIL,
    KIND_UPDATE_DRAFT,
    KIND_UPLOAD_SHAREPOINT_FILE,
)
from integrations.ms.outbox.business.worker import (
    MAX_ATTEMPTS,
    RECLAIM_STALE_AFTER_SECONDS,
    MsOutboxWorker,
)

FIXED_NOW = datetime(2026, 10, 26, 14, 30, 0, tzinfo=timezone.utc)


@contextmanager
def _applock(acquired=True):
    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield acquired

    with patch(
        "integrations.ms.outbox.business.worker.ms_app_lock",
        fake_lock,
    ):
        yield


def _stale_row(
    *,
    kind=KIND_APPEND_EXCEL_ROW,
    attempts=0,
    row_id=1,
    started_at="2026-10-26 10:00:00",
) -> MsOutbox:
    return MsOutbox(
        id=row_id,
        public_id=str(uuid.uuid4()),
        row_version="cmV2",
        kind=kind,
        entity_type="Bill",
        entity_public_id=str(uuid.uuid4()),
        tenant_id="tenant-1",
        request_id=str(uuid.uuid4()),
        status="in_progress",
        attempts=attempts,
        started_at=started_at,
    )


def test_reclaim_lock_busy_returns_zero_without_read():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    with _applock(acquired=False):
        assert worker.reclaim_stale_claims() == 0
    repo.read_stale_in_progress.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        KIND_APPEND_EXCEL_ROW,
        KIND_INSERT_EXCEL_ROW,
        KIND_UPLOAD_SHAREPOINT_FILE,
        KIND_UPDATE_DRAFT,
        KIND_EXPENSE_PULL_FANOUT,
    ],
)
@patch("integrations.ms.outbox.business.worker.datetime")
def test_reclaim_redrivable_kind_marks_failed(mock_dt, kind):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(kind=kind, attempts=0)
    repo.read_stale_in_progress.return_value = [row]
    worker = MsOutboxWorker(repo=repo)

    with _applock(acquired=True):
        assert worker.reclaim_stale_claims() == 1

    stale_before = repo.read_stale_in_progress.call_args.args[0]
    delta = abs((FIXED_NOW - stale_before).total_seconds() - RECLAIM_STALE_AFTER_SECONDS)
    assert delta < 2

    repo.mark_failed.assert_called_once_with(
        id=row.id,
        row_version=row.row_version,
        next_retry_at=FIXED_NOW,
        last_error=(
            f"U-703 reclaim: abandoned in_progress claim (started {row.started_at}); retrying"
        ),
    )
    repo.mark_dead_letter.assert_not_called()


@patch("integrations.ms.outbox.business.worker.datetime")
def test_reclaim_send_mail_dead_letters_without_mark_failed(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(kind=KIND_SEND_MAIL)
    repo.read_stale_in_progress.return_value = [row]
    worker = MsOutboxWorker(repo=repo)

    with _applock(acquired=True), patch.object(
        worker, "_escalate_dead_letter"
    ) as escalate:
        assert worker.reclaim_stale_claims() == 1

    repo.mark_dead_letter.assert_called_once()
    assert "send_mail" in repo.mark_dead_letter.call_args.kwargs["last_error"]
    repo.mark_failed.assert_not_called()
    escalate.assert_called_once()


@patch("integrations.ms.outbox.business.worker.datetime")
def test_reclaim_at_max_attempts_minus_two_still_retries(mock_dt):
    # Boundary: same rule as _handle_ms_error - one more attempt is still allowed.
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(kind=KIND_APPEND_EXCEL_ROW, attempts=MAX_ATTEMPTS - 2)
    repo.read_stale_in_progress.return_value = [row]
    worker = MsOutboxWorker(repo=repo)

    with _applock(acquired=True), patch.object(worker, "_escalate_dead_letter"):
        assert worker.reclaim_stale_claims() == 1

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


@patch("integrations.ms.outbox.business.worker.datetime")
def test_reclaim_at_max_attempts_minus_one_dead_letters(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(kind=KIND_APPEND_EXCEL_ROW, attempts=MAX_ATTEMPTS - 1)
    repo.read_stale_in_progress.return_value = [row]
    worker = MsOutboxWorker(repo=repo)

    with _applock(acquired=True), patch.object(worker, "_escalate_dead_letter"):
        assert worker.reclaim_stale_claims() == 1

    repo.mark_dead_letter.assert_called_once()
    assert f"retries exhausted after {MAX_ATTEMPTS}" in repo.mark_dead_letter.call_args.kwargs[
        "last_error"
    ]
    repo.mark_failed.assert_not_called()


@patch("integrations.ms.outbox.business.worker.datetime")
def test_reclaim_continues_when_one_row_mark_failed_raises(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row1 = _stale_row(kind=KIND_APPEND_EXCEL_ROW, row_id=1)
    row2 = _stale_row(kind=KIND_INSERT_EXCEL_ROW, row_id=2)
    repo.read_stale_in_progress.return_value = [row1, row2]
    repo.mark_failed.side_effect = [RuntimeError("rowversion conflict"), None]
    worker = MsOutboxWorker(repo=repo)

    with _applock(acquired=True):
        assert worker.reclaim_stale_claims() == 1

    assert repo.mark_failed.call_count == 2


def test_drain_all_calls_reclaim_once_before_draining():
    worker = MsOutboxWorker(repo=MagicMock())
    worker.reclaim_stale_claims = MagicMock(return_value=0)
    worker.drain_once = MagicMock(return_value=False)

    worker.drain_all(max_rows=5)

    worker.reclaim_stale_claims.assert_called_once()
    worker.drain_once.assert_called()


def test_drain_all_continues_when_reclaim_raises():
    worker = MsOutboxWorker(repo=MagicMock())
    worker.reclaim_stale_claims = MagicMock(side_effect=RuntimeError("sproc missing"))
    worker.drain_once = MagicMock(return_value=True)

    count = worker.drain_all(max_rows=2)

    assert count == 2
    assert worker.drain_once.call_count == 2
