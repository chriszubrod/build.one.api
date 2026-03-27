# Python Standard Library Imports
import asyncio
import logging

logger = logging.getLogger(__name__)

_scheduler_task = None
_interval_minutes = 30


async def _run_scheduled():
    """Background loop that runs BillAgent at fixed intervals."""
    while True:
        await asyncio.sleep(_interval_minutes * 60)

        # --- Bill processing (run in thread to avoid blocking the event loop) ---
        try:
            from core.ai.agents.bill_agent.business.runner import run_bill_folder_processing
            result = await asyncio.to_thread(
                run_bill_folder_processing,
                company_id=1,
                tenant_id=1,
                user_id="scheduler",
                trigger_source="scheduler",
            )
            if result.get("success"):
                logger.info(
                    "Scheduled bill processing: %d/%d files, %d bills created",
                    result.get("files_processed", 0),
                    result.get("files_found", 0),
                    result.get("bills_created", 0),
                )
            else:
                logger.error("Scheduled bill processing failed: %s", result.get("error"))
        except Exception as e:
            logger.error("Scheduled bill folder processing failed: %s", e)


def start_scheduler():
    """Start the bill agent scheduler as a background asyncio task."""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_run_scheduled())
    logger.info("BillAgent scheduler started (interval: %d min)", _interval_minutes)


def stop_scheduler():
    """Stop the bill agent scheduler."""
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("BillAgent scheduler stopped")
