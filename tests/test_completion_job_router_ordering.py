from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.bill.api.router import complete_bill_router
from entities.bill_credit.api.router import complete_bill_credit_router
from entities.expense.api.router import complete_expense_router
from entities.invoice.api.router import complete_invoice_router


def _patch_bill_deps(monkeypatch):
    bill = SimpleNamespace(is_draft=True)
    monkeypatch.setattr(
        "entities.bill.business.service.BillService.read_by_public_id",
        lambda self, public_id: bill,
    )
    return bill


def test_complete_bill_enqueues_job_before_background_task(monkeypatch):
    _patch_bill_deps(monkeypatch)
    call_order = []
    job = SimpleNamespace(public_id="job-public-id", was_created=True)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            call_order.append(("enqueue", entity_type, entity_public_id))
            return job

    background_tasks = MagicMock()
    background_tasks.add_task.side_effect = lambda fn, *args: call_order.append(("add_task", fn.__name__, args))

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        complete_bill_router(
            public_id="bill-public-id",
            background_tasks=background_tasks,
            current_user={"tenant_id": 1},
        )

    assert call_order[0] == ("enqueue", "Bill", "bill-public-id")
    assert call_order[1][0] == "add_task"
    assert call_order[1][1] == "_run_complete_bill"
    assert call_order[1][2] == ("bill-public-id", "job-public-id")


def test_complete_bill_skips_background_task_when_job_coalesced(monkeypatch):
    _patch_bill_deps(monkeypatch)
    job = SimpleNamespace(public_id="job-public-id", was_created=False)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            return job

    background_tasks = MagicMock()

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        response = complete_bill_router(
            public_id="bill-public-id",
            background_tasks=background_tasks,
            current_user={"tenant_id": 1},
        )

    background_tasks.add_task.assert_not_called()
    assert response.status_code == 202


def test_complete_expense_enqueues_job_before_background_task(monkeypatch):
    expense = SimpleNamespace(is_draft=True)
    monkeypatch.setattr(
        "entities.expense.business.service.ExpenseService.read_by_public_id",
        lambda self, public_id: expense,
    )
    call_order = []
    job = SimpleNamespace(public_id="job-public-id", was_created=True)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            call_order.append(("enqueue", entity_type, entity_public_id))
            return job

    background_tasks = MagicMock()
    background_tasks.add_task.side_effect = lambda fn, *args: call_order.append(("add_task", fn.__name__, args))

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        complete_expense_router(
            public_id="expense-public-id",
            background_tasks=background_tasks,
            current_user={"tenant_id": 1},
        )

    assert call_order[0] == ("enqueue", "Expense", "expense-public-id")
    assert call_order[1][0] == "add_task"
    assert call_order[1][1] == "_run_complete_expense"
    assert call_order[1][2] == ("expense-public-id", "job-public-id")


def test_complete_expense_skips_background_task_when_job_coalesced(monkeypatch):
    expense = SimpleNamespace(is_draft=True)
    monkeypatch.setattr(
        "entities.expense.business.service.ExpenseService.read_by_public_id",
        lambda self, public_id: expense,
    )
    job = SimpleNamespace(public_id="job-public-id", was_created=False)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            return job

    background_tasks = MagicMock()

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        response = complete_expense_router(
            public_id="expense-public-id",
            background_tasks=background_tasks,
            current_user={"tenant_id": 1},
        )

    background_tasks.add_task.assert_not_called()
    assert response.status_code == 202


def test_complete_invoice_skips_job_marks_when_enqueue_returns_sentinel(monkeypatch):
    invoice = SimpleNamespace(is_draft=True)
    monkeypatch.setattr(
        "entities.invoice.business.service.InvoiceService.read_by_public_id",
        lambda self, public_id: invoice,
    )
    complete_result = {"status_code": 200, "invoice_finalized": True}
    monkeypatch.setattr(
        "entities.invoice.business.service.InvoiceService.complete_invoice",
        lambda self, public_id: complete_result,
    )

    sentinel = SimpleNamespace(public_id=None, was_created=False)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            return sentinel

        def mark_success(self, public_id):
            raise AssertionError("mark_success must not be called without a job row")

        def mark_failure(self, public_id, last_error):
            raise AssertionError("mark_failure must not be called without a job row")

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        response = complete_invoice_router(
            public_id="invoice-public-id",
            current_user={"tenant_id": 1},
        )

    assert response["data"] == complete_result


def test_complete_bill_credit_skips_job_marks_when_enqueue_returns_sentinel(monkeypatch):
    bill_credit = SimpleNamespace(is_draft=True)
    monkeypatch.setattr(
        "entities.bill_credit.business.service.BillCreditService.read_by_public_id",
        lambda self, public_id: bill_credit,
    )
    complete_result = {"status_code": 200, "message": "ok"}
    monkeypatch.setattr(
        "entities.bill_credit.business.complete_service.BillCreditCompleteService.complete_bill_credit",
        lambda self, public_id: complete_result,
    )

    sentinel = SimpleNamespace(public_id=None, was_created=False)

    class _FakeJobService:
        def enqueue(self, entity_type, entity_public_id):
            return sentinel

        def mark_success(self, public_id):
            raise AssertionError("mark_success must not be called without a job row")

        def mark_failure(self, public_id, last_error):
            raise AssertionError("mark_failure must not be called without a job row")

    with patch("entities.completion_job.business.service.CompletionJobService", _FakeJobService):
        response = complete_bill_credit_router(
            public_id="bill-credit-public-id",
            current_user={"tenant_id": 1},
        )

    assert response == complete_result
