"""U-727: reclaim stale in_progress Box outbox rows (mock repo + box_app_lock)."""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from integrations.box.base.errors import BoxServerError
from integrations.box.outbox.business.model import BoxOutbox
from integrations.box.outbox.business.service import KIND_UPDATE_EXCEL, KIND_UPLOAD_FILE
from integrations.box.outbox.business.worker import (
    RECLAIM_STALE_AFTER_SECONDS,
    BoxOutboxWorker,
)

FIXED_NOW = datetime(2026, 10, 26, 14, 30, 0, tzinfo=timezone.utc)


@contextmanager
def _applock(acquired=True):
    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield acquired

    with patch(
        "integrations.box.outbox.business.worker.box_app_lock",
        fake_lock,
    ):
        yield


def _stale_row(
    *,
    kind=KIND_UPDATE_EXCEL,
    attempts=1,
    row_id=1,
    started_at="2026-10-26 10:00:00",
) -> BoxOutbox:
    return BoxOutbox(
        id=row_id,
        public_id=str(uuid.uuid4()),
        row_version="cmV2",
        kind=kind,
        entity_type="bill",
        entity_public_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        status="in_progress",
        attempts=attempts,
        started_at=started_at,
    )


def test_reclaim_lock_busy_returns_zero_without_read():
    repo = MagicMock()
    worker = BoxOutboxWorker(repo=repo)
    with _applock(acquired=False):
        assert worker.reclaim_stale_claims() == 0
    repo.read_stale_in_progress.assert_not_called()


@pytest.mark.parametrize("kind", [KIND_UPDATE_EXCEL, KIND_UPLOAD_FILE])
@patch("integrations.box.outbox.business.worker.datetime")
def test_reclaim_redrivable_kind_marks_failed(mock_dt, kind):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(kind=kind, attempts=1)
    repo.read_stale_in_progress.return_value = [row]
    worker = BoxOutboxWorker(repo=repo)

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
            f"U-727 reclaim: abandoned in_progress claim (started {row.started_at}); retrying"
        ),
    )
    repo.mark_dead_letter.assert_not_called()


@patch("integrations.box.outbox.business.worker.datetime")
def test_reclaim_at_max_attempts_minus_one_still_retries(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(attempts=BoxOutboxWorker.MAX_ATTEMPTS - 1)
    repo.read_stale_in_progress.return_value = [row]
    worker = BoxOutboxWorker(repo=repo)

    with _applock(acquired=True), patch.object(worker, "_escalate_dead_letter"):
        assert worker.reclaim_stale_claims() == 1

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


@patch("integrations.box.outbox.business.worker.datetime")
def test_reclaim_at_max_attempts_dead_letters(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row = _stale_row(attempts=BoxOutboxWorker.MAX_ATTEMPTS)
    repo.read_stale_in_progress.return_value = [row]
    worker = BoxOutboxWorker(repo=repo)

    with _applock(acquired=True), patch.object(worker, "_escalate_dead_letter"):
        assert worker.reclaim_stale_claims() == 1

    repo.mark_dead_letter.assert_called_once()
    assert (
        f"retries exhausted after {BoxOutboxWorker.MAX_ATTEMPTS}"
        in repo.mark_dead_letter.call_args.kwargs["last_error"]
    )
    repo.mark_failed.assert_not_called()


@patch("integrations.box.outbox.business.worker.datetime")
def test_reclaim_continues_when_one_row_mark_failed_raises(mock_dt):
    mock_dt.now.return_value = FIXED_NOW
    mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
    repo = MagicMock()
    row1 = _stale_row(kind=KIND_UPDATE_EXCEL, row_id=1)
    row2 = _stale_row(kind=KIND_UPLOAD_FILE, row_id=2)
    repo.read_stale_in_progress.return_value = [row1, row2]
    repo.mark_failed.side_effect = [RuntimeError("rowversion conflict"), None]
    worker = BoxOutboxWorker(repo=repo)

    with _applock(acquired=True):
        assert worker.reclaim_stale_claims() == 1

    assert repo.mark_failed.call_count == 2


def _auth_configured():
    return patch(
        "integrations.box.outbox.business.worker.BoxAuthService",
        return_value=MagicMock(is_configured=MagicMock(return_value=True)),
    )


def test_drain_all_calls_reclaim_once_before_token_mint():
    worker = BoxOutboxWorker(repo=MagicMock())
    worker.drain_once = MagicMock(
        return_value={"claimed": 0, "done": 0, "failed": 0, "dead_lettered": 0}
    )
    call_order: list[str] = []

    with _auth_configured() as AuthCls:
        auth = AuthCls.return_value
        auth.ensure_valid_token = MagicMock(
            side_effect=lambda: call_order.append("token")
        )
        worker.reclaim_stale_claims = MagicMock(
            side_effect=lambda: call_order.append("reclaim") or 0
        )
        worker.drain_all(max_rows=5)

    assert call_order == ["reclaim", "token"]
    worker.drain_once.assert_called()


def test_drain_all_continues_when_reclaim_raises():
    worker = BoxOutboxWorker(repo=MagicMock())
    worker.reclaim_stale_claims = MagicMock(side_effect=RuntimeError("sproc missing"))
    worker.drain_once = MagicMock(
        return_value={"claimed": 1, "done": 1, "failed": 0, "dead_lettered": 0}
    )

    with _auth_configured() as AuthCls:
        AuthCls.return_value.ensure_valid_token = MagicMock()
        totals = worker.drain_all(max_rows=2)

    assert totals["claimed"] == 2
    assert worker.drain_once.call_count == 2


def test_paused_drain_all_does_not_reclaim():
    worker = BoxOutboxWorker(repo=MagicMock())
    worker.reclaim_stale_claims = MagicMock()

    with patch.dict(os.environ, {"PAUSE_BOX_DRAIN": "true"}):
        result = worker.drain_all()

    assert result.get("paused") is True
    worker.reclaim_stale_claims.assert_not_called()


def test_unconfigured_box_drain_all_does_not_reclaim():
    worker = BoxOutboxWorker(repo=MagicMock())
    worker.reclaim_stale_claims = MagicMock()

    with patch(
        "integrations.box.outbox.business.worker.BoxAuthService",
        return_value=MagicMock(is_configured=MagicMock(return_value=False)),
    ):
        result = worker.drain_all()

    assert result.get("skipped") == "box_not_configured"
    worker.reclaim_stale_claims.assert_not_called()


def test_handle_box_error_retry_boundary_unchanged():
    """Same exhaustion rule as reclaim: stored attempts already count this claim."""
    repo = MagicMock()
    worker = BoxOutboxWorker(repo=repo)
    retryable = BoxServerError("transient", request_path="/x")

    row_retry = BoxOutbox(
        id=1,
        public_id=str(uuid.uuid4()),
        row_version="cmV2",
        kind=KIND_UPLOAD_FILE,
        entity_type="bill",
        entity_public_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        attempts=BoxOutboxWorker.MAX_ATTEMPTS - 1,
    )
    assert worker._handle_box_error(row_retry, retryable) == "failed"
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()

    repo.reset_mock()
    row_exhausted = BoxOutbox(
        id=2,
        public_id=str(uuid.uuid4()),
        row_version="cmV2",
        kind=KIND_UPLOAD_FILE,
        entity_type="bill",
        entity_public_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        attempts=BoxOutboxWorker.MAX_ATTEMPTS,
    )
    with patch.object(worker, "_escalate_dead_letter"):
        assert worker._handle_box_error(row_exhausted, retryable) == "dead_lettered"
    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()
