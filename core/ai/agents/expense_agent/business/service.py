# Python Standard Library Imports
import json
import logging
from typing import Optional

# Local Imports
from core.ai.agents.expense_agent.business.models import ExpenseAgentRun, ProcessingResult
from core.ai.agents.expense_agent.persistence.repo import ExpenseAgentRunRepository

logger = logging.getLogger(__name__)


class ExpenseAgentService:
    """Manages ExpenseAgentRun lifecycle — start, complete, fail, query."""

    def __init__(self, repo: Optional[ExpenseAgentRunRepository] = None):
        self.repo = repo or ExpenseAgentRunRepository()

    def start_run(
        self,
        trigger_source: str = "manual",
        created_by: Optional[str] = None,
    ) -> ExpenseAgentRun:
        """Create a new run record in 'running' state."""
        run = self.repo.create(
            trigger_source=trigger_source,
            created_by=created_by,
        )
        logger.info("ExpenseAgent run started: %s (trigger=%s)", run.public_id, trigger_source)
        return run

    def complete_run(
        self,
        public_id: str,
        result: ProcessingResult,
    ) -> Optional[ExpenseAgentRun]:
        """Mark a run as completed with processing metrics."""
        summary = json.dumps({
            "files_found": result.files_found,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "expenses_created": result.expenses_created,
            "errors": result.errors,
        })
        run = self.repo.complete(
            public_id,
            files_found=result.files_found,
            files_processed=result.files_processed,
            files_skipped=result.files_skipped,
            expenses_created=result.expenses_created,
            error_count=result.error_count,
            summary=summary,
        )
        logger.info(
            "ExpenseAgent run completed: %s — %d/%d files processed, %d expenses created",
            public_id, result.files_processed, result.files_found, result.expenses_created,
        )
        return run

    def fail_run(
        self,
        public_id: str,
        error: str,
    ) -> Optional[ExpenseAgentRun]:
        """Mark a run as failed."""
        run = self.repo.fail(public_id, summary=json.dumps({"error": error}))
        logger.error("ExpenseAgent run failed: %s — %s", public_id, error)
        return run

    def update_progress(
        self,
        public_id: str,
        result: ProcessingResult,
    ) -> None:
        """Update intermediate progress on a running run."""
        self.repo.update_progress(
            public_id,
            files_found=result.files_found,
            files_processed=result.files_processed,
            files_skipped=result.files_skipped,
            expenses_created=result.expenses_created,
            error_count=result.error_count,
        )

    def get_run(self, public_id: str) -> Optional[ExpenseAgentRun]:
        """Get a run by public ID."""
        return self.repo.read_by_public_id(public_id)

    def get_recent_runs(self, limit: int = 20) -> list[ExpenseAgentRun]:
        """Get recent runs."""
        return self.repo.read_recent(limit=limit)
