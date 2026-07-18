# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from entities.completion_job.business.model import CompletionJob
from entities.completion_job.persistence.repo import CompletionJobRepository
from shared.authz import current_company_id, current_is_system_admin, current_user_id, set_authz_context

logger = logging.getLogger(__name__)


class CompletionJobService:
    """Durable completion orchestration + reclaim dispatch.

    The was_created lease (CreateCompletionJob) applies only to async Bill/Expense
    /complete routes that schedule a BackgroundTask. BillCredit/Invoice complete
    synchronously in the request — no background task to dedup; coalesced job +
    guarded marks + idempotent completion cover their concurrent case.
    """

    def __init__(self, repo: Optional[CompletionJobRepository] = None):
        self._repo = repo or CompletionJobRepository()

    def enqueue(self, entity_type: str, entity_public_id: str) -> CompletionJob:
        company_id = current_company_id.get()
        return self._repo.create(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            company_id=company_id,
        )

    def claim_next_stuck(
        self, *, reclaim_after_seconds: int = 1800, max_attempts: int = 5
    ) -> Optional[CompletionJob]:
        return self._repo.claim_next_stuck(
            reclaim_after_seconds=reclaim_after_seconds,
            max_attempts=max_attempts,
        )

    def mark_success(self, public_id: str) -> None:
        self._repo.mark_success(public_id=public_id)

    def mark_failure(self, public_id: str, last_error: Optional[str] = None, max_attempts: int = 5) -> None:
        self._repo.mark_failure(public_id=public_id, last_error=last_error, max_attempts=max_attempts)

    def run_job(self, job: CompletionJob) -> None:
        """Reclaim path only — never re-raises.

        Reclaim fires only after N=1800s (default), far beyond real completion
        time (seconds). Residual: the one non-idempotent-under-overlap path is
        the MS Excel enqueue — outbox rows never coalesce and column-Z is only
        checked at enqueue — so a reclaim overlapping a still-alive >30-min-hung
        completion could duplicate DETAILS rows. This is a documented, N-guarded
        residual; the proper fix (column-Z re-check at Excel drain time) is a
        separate follow-up unit touching the shared MS Excel drain worker.
        """
        prior_uid = current_user_id.get()
        prior_cid = current_company_id.get()
        prior_isa = current_is_system_admin.get()
        set_authz_context(user_id=None, company_id=None, is_system_admin=True)
        try:
            self._run_job_inner(job)
        finally:
            set_authz_context(user_id=prior_uid, company_id=prior_cid, is_system_admin=prior_isa)

    def _run_job_inner(self, job: CompletionJob) -> None:
        public_id = job.entity_public_id
        job_public_id = job.public_id
        # Marking contract: Bill/Expense self-mark success/failure inside their
        # _run_complete_* wrappers, so they are NOT marked here. Only the sync
        # entities (BillCredit/Invoice) are marked here on normal return, and in
        # the except clause below — keep that ("BillCredit", "Invoice") tuple in
        # sync with these branches when adding a new entity type.
        try:
            if job.entity_type == "Bill":
                from entities.bill.api.router import _run_complete_bill

                _run_complete_bill(public_id, job_public_id, force=True)
            elif job.entity_type == "Expense":
                from entities.expense.api.router import _run_complete_expense

                _run_complete_expense(public_id, job_public_id, force=True)
            elif job.entity_type == "BillCredit":
                from entities.bill_credit.business.complete_service import BillCreditCompleteService

                BillCreditCompleteService().complete_bill_credit(public_id=public_id)
                # Returned dict (any status_code incl. 207/4xx/5xx) = orchestration finished;
                # outbox retries external writes. Only raised exceptions mark failure.
                self.mark_success(job_public_id)
            elif job.entity_type == "Invoice":
                from entities.invoice.business.service import InvoiceService

                InvoiceService().complete_invoice(public_id=public_id)
                # Returned dict (any status_code incl. 207/4xx/5xx) = orchestration finished;
                # outbox retries external writes. Only raised exceptions mark failure.
                self.mark_success(job_public_id)
            else:
                logger.error("Unknown completion job entity type: %s", job.entity_type)
                self.mark_failure(job_public_id, f"Unknown entity type: {job.entity_type}")
        except Exception as error:
            logger.exception(
                "Completion job reclaim failed: job_public_id=%s entity_type=%s entity_public_id=%s",
                job_public_id,
                job.entity_type,
                public_id,
            )
            if job.entity_type in ("BillCredit", "Invoice"):
                self.mark_failure(job_public_id, str(error))
