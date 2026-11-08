"""U-778d1 b2 -- POST /api/v1/qbo/invoice-push/{dry-run,compare}. Read-only: no business writes to QBO or the DB.
(The QBO transport meters each call -- `IncrementQboApiUsage`, fail-open -- and may refresh the OAuth token: the
normal side effects of any QBO read.) `compare` answers 404 for an invoice that does not belong to the proposal
project's QBO customer, returning nothing about it.

Gated by `QBO_INVOICE_PUSH` (off | dry_run | on; default off -> 404), `Modules.INVOICES can_complete`, and project
access (a project the caller cannot see answers 404, like every other by-id project read).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from integrations.intuit.qbo.invoice_push.api.schemas import CompareIn, ProposalIn
from integrations.intuit.qbo.invoice_push.business.mode import invoice_push_mode
from integrations.intuit.qbo.invoice_push.business.model import InvoiceNotFound
from integrations.intuit.qbo.invoice_push.business.service import InvoicePushDryRunService
from shared.access import assert_can_access_project
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1/qbo/invoice-push", tags=["api", "qbo-invoice-push"])


def get_push_service() -> InvoicePushDryRunService:
    from integrations.intuit.qbo.invoice_push.external.reader import LiveQboReader
    from integrations.intuit.qbo.invoice_push.persistence.repo import InvoicePushRepository
    return InvoicePushDryRunService(repo=InvoicePushRepository(),
                                    qbo_reader_factory=lambda realm_id: LiveQboReader(realm_id))


def _require_enabled() -> None:
    if invoice_push_mode() == "off":
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/dry-run")
def dry_run_router(body: ProposalIn,
                   current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete")),
                   service: InvoicePushDryRunService = Depends(get_push_service)):
    _require_enabled()
    assert_can_access_project(body.project_id)
    return item_response(service.dry_run(body.to_proposal()).to_dict())


@router.post("/compare")
def compare_router(body: CompareIn,
                   current_user: dict = Depends(require_module_api(Modules.INVOICES, "can_complete")),
                   service: InvoicePushDryRunService = Depends(get_push_service)):
    _require_enabled()
    assert_can_access_project(body.proposal.project_id)
    try:
        return item_response(service.compare(body.proposal.to_proposal(), body.qbo_invoice_id))
    except InvoiceNotFound:  # the service maps QBO absence of THE COMPARED invoice to this; nothing else
        raise HTTPException(status_code=404, detail="Not found")
