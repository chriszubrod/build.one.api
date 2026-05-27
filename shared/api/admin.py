# Python Standard Library Imports
import asyncio
import hmac
import logging
import os
import time
from typing import Any, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status

# Local Imports
import config
from shared.authz import set_authz_context

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

    Side effect (2026-05-12): on successful auth, populates the per-request
    `current_is_system_admin = True` so Phase 3+ entity sprocs allow the
    `@ActorIsSystemAdmin = 1` bypass and return all rows. This is what
    drain-secret callers (outbox drain, QBO sync, reconciliation, etc.)
    need — they read across all users by design. The previous `OR @ActorUserId
    IS NULL` sproc-level bypass was removed (migration `002_remove_legacy_actor_bypass`)
    after a leak was found where a regressed auth path silently fell through
    to "no actor → show everything" for user-facing requests. Drain-secret
    callers explicitly declare system intent here instead.
    """
    configured = (config.Settings().drain_secret or "").strip()
    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Drain secret not configured on server")
    provided = (x_drain_secret or "").strip()
    if not hmac.compare_digest(provided, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-Drain-Secret")

    # Authenticated as scheduler. Mark this request as system-level so
    # downstream entity sprocs bypass row-level user scoping via the
    # @ActorIsSystemAdmin = 1 clause. We never want the no-actor leak path
    # to be reachable post-002 migration — every legitimate cross-user
    # read either runs as a system admin user (JWT with isa=true), an
    # actual admin (also isa=true), or as a drain-secret call (this path).
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)


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


# --- Email inbox poll ------------------------------------------------------ #


@router.post("/email/poll", dependencies=[Depends(_require_drain_secret)])
async def email_poll_router():
    """
    Poll the configured shared invoice mailbox in three stages:
      1. Inbox  — new inbound mail for the agent to pick up.
      2. Sent   — our own outbound forwards (audit trail). Stored with
                  ProcessingStatus='outbound' so the agent runner skips
                  them.
      3. Reconcile Review.EmailMessageId on auto-advanced "In Review"
                  rows whose forward only became an EmailMessage row in
                  step 2.

    Returns a combined summary. Each stage is idempotent.

    Failure mode is load-bearing: when the inbox or sent stage returns
    a non-2xx `status_code`, this handler raises HTTPException(502) so
    the caller (Function App, App Insights, ad-hoc curl) sees a
    non-success response. Wrapping a Graph 4xx inside an HTTP 200
    envelope was the root cause of the 19h "silent poll" outage on
    2026-05-08 — never reintroduce that.

    Operator pause: set env var `PAUSE_EMAIL_POLL=true` on App Service
    and restart. While set, this endpoint returns `{paused: true}`
    without contacting Graph or upserting any EmailMessage / EmailAttachment
    rows. Used to halt ingestion during data-corruption investigations
    (Phase 0 of the 2026-05-27 reconciliation work) without restarting
    the Function App (which has Flex Consumption re-enable gotchas).
    """
    started = time.monotonic()

    pause_flag = (os.environ.get("PAUSE_EMAIL_POLL") or "").strip().lower()
    if pause_flag in ("true", "1", "yes"):
        return {
            "status": "ok",
            "job": "email.poll",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result": {"paused": True},
        }

    def _run() -> dict[str, Any]:
        from entities.email_message.business.service import MailboxPollService
        svc = MailboxPollService()
        inbox_result = svc.poll_invoice_inbox(top=50)
        sent_result = svc.poll_invoice_sent(top=50)
        reconciled = 0
        # Skip reconcile if either stage hit Graph errors — we'd be
        # operating on stale Sent state and could mis-bind reviews.
        if inbox_result.get("status_code") == 200 and sent_result.get("status_code") == 200:
            try:
                reconciled = svc.reconcile_review_email_message_links()
            except Exception as e:
                # Reconcile failure is recoverable on the next tick;
                # don't fail the whole poll over it.
                logger.exception("email.poll.reconcile_failed: %s", e)
        return {"inbox": inbox_result, "sent": sent_result, "reconciled_reviews": reconciled}

    envelope = await _timed("email.poll", _run)
    inner = envelope.get("result") or {}
    inbox = inner.get("inbox") if isinstance(inner, dict) else None
    sent = inner.get("sent") if isinstance(inner, dict) else None
    inbox_status = inbox.get("status_code") if isinstance(inbox, dict) else None
    sent_status = sent.get("status_code") if isinstance(sent, dict) else None
    bad = []
    if isinstance(inbox_status, int) and inbox_status >= 400:
        bad.append(("inbox", inbox))
    if isinstance(sent_status, int) and sent_status >= 400:
        bad.append(("sent", sent))
    if bad:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "job": "email.poll",
                "duration_ms": envelope.get("duration_ms"),
                "failures": [
                    {
                        "stage": stage,
                        "upstream_status": part.get("status_code"),
                        "message": part.get("message"),
                        "errors": part.get("errors") or [],
                    }
                    for stage, part in bad
                ],
            },
        )
    return envelope


@router.post("/email/extract/{attachment_public_id}", dependencies=[Depends(_require_drain_secret)])
async def email_extract_router(attachment_public_id: str = Path(...)):
    """
    Run Document Intelligence against a single EmailAttachment by
    public_id. Used for verification today; the email agent calls the
    underlying service directly in Phase 2.
    """
    def _run() -> dict[str, Any]:
        from entities.email_message.business.service import EmailAttachmentExtractionService
        return EmailAttachmentExtractionService().extract_by_public_id(attachment_public_id)

    return await _timed("email.extract", _run)


@router.post("/email/process_one", dependencies=[Depends(_require_drain_secret)])
async def email_process_one_router():
    """
    Claim the oldest pending EmailMessage and kick off an
    `email_specialist` agent run on it. Returns immediately — the agent
    runs in the background and stamps the outcome via mark_email_outcome
    when it finishes.

    Returns `{processed: false}` when the queue is empty so the
    scheduler's inner-drain loop knows to stop. Idempotent — the
    underlying claim sproc uses UPDLOCK + READPAST so concurrent ticks
    can't claim the same row.

    Operator pause: set env var `PAUSE_EMAIL_AGENT=true` on App Service
    and restart. While set, this endpoint returns `{processed: false,
    paused: true}` immediately — the Function App tick keeps firing
    harmlessly (50ms HTTP round-trip) and the scheduler's inner-drain
    loop exits cleanly. Avoids touching the Function App's Disabled
    flag, which is a one-way trip on Flex Consumption (see
    docs/runbooks/deploy-restart-timing.md). Reverse by clearing the
    env var + restart.
    """
    import asyncio as _asyncio

    started = time.monotonic()

    pause_flag = (os.environ.get("PAUSE_EMAIL_AGENT") or "").strip().lower()
    if pause_flag in ("true", "1", "yes"):
        return {
            "status": "ok",
            "job": "email.process_one",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result": {"processed": False, "paused": True},
        }

    # Lazy imports — avoid pulling the agent registry into the FastAPI
    # boot path unnecessarily.
    from entities.email_message.business.service import EmailMessageService
    from intelligence.api.background import start_run
    from shared.database import get_connection

    # 1. Atomically claim the next pending email.
    service = EmailMessageService()
    email = await _asyncio.to_thread(service.claim_next_pending)
    if not email:
        return {
            "status": "ok",
            "job": "email.process_one",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result": {"processed": False},
        }

    # 2. Resolve the email_agent User.Id so the AgentSession is owned by
    #    the correct agent identity.
    def _resolve_email_agent_user_id() -> Optional[int]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT u.Id FROM dbo.[User] u "
                "JOIN dbo.Auth a ON a.UserId = u.Id "
                "WHERE a.Username = 'email_agent'"
            )
            row = cur.fetchone()
            return row.Id if row else None

    email_agent_user_id = await _asyncio.to_thread(_resolve_email_agent_user_id)
    if email_agent_user_id is None:
        # Roll the email back to 'pending' so the next tick can retry.
        await _asyncio.to_thread(
            service.update_status,
            id=email.id,
            processing_status="pending",
            last_error="email_agent user not provisioned",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="email_agent user not provisioned — run seed.email_agent.sql",
        )

    # 3. Build the user_message and kick off the run. The agent's prompt
    #    teaches it to take the EmailMessage public_id from this seed
    #    message and drive the rest from there.
    user_message = (
        f"Process polled EmailMessage public_id={email.public_id}\n\n"
        f"From: {email.from_address}\n"
        f"Subject: {email.subject!r}\n"
        f"Has attachments: {email.has_attachments}\n"
    )

    session_public_id = await start_run(
        agent_name="email_specialist",
        user_message=user_message,
        requesting_user_id=email_agent_user_id,
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "admin.email.process_one.kicked_off email=%s session=%s duration_ms=%d",
        email.public_id, session_public_id, duration_ms,
    )

    return {
        "status": "ok",
        "job": "email.process_one",
        "duration_ms": duration_ms,
        "result": {
            "processed": True,
            "email_public_id": email.public_id,
            "agent_session_public_id": session_public_id,
        },
    }


# --- Time Tracking agent ---------------------------------------------------- #


@router.post("/time-tracking/process_one", dependencies=[Depends(_require_drain_secret)])
async def time_tracking_process_one_router():
    """
    Claim the oldest pending TimeTrackingOutbox row and kick off a
    `time_tracking_specialist` agent run. Returns immediately — the agent
    runs in the background and stamps ReviewPriority + ReviewReasons on
    the TimeEntry via flag_time_entry_for_human_review when it finishes.

    Returns `{processed: false}` when the queue is empty so the scheduler's
    inner-drain loop knows to stop. Idempotent — `ClaimNextPendingTimeTrackingOutbox`
    uses UPDLOCK + READPAST so concurrent ticks can't claim the same row.

    Operator pause: set env var `PAUSE_TIME_TRACKING_AGENT=true` on App Service
    and restart. While set, this endpoint returns `{processed: false, paused: true}`
    immediately. The submit-path keeps enqueueing rows; they accumulate as
    `pending` until the pause is lifted, then drain in submission order.
    No iOS data is lost.
    """
    import asyncio as _asyncio
    from datetime import datetime, timedelta, timezone

    started = time.monotonic()

    pause_flag = (os.environ.get("PAUSE_TIME_TRACKING_AGENT") or "").strip().lower()
    if pause_flag in ("true", "1", "yes"):
        return {
            "status": "ok",
            "job": "time_tracking.process_one",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result": {"processed": False, "paused": True},
        }

    # Lazy imports — keep the agent registry off the FastAPI boot path.
    from intelligence.outbox.business.service import TimeTrackingOutboxService
    from intelligence.api.background import start_run
    from shared.database import get_connection

    # 1. Atomically claim the next pending row.
    svc = TimeTrackingOutboxService()
    claimed = await _asyncio.to_thread(svc.claim_next_pending)
    if not claimed:
        return {
            "status": "ok",
            "job": "time_tracking.process_one",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "result": {"processed": False},
        }

    # 2. Resolve the time_tracking_agent User.Id so the AgentSession is
    #    owned by the correct agent identity.
    def _resolve_agent_user_id() -> Optional[int]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT u.Id FROM dbo.[User] u "
                "JOIN dbo.Auth a ON a.UserId = u.Id "
                "WHERE a.Username = 'time_tracking_agent'"
            )
            row = cur.fetchone()
            return row.Id if row else None

    agent_user_id = await _asyncio.to_thread(_resolve_agent_user_id)
    if agent_user_id is None:
        # Failback the row so the next tick can retry once the seed is run.
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=60)
        await _asyncio.to_thread(
            svc.mark_failed,
            id=claimed.id,
            row_version=claimed.row_version,
            next_retry_at=next_retry,
            last_error="time_tracking_agent user not provisioned",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="time_tracking_agent user not provisioned — run seed.time_tracking_agent.sql",
        )

    # 3. Build the user_message and kick off the run. The prompt teaches
    #    the agent to take the public_id from this seed message, validate,
    #    map to a ReviewPriority bucket, and stamp.
    user_message = (
        "Review iOS-submitted TimeEntry for completeness.\n\n"
        f"**TimeEntry public_id**: {claimed.entity_public_id}\n"
    )

    try:
        session_public_id = await start_run(
            agent_name="time_tracking_specialist",
            user_message=user_message,
            requesting_user_id=agent_user_id,
        )
    except Exception as error:
        # start_run failed (e.g. AgentSession insert blocked, registry miss,
        # Anthropic key missing). Failback the row so the next tick retries.
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=60)
        await _asyncio.to_thread(
            svc.mark_failed,
            id=claimed.id,
            row_version=claimed.row_version,
            next_retry_at=next_retry,
            last_error=f"start_run failed: {str(error)[:400]}",
        )
        logger.exception(
            "admin.time_tracking.process_one.start_run_failed outbox_id=%s entity=%s",
            claimed.id, claimed.entity_public_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"start_run failed: {error}",
        )

    # 4. start_run returned successfully — the AgentSession row exists and
    #    the loop is dispatched. From the outbox's perspective, "kicked off"
    #    is the success state; the agent's own outcome (flag stamped /
    #    failed mid-run) is tracked on AgentSession + TimeEntry.ReviewPriority,
    #    not here.
    await _asyncio.to_thread(
        svc.mark_done,
        id=claimed.id,
        row_version=claimed.row_version,
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "admin.time_tracking.process_one.kicked_off outbox_id=%s entity=%s session=%s duration_ms=%d",
        claimed.id, claimed.entity_public_id, session_public_id, duration_ms,
    )

    return {
        "status": "ok",
        "job": "time_tracking.process_one",
        "duration_ms": duration_ms,
        "result": {
            "processed": True,
            "time_entry_public_id": claimed.entity_public_id,
            "outbox_public_id": claimed.public_id,
            "agent_session_public_id": session_public_id,
        },
    }


# --- Email recovery: stuck-row + long-running session sweep --------------- #


@router.post("/email/recover_stuck", dependencies=[Depends(_require_drain_secret)])
async def email_recover_stuck_router():
    """
    Sweep for orphaned EmailMessage rows + long-running AgentSessions and
    reset / dead-letter them so the queue keeps moving. Called on a 5-min
    cadence by the scheduler Function App.

    Two failure modes covered:
    1. EmailMessage stuck in 'processing' with AgentSessionId IS NULL —
       the claim sproc commits before the AgentSession row is inserted,
       so any crash between those two steps orphans the email row.
    2. AgentSession stuck in 'running' (e.g. worker recycled mid-run);
       its linked EmailMessage is also reset back to 'pending' so the
       next process_one tick can re-attempt.

    Each row carries a ProcessingResetCount; once it hits MaxResets the
    row dead-letters to 'failed' instead of looping forever.
    """
    def _run() -> dict[str, Any]:
        from entities.email_message.business.service import EmailMessageService
        from intelligence.persistence.session_repo import AgentSessionRepo

        em_result = EmailMessageService().recover_stuck_processing(
            stale_after_minutes=10, max_resets=3
        )
        ag_result = AgentSessionRepo().timeout_long_running(
            stale_after_minutes=30, max_email_resets=3
        )
        return {**em_result, **ag_result}

    return await _timed("email.recover_stuck", _run)


# --- Bill folder processing -- one file per tick --------------------------- #


@router.post("/bill-folder/tick", dependencies=[Depends(_require_drain_secret)])
async def bill_folder_tick_router():
    """
    Process one queued BillFolderRunItem. Called on a cadence by the
    scheduler Function App. Returns {"processed": false} when the queue
    is empty so the caller can stop looping.

    Bounded work — one file per call — so Azure App Service's idle
    timeout and transient MS Graph failures only affect that single
    file. Per-file errors go on the item row; the parent run keeps
    draining the rest.
    """
    def _run() -> dict[str, Any]:
        from entities.bill.business.folder_processor import BillFolderProcessor
        from entities.bill.persistence.folder_run_repo import BillFolderRunItemRepository

        item_repo = BillFolderRunItemRepository()

        # Sweep stale items + runs before claiming. Cheap (indexed) and
        # keeps the UI from hanging on abandoned runs after a bad deploy.
        try:
            item_repo.auto_fail_stale(stale_after_minutes=30)
        except Exception:
            logger.exception("auto_fail_stale swallowed")

        item = item_repo.claim_next(reclaim_after_seconds=180, max_attempts=3)
        if item is None:
            return {"processed": False}

        logger.info(
            "bill_folder.tick.claimed item=%s filename=%s attempts=%d",
            item.public_id, item.filename, item.attempts,
        )

        try:
            outcome = BillFolderProcessor().process_single_item(
                filename=item.filename,
                item_id=item.item_id,
                company_id=1,
                tenant_id=1,
            )
        except Exception as error:
            # Transient failure — return to queue or dead-letter.
            logger.exception("bill_folder.tick.failed item=%s", item.public_id)
            item_repo.mark_failure(
                public_id=item.public_id,
                last_error=f"{type(error).__name__}: {error}",
                max_attempts=3,
            )
            item_repo.check_and_complete_run(run_id=item.run_id)
            return {
                "processed": True,
                "run_id": item.run_id,
                "filename": item.filename,
                "item_status": "retry",
            }

        # Permanent-terminal path: processor returned a result dict.
        terminal_status = outcome.get("status", "skipped")
        item_repo.mark_success(
            public_id=item.public_id,
            status=terminal_status,
            result=outcome,
        )
        item_repo.check_and_complete_run(run_id=item.run_id)
        return {
            "processed": True,
            "run_id": item.run_id,
            "filename": item.filename,
            "item_status": terminal_status,
        }

    return await _timed("bill_folder.tick", _run)


@router.post("/bill-folder/enumerate", dependencies=[Depends(_require_drain_secret)])
async def bill_folder_enumerate_router():
    """
    Scan the SharePoint source folder and enqueue any new PDFs as run
    items. Called on a 5-min cadence by the scheduler Function App so
    files dropped into the folder get picked up without anyone clicking
    the React 'Process Folder' button.

    Skips files that are already in 'queued' or 'processing' so a
    button-triggered run + scheduled tick can't double-queue the same
    PDF. Returns {"status": "noop"} when nothing new is found (no run
    row created).
    """
    def _run() -> dict[str, Any]:
        from entities.bill.business.folder_processor import (
            BillFolderEnumerationError,
            enqueue_bill_folder_run,
        )
        try:
            return enqueue_bill_folder_run(dedup_active=True)
        except BillFolderEnumerationError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to enumerate source folder: {error}",
            )

    return await _timed("bill_folder.enumerate", _run)
