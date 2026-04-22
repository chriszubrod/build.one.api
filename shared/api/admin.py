# Python Standard Library Imports
import asyncio
import hmac
import logging
import time
from typing import Any, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status

# Local Imports
import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["api", "admin"])


VALID_QBO_ENTITIES = {
    "bill",
    "invoice",
    "purchase",
    "vendorcredit",
    "vendor",
    "customer",
    "item",
    "account",
    "term",
    "company_info",
}


def _require_drain_secret(x_drain_secret: Optional[str] = Header(default=None, alias="X-Drain-Secret")) -> None:
    """
    Validate the caller presented the shared drain secret. Used for machine-
    to-machine calls from the scheduler Function App. Fails closed when the
    server has no secret configured, so a missing env var can't silently
    open the admin surface.
    """
    configured = (config.Settings().drain_secret or "").strip()
    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Drain secret not configured on server")
    provided = (x_drain_secret or "").strip()
    if not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-Drain-Secret")


async def _timed(job_name: str, sync_fn) -> dict[str, Any]:
    """Run a blocking callable off the event loop and return a timing envelope."""
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(sync_fn)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info("admin.job.completed job=%s duration_ms=%d", job_name, duration_ms)
        payload = result if isinstance(result, (dict, list, int, str, type(None))) else {"type": type(result).__name__}
        return {
            "status": "ok",
            "job": job_name,
            "duration_ms": duration_ms,
            "result": payload,
        }
    except HTTPException:
        raise
    except Exception as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("admin.job.failed job=%s duration_ms=%d", job_name, duration_ms)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{job_name} failed after {duration_ms}ms: {error}",
        )


# --- Outbox drain ---------------------------------------------------------- #


@router.post("/outbox/drain", dependencies=[Depends(_require_drain_secret)])
async def drain_outbox_router():
    """
    Drain QBO and MS outboxes once. Called on a short cadence (e.g. 30s)
    by the scheduler Function App. Each integration drains independently;
    a failure in one does not prevent the other from running.
    """
    def _run() -> dict[str, Any]:
        from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
        from integrations.ms.outbox.business.worker import MsOutboxWorker
        summary: dict[str, Any] = {}
        try:
            QboOutboxWorker().drain_once()
            summary["qbo"] = "ok"
        except Exception as error:
            logger.exception("qbo.outbox.drain.failed")
            summary["qbo"] = {"error": str(error)}
        try:
            MsOutboxWorker().drain_once()
            summary["ms"] = "ok"
        except Exception as error:
            logger.exception("ms.outbox.drain.failed")
            summary["ms"] = {"error": str(error)}
        return summary

    return await _timed("outbox.drain", _run)


# --- QBO pulls ------------------------------------------------------------- #


def _qbo_sync_fn(entity: str):
    """Lazy-lookup of the sync_qbo_* script for a given entity."""
    if entity == "bill":
        from scripts.sync_qbo_bill import sync_qbo_bill
        return sync_qbo_bill
    if entity == "invoice":
        from scripts.sync_qbo_invoice import sync_qbo_invoice
        return sync_qbo_invoice
    if entity == "purchase":
        from scripts.sync_qbo_purchase import sync_qbo_purchase
        return sync_qbo_purchase
    if entity == "vendorcredit":
        from scripts.sync_qbo_vendorcredit import sync_qbo_vendorcredit
        return sync_qbo_vendorcredit
    if entity == "vendor":
        from scripts.sync_qbo_vendor import sync_qbo_vendor
        return sync_qbo_vendor
    if entity == "customer":
        from scripts.sync_qbo_customer import sync_qbo_customer
        return sync_qbo_customer
    if entity == "item":
        from scripts.sync_qbo_item import sync_qbo_item
        return sync_qbo_item
    if entity == "account":
        from scripts.sync_qbo_account import sync_qbo_account
        return sync_qbo_account
    if entity == "term":
        from scripts.sync_qbo_term import sync_qbo_term
        return sync_qbo_term
    if entity == "company_info":
        from scripts.sync_qbo_company_info import sync_qbo_company_info
        return sync_qbo_company_info
    raise HTTPException(status_code=400, detail=f"Unknown QBO entity: {entity}")


@router.post("/sync/qbo/{entity}", dependencies=[Depends(_require_drain_secret)])
async def sync_qbo_router(entity: str = Path(...)):
    """
    Run a single QBO sync script (pull from QBO → local DB). Entities:
    bill, invoice, purchase, vendorcredit, vendor, customer, item, account,
    term, company_info.
    """
    if entity not in VALID_QBO_ENTITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid entity '{entity}'. Valid: {', '.join(sorted(VALID_QBO_ENTITIES))}",
        )
    sync_fn = _qbo_sync_fn(entity)
    return await _timed(f"sync.qbo.{entity}", sync_fn)


# --- Reconciliation -------------------------------------------------------- #


@router.post("/reconcile/qbo", dependencies=[Depends(_require_drain_secret)])
async def reconcile_qbo_router():
    """Run the QBO daily reconciliation (Bill detector today; more later)."""
    def _run() -> dict[str, Any]:
        from integrations.intuit.qbo.auth.business.service import QboAuthService
        from integrations.intuit.qbo.reconciliation.business.service import ReconciliationService
        auth = QboAuthService().ensure_valid_token()
        if not auth or not auth.realm_id:
            return {"skipped": True, "reason": "no valid QBO auth"}
        ReconciliationService().reconcile_bills(realm_id=auth.realm_id)
        return {"reconciled": True, "realm_id": auth.realm_id}

    return await _timed("reconcile.qbo", _run)


@router.post("/reconcile/ms", dependencies=[Depends(_require_drain_secret)])
async def reconcile_ms_router():
    """Run the MS daily Excel missing-row reconciliation."""
    def _run() -> dict[str, Any]:
        from integrations.ms.reconciliation.business.excel_detector import ExcelMissingRowDetector
        ExcelMissingRowDetector().run()
        return {"reconciled": True}

    return await _timed("reconcile.ms", _run)
