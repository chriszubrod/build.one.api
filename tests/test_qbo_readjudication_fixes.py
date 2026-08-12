"""Pins for the re-adjudication fixes (2026-08-12).

Covers the behavior changes made in response to the U-218b/U-218e
re-adjudication, so a later edit cannot silently undo them.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker


def _worker():
    repo = MagicMock()
    budget = MagicMock()
    budget.status.return_value = MagicMock(blocked=False)
    return QboOutboxWorker(repo=repo, api_budget=budget), repo


@contextmanager
def _granted_lock(*_args, **_kwargs):
    """Stand in for sp_getapplock — the real one needs a DB connection, which
    this pure-logic harness does not have."""
    yield True


@patch("integrations.intuit.qbo.outbox.business.worker.qbo_app_lock", _granted_lock)
@patch("integrations.intuit.qbo.outbox.business.worker.writes_allowed", return_value=False)
def test_reclaim_still_runs_when_writes_are_disabled(_writes_off):
    """Stranding is a DB-only condition and its repair issues no QBO call.
    Leaving reclaim behind the write gate meant a deploy restart during a
    writes-off window stranded rows nothing would ever release."""
    worker, repo = _worker()
    with patch.object(worker, "_reclaim_stranded_rows") as reclaim:
        assert worker.drain_once() is False
        reclaim.assert_called_once()
    # ...but no row is claimed while writes are off.
    repo.claim_next_pending.assert_not_called()


@patch("integrations.intuit.qbo.outbox.business.worker.qbo_app_lock", _granted_lock)
@patch("integrations.intuit.qbo.outbox.business.worker.writes_allowed", return_value=True)
def test_writes_enabled_still_reclaims_then_claims(_writes_on):
    worker, repo = _worker()
    repo.claim_next_pending.return_value = None
    with patch.object(worker, "_reclaim_stranded_rows") as reclaim:
        worker.drain_once()
        reclaim.assert_called_once()
    repo.claim_next_pending.assert_called_once()


def test_reconcile_project_declares_system_intent_under_main():
    """--write calls sync_from_qbo_bill, whose CREATE path reads back the Bill
    it just created; without system intent that scoped read returns None and
    the connector strands an orphan header-only Bill."""
    src = open("scripts/reconcile_project.py").read()
    assert "from scripts.sync_helper import assert_cli_system_admin" in src
    main_guard = src.split('if __name__ == "__main__":')[-1]
    assert "assert_cli_system_admin()" in main_guard
    # and it must precede main()
    assert main_guard.index("assert_cli_system_admin()") < main_guard.index("main()")


def test_reconcile_project_does_not_route_operators_to_the_unsafe_repush():
    """enqueue() mints a fresh RequestId, so pointing operators at
    POST /sync/bill-to-qbo for a dead-lettered bill risks a duplicate QBO bill.
    The replay script preserves the original RequestId."""
    src = open("scripts/reconcile_project.py").read()
    assert "retry_qbo_outbox_dead_letters.py" in src
    assert "Do NOT use POST /sync/bill-to-qbo for it" in src
