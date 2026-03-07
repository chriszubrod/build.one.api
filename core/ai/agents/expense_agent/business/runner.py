# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from core.ai.agents.expense_agent.business.models import ProcessingResult
from core.ai.agents.expense_agent.business.processor import ExpenseFolderProcessor
from core.ai.agents.expense_agent.business.service import ExpenseAgentService

logger = logging.getLogger(__name__)


def run_expense_folder_processing(
    company_id: int,
    tenant_id: int = 1,
    user_id: Optional[str] = None,
    trigger_source: str = "manual",
) -> dict:
    """
    Top-level entry point for expense folder processing.
    Wraps the processor with run tracking (start, complete/fail).

    Args:
        company_id: Database ID of the company
        tenant_id: Tenant ID for multi-tenant isolation
        user_id: User or system identifier who triggered the run
        trigger_source: 'manual' or 'scheduler'

    Returns:
        Dict with success flag, run_public_id, and processing metrics
    """
    service = ExpenseAgentService()
    run = service.start_run(trigger_source=trigger_source, created_by=user_id)

    try:
        processor = ExpenseFolderProcessor()
        result = processor.process(company_id=company_id, tenant_id=tenant_id)

        service.complete_run(run.public_id, result)

        return {
            "success": True,
            "run_public_id": run.public_id,
            "files_found": result.files_found,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "expenses_created": result.expenses_created,
            "error_count": result.error_count,
            "errors": result.errors,
        }
    except Exception as e:
        logger.exception("Expense folder processing failed")
        service.fail_run(run.public_id, error=str(e))
        return {
            "success": False,
            "run_public_id": run.public_id,
            "error": str(e),
        }
