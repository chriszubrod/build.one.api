"""U-435 — the completion-job marking bug was a FAMILY, not a Bill bug.

U-434 fixed `Bill`. Pass 2's reuse lens then found `entities/expense/api/router.py`
carrying the identical unconditional `mark_success` under the identical false
comment, verbatim, and `CompletionJobService._run_job_inner` doing the same for
BillCredit and Invoice. All four completable entities had it.

The defect, in one line: `mark_success()` was called for ANY dict a `complete_*`
returned, on the stated theory that "a returned dict = finalize+enqueue ran".
That is false for the early returns (404/400/500), which return BEFORE the
finalize and before the outbox enqueue — so nothing was queued and nothing
retries. Because `claim_next_stuck` keys on job status, marking them successful
retired the job and the reclaim watchdog skipped them forever: the entity stayed
`IsDraft=1` and its money never reached QBO/SharePoint/Excel/Box, while the
client already held a 202.

Every `complete_*` returns `{entity}_finalized`, True on every path past the
finalize — INCLUDING the 207 partial-success path, where the outbox legitimately
owns the retries — and False on exactly the early returns. That flag is the
discriminator.

This file covers the EXPENSE half. BillCredit and Invoice are covered in
`test_completion_job_service.py` (they share `_mark_from_result`); Bill is
covered in `test_u434_complete_bill_finalize_and_job_marking.py`.

Written because a mutation check found the gap: reverting the Expense fix to an
unconditional `mark_success` left the whole suite GREEN. A fix nothing can catch
is a fix that silently rots.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import entities.expense.api.router as expense_router


def _expense(is_draft=True):
    return SimpleNamespace(id=9, public_id="exp-9", is_draft=is_draft)


def _run(result, *, expense=..., force=False):
    """Drive _run_complete_expense with complete_expense stubbed to `result`."""
    job = MagicMock()
    with patch.object(expense_router, "ExpenseService") as MockSvc, \
         patch("entities.completion_job.business.service.CompletionJobService", return_value=job):
        MockSvc.return_value.read_by_public_id.return_value = _expense() if expense is ... else expense
        MockSvc.return_value.complete_expense.return_value = result
        expense_router._run_complete_expense("exp-9", "job-1", force)
    return job


def _ok(**over):
    r = {"status_code": 200, "message": "ok", "expense_finalized": True, "errors": []}
    r.update(over)
    return r


@pytest.mark.parametrize("status_code", [200, 207])
def test_finalized_expense_completions_mark_success(status_code):
    """207 is partial success — the finalize and enqueue DID run, so the outbox
    owns the retries and the job is legitimately done."""
    job = _run(_ok(status_code=status_code))
    job.mark_success.assert_called_once_with("job-1")
    job.mark_failure.assert_not_called()


@pytest.mark.parametrize(
    "status_code,message",
    [
        (404, "Expense not found during finalization"),
        (400, "Vendor not found for expense"),
        (500, "Error finalizing expense: boom"),
    ],
)
def test_pre_enqueue_failures_mark_failure(status_code, message):
    """THE core regression. Each returns before the enqueue, so nothing retries.
    Marking these successful is what made the loss silent AND permanent."""
    job = _run({"status_code": status_code, "message": message, "expense_finalized": False, "errors": []})
    job.mark_failure.assert_called_once()
    job.mark_success.assert_not_called()
    assert str(status_code) in job.mark_failure.call_args.args[1]


def test_a_job_pointing_at_a_deleted_expense_marks_failure():
    job = _run(_ok(), expense=None)
    job.mark_failure.assert_called_once_with("job-1", "Expense not found")
    job.mark_success.assert_not_called()


def test_an_already_completed_expense_marks_success_as_an_idempotent_noop():
    job = _run(_ok(), expense=_expense(is_draft=False))
    job.mark_success.assert_called_once_with("job-1")
    job.mark_failure.assert_not_called()


def test_force_redrives_an_already_completed_expense():
    """The reclaim watchdog's force=True must bypass the already-done skip."""
    job = _run(_ok(), expense=_expense(is_draft=False), force=True)
    job.mark_success.assert_called_once_with("job-1")


def test_no_unconditional_mark_success_survives_in_the_family():
    """Structural guard across all four entities.

    The bug's signature was `mark_success` reached with no test on a finalized
    flag. Both runners must gate on their own flag; the shared runner must route
    BillCredit and Invoice through `_mark_from_result`. This is the pin that a
    future copy-paste of the old shape would trip.
    """
    import inspect
    import entities.bill.api.router as bill_router
    from entities.completion_job.business.service import CompletionJobService

    bill_src = inspect.getsource(bill_router._run_complete_bill)
    exp_src = inspect.getsource(expense_router._run_complete_expense)
    runner_src = inspect.getsource(CompletionJobService._run_job_inner)

    assert 'result.get("bill_finalized")' in bill_src
    assert 'result.get("expense_finalized")' in exp_src
    assert runner_src.count("_mark_from_result") == 2, (
        "BillCredit and Invoice must both route through _mark_from_result"
    )
    assert "# Returned dict (any status_code" not in bill_src + exp_src + runner_src, (
        "the false 'a returned dict = finalize+enqueue ran' comment is back — "
        "the marking contract has probably been reverted with it"
    )
