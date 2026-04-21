# Python Standard Library Imports
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# Module-level singleton. Populated by start_scheduler() on app startup;
# consulted by shutdown_scheduler() on app shutdown.
_scheduler = None  # type: ignore


def _scheduler_enabled() -> bool:
    """
    Default-deny gate. Local dev doesn't start the scheduler unless opted in.
    Production App Service sets ENABLE_SCHEDULER=true in Application Settings.
    """
    return os.getenv("ENABLE_SCHEDULER", "").strip().lower() == "true"


def start_scheduler() -> None:
    """
    Start the APScheduler AsyncIOScheduler and register all recurring jobs.
    No-op if ENABLE_SCHEDULER is not explicitly "true".

    Intended to be called from FastAPI's startup event. Safe to call more
    than once — second call is a no-op.

    Jobs registered:
      - qbo_outbox_drain: every 5 seconds. Claims one outbox row per tick
        and dispatches it. Cross-process contention is handled via
        sp_getapplock inside the worker.
    """
    global _scheduler

    if not _scheduler_enabled():
        logger.info(
            "Scheduler disabled (ENABLE_SCHEDULER is not 'true'). Jobs will not run."
        )
        return

    if _scheduler is not None:
        logger.debug("Scheduler already started; skipping.")
        return

    # Lazy import so the module is importable when APScheduler isn't installed
    # (e.g., a scenario where requirements haven't been refreshed yet).
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as error:
        logger.warning(
            f"APScheduler not installed; scheduler will not start: {error}"
        )
        return

    scheduler = AsyncIOScheduler(timezone="UTC")

    # --- Job registration -------------------------------------------------- #

    _register_qbo_outbox_drain(scheduler)
    _register_qbo_pull_jobs(scheduler)
    _register_qbo_reconcile_jobs(scheduler)

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started. Registered jobs: qbo_outbox_drain (5s), "
        "qbo_sync_{bill,invoice,purchase,vendorcredit} (15m), "
        "qbo_sync_{vendor,customer,item,account,term} (4h), "
        "qbo_sync_company_info (daily), qbo_reconcile_bills (daily)."
    )


def _register_qbo_outbox_drain(scheduler) -> None:
    """Register the outbox drain tick."""
    from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

    def _drain_qbo_outbox() -> None:
        # Exception isolator — never let a drain error kill the scheduler.
        try:
            QboOutboxWorker().drain_once()
        except Exception:
            logger.exception("qbo.outbox.drain.tick_failed")

    scheduler.add_job(
        _drain_qbo_outbox,
        trigger="interval",
        seconds=5,
        id="qbo_outbox_drain",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _register_qbo_pull_jobs(scheduler) -> None:
    """
    Register all scheduled QBO pulls. Tiered cadence per Chapter 2 decisions:
      - Transactional (bill/invoice/purchase/vendorcredit) every 15 minutes.
      - Reference (vendor/customer/item/account/term) every 4 hours.
      - CompanyInfo daily.

    Each job's initial fire is staggered so a cold-start doesn't fire all four
    transactional syncs simultaneously (they don't conflict at the DB level but
    staggering is polite to QBO's rate limits and makes logs easier to read).
    """
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)

    def _isolated(entity_name: str, sync_fn):
        """Wrap a sync function with structured logging + exception isolation."""

        def run() -> None:
            logger.info(
                "qbo.sync.pull.started",
                extra={
                    "event_name": "qbo.sync.pull.started",
                    "operation_name": f"qbo.sync.{entity_name}",
                    "entity_type": entity_name,
                },
            )
            try:
                result = sync_fn()
                logger.info(
                    "qbo.sync.pull.completed",
                    extra={
                        "event_name": "qbo.sync.pull.completed",
                        "operation_name": f"qbo.sync.{entity_name}",
                        "entity_type": entity_name,
                        "outcome": "success",
                        "result_summary": _summarize_sync_result(result),
                    },
                )
            except Exception as error:
                logger.exception(
                    "qbo.sync.pull.failed",
                    extra={
                        "event_name": "qbo.sync.pull.failed",
                        "operation_name": f"qbo.sync.{entity_name}",
                        "entity_type": entity_name,
                        "outcome": "failure",
                        "error_class": type(error).__name__,
                    },
                )

        return run

    # --- Transactional tier: every 15 minutes, staggered by 3 minutes ---- #

    from scripts.sync_qbo_bill import sync_qbo_bill
    from scripts.sync_qbo_invoice import sync_qbo_invoice
    from scripts.sync_qbo_purchase import sync_qbo_purchase
    from scripts.sync_qbo_vendorcredit import sync_qbo_vendorcredit

    transactional = [
        ("bill", sync_qbo_bill),
        ("invoice", sync_qbo_invoice),
        ("purchase", sync_qbo_purchase),
        ("vendorcredit", sync_qbo_vendorcredit),
    ]
    for i, (entity_name, sync_fn) in enumerate(transactional):
        scheduler.add_job(
            _isolated(entity_name, sync_fn),
            trigger="interval",
            minutes=15,
            id=f"qbo_sync_{entity_name}",
            next_run_time=now_utc + timedelta(minutes=(i * 3) + 1),  # stagger: 1, 4, 7, 10 min after start
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    # --- Reference tier: every 4 hours, staggered by 20 minutes ---------- #

    from scripts.sync_qbo_vendor import sync_qbo_vendor
    from scripts.sync_qbo_customer import sync_qbo_customer
    from scripts.sync_qbo_item import sync_qbo_item
    from scripts.sync_qbo_account import sync_qbo_account
    from scripts.sync_qbo_term import sync_qbo_term

    reference = [
        ("vendor", sync_qbo_vendor),
        ("customer", sync_qbo_customer),
        ("item", sync_qbo_item),
        ("account", sync_qbo_account),
        ("term", sync_qbo_term),
    ]
    for i, (entity_name, sync_fn) in enumerate(reference):
        scheduler.add_job(
            _isolated(entity_name, sync_fn),
            trigger="interval",
            hours=4,
            id=f"qbo_sync_{entity_name}",
            next_run_time=now_utc + timedelta(minutes=(i * 20) + 15),  # 15, 35, 55, 75, 95 min after start
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    # --- Singleton tier: CompanyInfo once per day ------------------------ #

    from scripts.sync_qbo_company_info import sync_qbo_company_info

    scheduler.add_job(
        _isolated("company_info", sync_qbo_company_info),
        trigger="interval",
        hours=24,
        id="qbo_sync_company_info",
        next_run_time=now_utc + timedelta(hours=2),  # wait for other tiers to settle first
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _register_qbo_reconcile_jobs(scheduler) -> None:
    """
    Register reconciliation jobs. Full-scan reconciliation is the safety net
    for delta sync — it catches records the watermark missed and drift that
    accumulated from partial failures. Daily cadence is sufficient for a
    bookkeeping integration; more frequent would be wasteful at this volume.

    Only the Bill detector is wired up for now (task #16 scope). Additional
    entity types (Invoice, Purchase, VendorCredit) can be added here as
    their detectors are implemented in ReconciliationService.
    """
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)

    def _reconcile_bills() -> None:
        try:
            from integrations.intuit.qbo.auth.business.service import QboAuthService
            from integrations.intuit.qbo.reconciliation.business.service import (
                ReconciliationService,
            )
            auth = QboAuthService().ensure_valid_token()
            if not auth or not auth.realm_id:
                logger.warning("qbo.reconcile.bill.skipped: no valid QBO auth")
                return
            ReconciliationService().reconcile_bills(realm_id=auth.realm_id)
        except Exception:
            logger.exception("qbo.reconcile.bill.tick_failed")

    # Daily at a calm hour. `next_run_time` sets the first fire as 3 hours
    # after startup so it doesn't collide with initial boot-time pulls.
    scheduler.add_job(
        _reconcile_bills,
        trigger="interval",
        hours=24,
        id="qbo_reconcile_bills",
        next_run_time=now_utc + timedelta(hours=3),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _summarize_sync_result(result) -> dict:
    """
    Best-effort extraction of the interesting bits of a sync function's return.
    The scripts each return different dict shapes; we just surface the top-level
    keys and a few common counters so log records are reasonably queryable
    in App Insights without being bloated.
    """
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    # Surface top-level keys and common counters if present.
    summary: dict = {}
    for key in ("success", "status_code", "message", "end_time"):
        if key in result:
            summary[key] = result[key]
    # sync_qbo_bill etc. nest the envelope under "result"; unwrap once.
    inner = result.get("result") if isinstance(result.get("result"), dict) else None
    if inner:
        for key in ("success", "status_code", "start_time", "end_time"):
            if key in inner:
                summary.setdefault(key, inner[key])
    return summary


def shutdown_scheduler() -> None:
    """
    Gracefully stop the scheduler if it was started. Waits for in-flight
    jobs to finish before returning. No-op if scheduler was never started.
    """
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler shut down.")
    except Exception:
        logger.exception("Error during scheduler shutdown")
    finally:
        _scheduler = None


def get_scheduler():
    """Return the running scheduler instance (or None if not started)."""
    return _scheduler
