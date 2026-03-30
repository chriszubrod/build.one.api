"""
core/notifications/sla_scheduler.py
=====================================
APScheduler-style SLA breach detection and notification.

Follows the same pattern as core/ai/agents/bill_agent/scheduler.py:
- Module-level asyncio task
- Infinite while loop with sleep-first
- Sync DB work runs in asyncio.to_thread()
- Async notifications run directly in the loop

Checks every 30 minutes for EmailThreads that have been in a
requires-action stage longer than their SLA threshold. When a breach
is found, it:
  1. Fires a push notification to the thread owner
  2. Writes an EmailThreadStageHistory record with triggered_by=SLA_BREACH

SLA thresholds are defined in email_processes.json and entity_processes.json.
The DB sproc ReadEmailThreadsExceedingStageDuration handles the query.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None
_interval_minutes = 30


async def _run_scheduled() -> None:
    """
    Main scheduler loop. Sleeps first so first execution is
    _interval_minutes after startup — consistent with bill agent pattern.
    """
    while True:
        await asyncio.sleep(_interval_minutes * 60)
        try:
            await _check_sla_breaches()
        except Exception as error:
            logger.error(f"SLA scheduler: unhandled error: {error}")


async def _check_sla_breaches() -> None:
    """
    Check all registered SLA thresholds and fire notifications
    for any threads that have exceeded them.
    """
    from core.workflow.business.process_registry import (
        get_all_email_process_types,
        get_sla_for_stage,
        get_email_process,
    )

    logger.info("SLA scheduler: checking for breached threads...")

    # Build a map of { max_hours — breach_results } by querying the sproc
    # for the most restrictive threshold across all processes.
    # The sproc returns all threads exceeding @MaxHours — we pass the
    # minimum configured threshold to catch everything, then filter by
    # process-specific thresholds in Python.

    all_sla_hours = _collect_all_sla_hours()
    if not all_sla_hours:
        logger.debug("SLA scheduler: no SLA thresholds configured.")
        return

    min_hours = min(all_sla_hours)

    breached = await asyncio.to_thread(_fetch_breached_threads, min_hours)
    if not breached:
        logger.debug("SLA scheduler: no breached threads found.")
        return

    logger.info(f"SLA scheduler: found {len(breached)} potentially breached thread(s).")

    for row in breached:
        try:
            await _handle_breach(row)
        except Exception as error:
            thread_id = row.get("PublicId", "unknown")
            logger.error(f"SLA scheduler: error handling breach for {thread_id}: {error}")


def _collect_all_sla_hours() -> list[int]:
    """Collect all unique max_hours values from the process registry."""
    from core.workflow.business.process_registry import (
        get_all_email_process_types,
        get_email_process,
    )

    hours = []
    for process_type in get_all_email_process_types():
        try:
            definition = get_email_process(process_type)
            for stage_sla in definition.get("sla", {}).values():
                if isinstance(stage_sla, dict) and "max_hours" in stage_sla:
                    hours.append(int(stage_sla["max_hours"]))
        except Exception:
            pass
    return hours


def _fetch_breached_threads(min_hours: int) -> list[dict]:
    """Sync DB call — runs in asyncio.to_thread()."""
    try:
        from entities.email_thread.persistence.stage_history_repo import (
            EmailThreadStageHistoryRepository,
        )
        repo = EmailThreadStageHistoryRepository()
        return repo.read_threads_exceeding_sla(max_hours=min_hours)
    except Exception as error:
        logger.error(f"SLA scheduler: DB query failed: {error}")
        return []


async def _handle_breach(row: dict) -> None:
    """
    Handle a single breached thread:
    1. Verify this thread's process + stage actually has an SLA that's breached
    2. Fire push notification to owner
    3. Write stage history record
    """
    from core.workflow.business.process_registry import get_sla_for_stage
    from core.notifications.push_service import notify_sla_breach

    thread_public_id  = str(row.get("PublicId", ""))
    process_type      = str(row.get("ProcessType", ""))
    stalled_at_stage  = str(row.get("StalledAtStage", ""))
    hours_in_stage    = int(row.get("HoursInStage", 0))
    owner_user_id     = row.get("OwnerUserId")
    thread_id         = row.get("Id")

    # Verify this stage actually has an SLA breach at this hour count
    sla = get_sla_for_stage(process_type, stalled_at_stage, registry_type="email")
    if not sla:
        # Try entity registry
        sla = get_sla_for_stage(process_type, stalled_at_stage, registry_type="entity")

    if not sla:
        return  # No SLA defined for this stage — skip

    max_hours = int(sla.get("max_hours", 0))
    if hours_in_stage <= max_hours:
        return  # Not actually breached for this specific SLA

    logger.warning(
        f"SLA breach: thread {thread_public_id} has been in "
        f"'{stalled_at_stage}' for {hours_in_stage}h "
        f"(max: {max_hours}h)"
    )

    # Write stage history record
    await asyncio.to_thread(
        _write_breach_history,
        thread_id=thread_id,
        thread_public_id=thread_public_id,
        stalled_at_stage=stalled_at_stage,
        hours_in_stage=hours_in_stage,
    )

    # Fire push notification to owner (if assigned)
    if owner_user_id:
        await notify_sla_breach(
            user_id=int(owner_user_id),
            thread_id=thread_public_id,
            stage=stalled_at_stage,
            hours=hours_in_stage,
        )


def _write_breach_history(
    thread_id:          int,
    thread_public_id:   str,
    stalled_at_stage:   str,
    hours_in_stage:     int,
) -> None:
    """Write SLA_BREACH stage history record. Sync — runs in asyncio.to_thread()."""
    try:
        from core.workflow.api.process_engine import EventType
        from entities.email_thread.persistence.stage_history_repo import (
            EmailThreadStageHistoryRepository,
        )

        repo = EmailThreadStageHistoryRepository()
        repo.create(
            public_id=          str(uuid.uuid4()),
            email_thread_id=    thread_id,
            from_stage=         stalled_at_stage,
            to_stage=           stalled_at_stage,  # stage doesn't change — breach is a signal
            triggered_by=       EventType.SLA_BREACH.value,
            notes=(
                f"SLA breach: thread has been in '{stalled_at_stage}' "
                f"for {hours_in_stage} hours."
            ),
        )
    except Exception as error:
        logger.error(f"SLA scheduler: failed to write breach history: {error}")


# ---------------------------------------------------------------------------
# Public API — mirrors bill_agent scheduler
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Start the SLA breach scheduler as a background asyncio task."""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_run_scheduled())
    logger.info(
        f"SLA breach scheduler started — "
        f"interval: {_interval_minutes} minutes"
    )


def stop_scheduler() -> None:
    """Cancel the SLA breach scheduler task on app shutdown."""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("SLA breach scheduler stopped.")
