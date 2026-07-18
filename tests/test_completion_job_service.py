from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.completion_job.business.model import CompletionJob
from entities.completion_job.business.service import CompletionJobService
from entities.completion_job.persistence.repo import CompletionJobRepository
from shared.authz import current_is_system_admin, set_authz_context


def _job(*, entity_type: str = "Bill", entity_public_id: str = "entity-uuid", public_id: str = "job-uuid"):
    return CompletionJob(
        id=1,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        status="processing",
        attempts=2,
        max_attempts=5,
        claimed_at="2026-07-17T00:00:00.000",
        last_error=None,
        public_id=public_id,
    )


def test_run_job_bill_marks_success_on_normal_return():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="Bill")

    with patch("entities.bill.api.router._run_complete_bill") as run_complete:
        service.run_job(job)

    run_complete.assert_called_once_with(job.entity_public_id, job.public_id, force=True)
    repo.mark_success.assert_not_called()
    repo.mark_failure.assert_not_called()


def test_run_job_bill_reclaim_redrives_already_finalized_entity():
    """Reclaim must invoke complete_bill even when is_draft=False (force=True bypass)."""
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="Bill")
    bill = SimpleNamespace(is_draft=False)
    result = {"status_code": 200, "bill_finalized": True}

    with patch("entities.bill.business.service.BillService.read_by_public_id", return_value=bill), patch(
        "entities.bill.business.service.BillService.complete_bill", return_value=result
    ) as complete_bill, patch("entities.bill.persistence.repo.BillRepository.set_completion_result"), patch.object(
        CompletionJobService, "mark_success"
    ) as mark_success:
        service.run_job(job)

    complete_bill.assert_called_once_with(public_id=job.entity_public_id)
    mark_success.assert_called_once_with(job.public_id)


def test_run_job_expense_reclaim_redrives_already_finalized_entity():
    """Reclaim must invoke complete_expense even when is_draft=False (force=True bypass)."""
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="Expense")
    expense = SimpleNamespace(is_draft=False)
    result = {"status_code": 200, "expense_finalized": True}

    with patch("entities.expense.business.service.ExpenseService.read_by_public_id", return_value=expense), patch(
        "entities.expense.business.service.ExpenseService.complete_expense", return_value=result
    ) as complete_expense, patch.object(
        CompletionJobService, "mark_success"
    ) as mark_success:
        service.run_job(job)

    complete_expense.assert_called_once_with(public_id=job.entity_public_id)
    mark_success.assert_called_once_with(job.public_id)


def test_run_job_billcredit_marks_success_on_normal_return_including_207():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="BillCredit")

    with patch(
        "entities.bill_credit.business.complete_service.BillCreditCompleteService.complete_bill_credit",
        return_value={"status_code": 207, "message": "partial"},
    ):
        service.run_job(job)

    repo.mark_success.assert_called_once_with(public_id=job.public_id)
    repo.mark_failure.assert_not_called()


def test_run_job_billcredit_marks_failure_on_raise():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="BillCredit")

    with patch(
        "entities.bill_credit.business.complete_service.BillCreditCompleteService.complete_bill_credit",
        side_effect=ValueError("orchestration blew up"),
    ):
        service.run_job(job)

    repo.mark_failure.assert_called_once_with(
        public_id=job.public_id,
        last_error="orchestration blew up",
        max_attempts=5,
    )


def test_run_job_never_re_raises():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="Invoice")

    with patch(
        "entities.invoice.business.service.InvoiceService.complete_invoice",
        side_effect=RuntimeError("kaboom"),
    ):
        service.run_job(job)


def test_run_job_enters_system_admin_authz_context():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)
    job = _job(entity_type="Invoice")
    seen = {}

    def _capture_complete(*_args, **_kwargs):
        seen["isa"] = current_is_system_admin.get()

    with patch(
        "entities.invoice.business.service.InvoiceService.complete_invoice",
        side_effect=_capture_complete,
    ):
        set_authz_context(user_id=99, company_id=1, is_system_admin=False)
        try:
            service.run_job(job)
        finally:
            set_authz_context(user_id=None, company_id=None, is_system_admin=False)

    assert seen["isa"] is True


def test_mark_success_routes_to_guarded_sproc():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)

    service.mark_success("job-uuid")

    repo.mark_success.assert_called_once_with(public_id="job-uuid")


def test_mark_failure_routes_to_guarded_sproc():
    repo = MagicMock()
    service = CompletionJobService(repo=repo)

    service.mark_failure("job-uuid", "stale reclaim lost race")

    repo.mark_failure.assert_called_once_with(
        public_id="job-uuid",
        last_error="stale reclaim lost race",
        max_attempts=5,
    )


def test_run_complete_bill_marks_success_on_207_result():
    from entities.bill.api.router import _run_complete_bill

    bill = SimpleNamespace(is_draft=True)
    result = {"status_code": 207, "bill_finalized": True}

    with patch("entities.bill.business.service.BillService.read_by_public_id", return_value=bill), patch(
        "entities.bill.business.service.BillService.complete_bill", return_value=result
    ), patch("entities.bill.persistence.repo.BillRepository.set_completion_result"), patch(
        "entities.completion_job.business.service.CompletionJobService.mark_success"
    ) as mark_success, patch(
        "entities.completion_job.business.service.CompletionJobService.mark_failure"
    ) as mark_failure:
        _run_complete_bill("bill-uuid", "job-uuid")

    mark_success.assert_called_once_with("job-uuid")
    mark_failure.assert_not_called()


def test_run_complete_bill_skips_non_draft_without_force():
    from entities.bill.api.router import _run_complete_bill

    bill = SimpleNamespace(is_draft=False)

    with patch("entities.bill.business.service.BillService.read_by_public_id", return_value=bill), patch(
        "entities.bill.business.service.BillService.complete_bill"
    ) as complete_bill, patch(
        "entities.completion_job.business.service.CompletionJobService.mark_success"
    ) as mark_success:
        _run_complete_bill("bill-uuid", "job-uuid")

    complete_bill.assert_not_called()
    mark_success.assert_called_once_with("job-uuid")


def test_run_complete_bill_redrives_non_draft_with_force():
    from entities.bill.api.router import _run_complete_bill

    bill = SimpleNamespace(is_draft=False)
    result = {"status_code": 200, "bill_finalized": True}

    with patch("entities.bill.business.service.BillService.read_by_public_id", return_value=bill), patch(
        "entities.bill.business.service.BillService.complete_bill", return_value=result
    ) as complete_bill, patch("entities.bill.persistence.repo.BillRepository.set_completion_result"), patch(
        "entities.completion_job.business.service.CompletionJobService.mark_success"
    ) as mark_success:
        _run_complete_bill("bill-uuid", "job-uuid", force=True)

    complete_bill.assert_called_once_with(public_id="bill-uuid")
    mark_success.assert_called_once_with("job-uuid")


def test_run_complete_bill_marks_failure_on_exception():
    from entities.bill.api.router import _run_complete_bill

    bill = SimpleNamespace(is_draft=True)

    with patch("entities.bill.business.service.BillService.read_by_public_id", return_value=bill), patch(
        "entities.bill.business.service.BillService.complete_bill", side_effect=RuntimeError("fail")
    ), patch("entities.completion_job.business.service.CompletionJobService.mark_success") as mark_success, patch(
        "entities.completion_job.business.service.CompletionJobService.mark_failure"
    ) as mark_failure:
        _run_complete_bill("bill-uuid", "job-uuid")

    mark_failure.assert_called_once_with("job-uuid", "fail")
    mark_success.assert_not_called()


def test_repo_create_returns_sentinel_when_sproc_returns_no_row(monkeypatch):
    class _FakeCursor:
        def fetchone(self):
            return None

    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: _FakeCursor())

    def _noop_call_procedure(*, cursor, name, params):
        pass

    monkeypatch.setattr("entities.completion_job.persistence.repo.get_connection", _fake_conn)
    monkeypatch.setattr("entities.completion_job.persistence.repo.call_procedure", _noop_call_procedure)

    job = CompletionJobRepository().create(
        entity_type="Bill",
        entity_public_id="12345678-1234-5678-1234-567812345678",
        company_id=1,
    )

    assert job.public_id is None
    assert job.was_created is False
    assert job.entity_type == "Bill"
    assert job.entity_public_id == "12345678-1234-5678-1234-567812345678"
