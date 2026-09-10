"""U-434 — the two defects that made `complete_bill` lose completions silently.

Both were found by U-426's re-review, independently by 6 of its 10 finder
angles, and they compounded:

1. `complete_bill`'s 3-attempt row-version retry loop was UNREACHABLE dead code.
   `BillRepository.update_by_id` RAISES `map_database_error(...)` on a zero-row
   UPDATE and never returns `None`, so the `else: ... retrying` branch could not
   run and `time.sleep(0.2)` never executed in production. The race the loop was
   written for — BillEdit's 300ms auto-save landing between completion's re-read
   and its UPDATE — therefore produced a hard 500 on attempt 1.

2. `_run_complete_bill` then called `job_service.mark_success()` for ANY dict
   `complete_bill` returned, on the stated theory that "a returned dict =
   finalize+enqueue ran". False for the early returns, which return BEFORE the
   finalize and before Step 5's outbox enqueue. Because `claim_next_stuck` keys
   on job status, marking them successful retired the job and the reclaim
   watchdog skipped them forever: the bill stayed `IsDraft=1` and its AP never
   reached QBO/SharePoint/Excel/Box, while the client already had its 202.

The fix: finalization becomes one idempotent state transition
(`FinalizeBillById`, guarded on `IsDraft = 1`, no `@RowVersion`), and the job
marking keys on `bill_finalized` — True on every path that got past the finalize
(including the 207 partial-success path, where the outbox legitimately owns the
retries) and False on exactly the early returns.

ACCEPTED RESIDUAL (Chris's call, 2026-09-09 — option 2 of 3): making failures
visible to the watchdog necessarily makes reclaims fire, and the MS Excel enqueue
is not idempotent under overlap (outbox rows never coalesce; column-Z is checked
only at enqueue). So a reclaim can duplicate DETAILS rows. That trade was taken
knowingly — a silently dropped AP push is worse than a visible, correctable
duplicate row. The proper fix (column-Z re-check at Excel drain time) is a
separate unit touching the shared MS drain worker, and is booked.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import entities.bill.api.router as bill_router
from entities.bill.business.service import BillService


def _bill(**over):
    base = dict(
        id=55,
        public_id="pub-55",
        row_version="cm9ja2V0",
        vendor_id=7,
        payment_term_id=None,
        bill_date="2026-09-01",
        due_date="2026-09-01",
        bill_number="INV-1",
        total_amount=None,
        memo=None,
        is_draft=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _service(*, finalize_returns=..., duplicate=None):
    repo = MagicMock()
    repo.finalize_by_id.return_value = _bill(is_draft=False) if finalize_returns is ... else finalize_returns
    repo.read_by_bill_number_and_vendor_id.return_value = duplicate
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_bill())
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = []
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    return svc, repo


# ---------------------------------------------------------------------------
# Defect 1 — finalization is one idempotent transition, not a retry loop
# ---------------------------------------------------------------------------


def test_finalize_goes_through_the_idempotent_sproc_not_a_rowversion_update():
    svc, repo = _service()
    with patch.object(BillService, "update_by_public_id") as mock_update:
        result = svc.complete_bill(public_id="pub-55")
    repo.finalize_by_id.assert_called_once_with(id=55)
    assert mock_update.call_count == 0
    assert result["bill_finalized"] is True


def test_finalize_carries_no_row_version():
    """The whole point: an unrelated concurrent field edit must not block it."""
    svc, repo = _service()
    svc.complete_bill(public_id="pub-55")
    kwargs = repo.finalize_by_id.call_args.kwargs
    assert "row_version" not in kwargs and "RowVersion" not in kwargs
    assert set(kwargs) == {"id"}


def test_no_sleep_and_no_retry_remain_on_the_finalize_path():
    """Pins the removal, so a future 'fix' can't reintroduce the retry loop.

    `time` is no longer imported by the module at all; asserting that is a
    cheap, exact proxy for "no sleep-based retry lives here".
    """
    import entities.bill.business.service as svc_mod

    assert not hasattr(svc_mod, "time"), (
        "entities.bill.business.service re-imported `time` — the sleep-based "
        "retry loop U-434 deleted is likely back"
    )


def test_bill_deleted_mid_finalize_is_a_404_not_a_silent_success():
    """finalize_by_id returns None ONLY when the Bill is gone.

    The sproc re-SELECTs unconditionally, so an already-finalized bill still
    yields a row; None is unambiguous.
    """
    svc, repo = _service(finalize_returns=None)
    result = svc.complete_bill(public_id="pub-55")
    assert result["status_code"] == 404
    assert result["bill_finalized"] is False


def test_missing_vendor_still_refuses_before_finalizing():
    svc, repo = _service()
    svc.vendor_service.read_by_id.return_value = None
    result = svc.complete_bill(public_id="pub-55")
    assert result["status_code"] == 400
    assert result["bill_finalized"] is False
    repo.finalize_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# The guard that moving the field-write could have silently dropped
# ---------------------------------------------------------------------------


def test_duplicate_check_still_runs_on_the_completion_path():
    """This guard lived inline in update_by_public_id, reachable only when a
    caller passed is_draft=False — which is how completion used to finalize.
    Dropping the field-write would have dropped the guard with it, in a
    different method from the deleted lines: invisible to a diff review."""
    svc, repo = _service(duplicate=SimpleNamespace(public_id="some-other-bill"))
    result = svc.complete_bill(public_id="pub-55")

    # Behaviour PRESERVED, not changed: the guard's ValueError is caught by
    # completion's own handler and surfaces as a non-finalized result dict —
    # exactly as it did before U-434, when the same ValueError came out of
    # update_by_public_id into the same handler. The 500 (rather than a 409) is
    # pre-existing and deliberately left alone; U-434 is not a status-code unit.
    assert result["bill_finalized"] is False
    assert result["status_code"] == 500
    assert "already exists for this vendor" in result["message"]
    repo.finalize_by_id.assert_not_called()


def test_duplicate_check_ignores_the_bill_itself():
    svc, repo = _service(duplicate=SimpleNamespace(public_id="PUB-55"))  # case-insensitive
    result = svc.complete_bill(public_id="pub-55")
    assert result["bill_finalized"] is True


def test_duplicate_check_noops_on_an_incomplete_draft():
    """A draft may legitimately lack vendor or bill number — and
    UQ_Bill_VendorId_BillNumber_BillDate is filtered to non-NULL for the same
    reason, so the Python guard must agree with the index."""
    svc, repo = _service()
    svc.read_by_public_id = MagicMock(return_value=_bill(bill_number=None))
    svc.complete_bill(public_id="pub-55")
    repo.read_by_bill_number_and_vendor_id.assert_not_called()


# ---------------------------------------------------------------------------
# Defect 2 — the job-marking contract
# ---------------------------------------------------------------------------


def _run(result, *, bill=..., force=False):
    """Drive _run_complete_bill with complete_bill stubbed to `result`."""
    job = MagicMock()
    with patch.object(bill_router, "BillService") as MockSvc, \
         patch("entities.completion_job.business.service.CompletionJobService", return_value=job), \
         patch.object(bill_router, "BillRepository") as MockRepo:
        MockSvc.return_value.read_by_public_id.return_value = _bill() if bill is ... else bill
        MockSvc.return_value.complete_bill.return_value = result
        MockRepo.return_value.set_completion_result.return_value = None
        bill_router._run_complete_bill("pub-55", "job-1", force)
    return job


def _ok(**over):
    r = {"status_code": 200, "message": "ok", "bill_finalized": True, "errors": []}
    r.update(over)
    return r


@pytest.mark.parametrize("status_code", [200, 207])
def test_finalized_completions_mark_success(status_code):
    """207 is partial success — finalize+enqueue DID run, so the outbox owns the
    retries and the job is legitimately done."""
    job = _run(_ok(status_code=status_code))
    job.mark_success.assert_called_once_with("job-1")
    job.mark_failure.assert_not_called()


@pytest.mark.parametrize(
    "status_code,message",
    [
        (404, "Bill not found during finalization"),
        (400, "Vendor not found for bill"),
        (500, "Error finalizing bill: boom"),
    ],
)
def test_pre_enqueue_failures_mark_failure_so_the_watchdog_can_see_them(status_code, message):
    """THE core regression. Each of these returns before Step 5's enqueue, so
    nothing was queued and nothing retries. Marking them successful retired the
    job and `claim_next_stuck` — which keys on job status — skipped them
    forever."""
    job = _run({"status_code": status_code, "message": message, "bill_finalized": False, "errors": []})
    job.mark_failure.assert_called_once()
    assert job.mark_success.call_count == 0
    assert str(status_code) in job.mark_failure.call_args.args[1]


def test_a_job_pointing_at_a_deleted_bill_marks_failure():
    job = _run(_ok(), bill=None)
    job.mark_failure.assert_called_once_with("job-1", "Bill not found")
    job.mark_success.assert_not_called()


def test_an_already_completed_bill_marks_success_as_an_idempotent_noop():
    job = _run(_ok(), bill=_bill(is_draft=False))
    job.mark_success.assert_called_once_with("job-1")
    job.mark_failure.assert_not_called()


def test_force_redrives_an_already_completed_bill():
    """The reclaim watchdog's force=True must bypass the already-done skip."""
    job = _run(_ok(), bill=_bill(is_draft=False), force=True)
    job.mark_success.assert_called_once_with("job-1")


# ---------------------------------------------------------------------------
# The sproc contract, pinned against the .sql file
# ---------------------------------------------------------------------------


def test_finalize_sproc_is_idempotent_and_pyodbc_safe():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"
    params = sproc_params(base, "FinalizeBillById")
    body = sproc_body(base, "FinalizeBillById")

    assert "@RowVersion" not in params, (
        "FinalizeBillById must NOT take a RowVersion — that predicate is the "
        "race U-434 removed"
    )
    assert "[IsDraft] = 1" in body, "the transition must be guarded on IsDraft=1 (idempotency)"
    assert "SET NOCOUNT ON" in body, "mutation sproc + trailing SELECT needs NOCOUNT (pyodbc, 2026-06-11)"
    # A DML statement followed by a SELECT: the SELECT must be unconditional so
    # already-finalized and missing are distinguishable by presence, not rowcount.
    assert body.count("SELECT") >= 1 and "FROM dbo.[Bill]" in body
    assert "[QboId]" in body, "projected here so this path doesn't repeat UpdateBillById's omission"
