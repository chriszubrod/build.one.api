# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Dict, List, Optional

# Local Imports
from services.tasks.business.model import Task
from services.tasks.persistence.repo import TaskRepository

logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATES = {"completed", "abandoned", "cancelled"}


class TaskService:
    """
    Service for task lifecycle and list/detail.
    Tasks are a generic hub for all user work items - email workflows, data uploads, manual entry.
    """

    def __init__(self):
        self.task_repo = TaskRepository()

    # =========================================================================
    # Generic CRUD Methods
    # =========================================================================

    def create_task(
        self,
        *,
        tenant_id: int,
        task_type: str,
        reference_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        bill_id: Optional[int] = None,
        context: Optional[dict] = None,
    ) -> Task:
        """Create a new task (manual or from any source)."""
        return self.task_repo.create(
            tenant_id=tenant_id,
            task_type=task_type,
            reference_id=reference_id,
            title=title,
            description=description,
            status=status or "new",
            source_type=source_type,
            source_id=source_id,
            created_by_user_id=created_by_user_id,
            workflow_id=workflow_id,
            vendor_id=vendor_id,
            project_id=project_id,
            bill_id=bill_id,
            context=context,
        )

    def update_task(
        self,
        public_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        context: Optional[dict] = None,
        bill_id: Optional[int] = None,
    ) -> Optional[Task]:
        """Update a task's fields."""
        return self.task_repo.update(
            public_id=public_id,
            title=title,
            description=description,
            status=status,
            context=context,
            bill_id=bill_id,
        )

    def update_status(self, public_id: str, status: str) -> Optional[Task]:
        """Quick status-only update."""
        return self.task_repo.update(public_id=public_id, status=status)

    def set_task_bill_for_parent_workflow(
        self,
        parent_workflow_public_id: str,
        bill_id: int,
        bill_public_id: str,
    ) -> Optional[Task]:
        """Link the task for the given (email_intake) workflow to the created bill."""
        try:
            from workflows.persistence.repo import WorkflowRepository

            wf_repo = WorkflowRepository()
            parent = wf_repo.read_by_public_id(parent_workflow_public_id)
            if not parent:
                logger.warning("Parent workflow not found: %s", parent_workflow_public_id)
                return None
            task = self.task_repo.read_by_workflow_id(parent.id)
            if not task:
                logger.warning("No task for workflow %s", parent_workflow_public_id)
                return None
            merged = dict(task.context or {})
            merged["bill_public_id"] = bill_public_id
            return self.task_repo.update(
                public_id=task.public_id,
                bill_id=bill_id,
                context=merged,
            )
        except Exception as e:
            logger.warning("Failed to set task bill for parent workflow %s: %s", parent_workflow_public_id, e)
            return None

    def get_tasks(
        self,
        tenant_id: int,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        open_only: bool = True,
    ) -> List[Task]:
        """List tasks with optional filters."""
        tasks = self.task_repo.read_tasks(
            tenant_id=tenant_id,
            status=status,
            source_type=source_type,
            source_id=source_id,
            open_only=open_only,
        )
        # Apply task_type filter if provided (not supported at DB level yet)
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        return tasks

    def get_task_detail(self, public_id: str) -> Optional[Dict]:
        """
        Load task by public_id and resolve type-specific detail.
        Returns dict with task + type-specific data (e.g. workflow, upload).
        """
        task = self.task_repo.read_by_public_id(public_id)
        if not task:
            return None

        result = {"task": task.to_dict()}

        # Resolve type-specific detail
        if task.task_type == "workflow" and task.workflow_id:
            workflow_detail = self._resolve_workflow_detail(task)
            if workflow_detail:
                result["workflow"] = workflow_detail.get("workflow")
                result["events"] = workflow_detail.get("events", [])
                result["summary"] = workflow_detail.get("summary")
        elif task.task_type == "data_upload":
            upload_detail = self._resolve_upload_detail(task)
            if upload_detail:
                result["upload"] = upload_detail

        return result

    # =========================================================================
    # Type-Specific Resolvers (private)
    # =========================================================================

    def _resolve_workflow_detail(self, task: Task) -> Optional[Dict]:
        """
        Fetch workflow + events for a workflow-type task.
        Uses lazy import to avoid hard dependency on workflows module.
        """
        if not task.workflow_id:
            return None

        try:
            # Lazy import
            from workflows.admin import WorkflowAdmin
            from workflows.persistence.repo import WorkflowRepository

            workflow_repo = WorkflowRepository()
            workflow = workflow_repo.read_by_id(task.workflow_id)
            if not workflow:
                return None

            workflow_admin = WorkflowAdmin()
            detail = workflow_admin.get_workflow_with_events(str(workflow.public_id))
            return detail
        except Exception as e:
            logger.warning("Failed to resolve workflow detail for task %s: %s", task.public_id, e)
            return None

    def _resolve_upload_detail(self, task: Task) -> Optional[Dict]:
        """
        Fetch upload-specific detail (file info, parse status, preview).
        For now, just return context data.
        """
        if not task.context:
            return None
        # Upload detail is stored in task.context
        return task.context

    # =========================================================================
    # Workflow Bridge Methods (for backward compatibility)
    # =========================================================================

    def create_from_workflow(self, tenant_id: int, workflow) -> Optional[Task]:
        """
        Create a Task from a Workflow (called by workflow executor).
        Uses lazy import to avoid hard dependency.
        """
        if not workflow or not workflow.id:
            return None

        try:
            title = self._workflow_title(workflow)
            status = workflow.state or "new"

            # Extract source info from workflow context
            ctx = workflow.context or {}
            email = ctx.get("email") or {}
            conversation_id = email.get("conversation_id")

            # Extract vendor/bill info if available
            vendor_match = ctx.get("vendor_match") or {}
            vendor = vendor_match.get("vendor") or {}
            vendor_id = vendor.get("id")

            return self.task_repo.create(
                tenant_id=tenant_id,
                task_type="workflow",
                reference_id=workflow.id,
                title=title,
                status=status,
                source_type="email" if conversation_id else None,
                source_id=conversation_id,
                workflow_id=workflow.id,
                vendor_id=vendor_id,
            )
        except Exception as e:
            logger.warning("Failed to create task from workflow %s: %s", workflow.id, e)
            return None

    def sync_status_from_workflow(self, workflow_id: int, workflow_state: str) -> Optional[Task]:
        """
        Update task status when workflow state changes.
        Uses lazy import to avoid hard dependency.
        """
        try:
            task = self.task_repo.read_by_workflow_id(workflow_id)
            if not task:
                return None

            # Map workflow state to task status
            task_status = self._map_workflow_state_to_task_status(workflow_state)

            return self.task_repo.update(
                public_id=task.public_id,
                status=task_status,
            )
        except Exception as e:
            logger.warning("Failed to sync task status from workflow %s: %s", workflow_id, e)
            return None

    def _map_workflow_state_to_task_status(self, workflow_state: str) -> str:
        """Map workflow states to task statuses."""
        if workflow_state in TERMINAL_WORKFLOW_STATES:
            if workflow_state in ("abandoned", "cancelled"):
                return "cancelled"
            return "completed"
        if workflow_state in ("needs_review", "awaiting_confirmation"):
            return "needs_review"
        if workflow_state == "awaiting_approval":
            return "pending_approval"
        return "in_progress"

    def _workflow_title(self, workflow) -> Optional[str]:
        """Derive a display title from workflow (e.g. email subject)."""
        ctx = workflow.context or {}
        email = ctx.get("email") or {}
        subject = email.get("subject")
        if subject:
            return subject[:500] if len(subject) > 500 else subject

        classification = ctx.get("classification") or {}
        vendor_match = ctx.get("vendor_match") or {}
        vendor_name = (vendor_match.get("vendor") or {}).get("name")
        if vendor_name:
            return f"{vendor_name}"[:500]

        return workflow.state or "Workflow"

    # =========================================================================
    # Legacy Methods (for backward compatibility with existing code)
    # =========================================================================

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string from various formats."""
        if not dt_str:
            return None
        try:
            if "T" in dt_str:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00").split("+")[0])
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    def get_tasks_for_list(
        self,
        tenant_id: int,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        open_only: bool = True,
    ) -> List[Dict]:
        """
        Return list of task dicts for the list page, optionally enriched with workflow summary.
        This is a legacy method that's still used by the web controller.
        """
        tasks = self.task_repo.read_tasks(
            tenant_id=tenant_id,
            status=status,
            source_type=source_type,
            source_id=source_id,
            open_only=open_only,
        )

        result = []
        for task in tasks:
            item = task.to_dict()

            # Enrich workflow tasks with workflow detail
            if task.task_type == "workflow" and task.workflow_id:
                workflow_enrichment = self._enrich_with_workflow_summary(task)
                if workflow_enrichment:
                    item.update(workflow_enrichment)
                else:
                    # Fallback if workflow not found
                    item["subject"] = task.title or "No subject"
                    item["state"] = task.status
                    item["needs_attention"] = task.status == "needs_review"
                    item["days_ago"] = None
            else:
                # Non-workflow tasks
                item["subject"] = task.title or "No subject"
                item["state"] = task.status
                item["needs_attention"] = task.status == "needs_review"
                item["days_ago"] = None

            # When task is linked to a bill, add bill_public_id and bill_status for list link/display
            if task.bill_id:
                bill_enrichment = self._enrich_with_bill(task)
                if bill_enrichment:
                    item.update(bill_enrichment)
            elif (task.context or {}).get("bill_public_id"):
                item["bill_public_id"] = task.context.get("bill_public_id")

            result.append(item)
        return result

    def _enrich_with_workflow_summary(self, task: Task) -> Optional[Dict]:
        """
        Enrich task with workflow summary for list display.
        Uses lazy import to avoid hard dependency.
        """
        if not task.workflow_id:
            return None

        try:
            # Lazy import
            from workflows.admin import WorkflowAdmin
            from workflows.persistence.repo import WorkflowRepository

            workflow_repo = WorkflowRepository()
            workflow = workflow_repo.read_by_id(task.workflow_id)
            if not workflow:
                return None

            workflow_admin = WorkflowAdmin()
            summary = workflow_admin._format_workflow_summary(workflow)

            ctx = workflow.context or {}
            email = ctx.get("email") or {}
            classification = ctx.get("classification") or {}
            vendor_match = ctx.get("vendor_match") or {}

            enrichment = {
                "subject": task.title or email.get("subject") or "No subject",
                "from_name": email.get("from_name"),
                "from_address": email.get("from_address"),
                "entity_label": (vendor_match.get("vendor") or {}).get("name") or workflow.workflow_type,
                "confidence": classification.get("confidence"),
                "state": workflow.state,
                "has_attachments": bool(
                    ctx.get("total_attachments") or ctx.get("all_conversation_attachments")
                ),
                "needs_attention": workflow.state == "needs_review",
                "workflow_public_id": workflow.public_id,
            }

            created = self._parse_datetime(workflow.created_datetime)
            enrichment["days_ago"] = (datetime.utcnow() - created).days if created else None

            return enrichment
        except Exception as e:
            logger.warning("Failed to enrich task %s with workflow summary: %s", task.public_id, e)
            return None

    def _enrich_with_bill(self, task: Task) -> Optional[Dict]:
        """Enrich task with bill public_id and status for list link/display."""
        if not task.bill_id:
            return None
        try:
            from services.bill.business.service import BillService

            bill = BillService().read_by_id(task.bill_id)
            if not bill:
                return None
            return {
                "bill_public_id": bill.public_id,
                "bill_status": "Draft" if bill.is_draft else "Finalized",
            }
        except Exception as e:
            logger.warning("Failed to enrich task %s with bill: %s", task.public_id, e)
            return None

    # =========================================================================
    # Legacy workflow-specific methods (kept for backward compatibility)
    # =========================================================================

    def upsert_task_for_workflow(
        self,
        workflow,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> Optional[Task]:
        """
        Create or update a Task for this workflow (one Task per TaskType + ReferenceId).
        Legacy method - kept for backward compatibility with existing workflow code.
        """
        print(f"[TaskService] upsert_task_for_workflow workflow.id={workflow.id} workflow.tenant_id={workflow.tenant_id} workflow.state={workflow.state}")
        if not workflow.id or not workflow.tenant_id:
            print(f"[TaskService] upsert_task_for_workflow skipping: missing workflow.id or workflow.tenant_id")
            return None

        if workflow.state in TERMINAL_WORKFLOW_STATES:
            return self.close_task_for_workflow(workflow)

        title = self._workflow_title(workflow)
        status = workflow.state

        existing = self.task_repo.read_by_task_type_and_reference_id(
            tenant_id=workflow.tenant_id,
            task_type="workflow",
            reference_id=workflow.id,
        )

        if existing:
            updated = self.task_repo.update(
                public_id=existing.public_id,
                title=title,
                status=status,
            )
            print(f"[TaskService] upsert_task_for_workflow updated existing task public_id={existing.public_id}")
            return updated

        print(f"[TaskService] upsert_task_for_workflow creating new task tenant_id={workflow.tenant_id} reference_id={workflow.id} title={title[:50] if title else None}...")
        return self.task_repo.create(
            tenant_id=workflow.tenant_id,
            task_type="workflow",
            reference_id=workflow.id,
            title=title,
            status=status,
            source_type=source_type,
            source_id=source_id,
            workflow_id=workflow.id,
        )

    def close_task_for_workflow(self, workflow) -> Optional[Task]:
        """
        Update the Task for this workflow to completed/cancelled when workflow reaches terminal state.
        Legacy method - kept for backward compatibility with existing workflow code.
        """
        if not workflow.id or not workflow.tenant_id:
            return None

        existing = self.task_repo.read_by_task_type_and_reference_id(
            tenant_id=workflow.tenant_id,
            task_type="workflow",
            reference_id=workflow.id,
        )
        if not existing:
            return None

        task_status = "cancelled" if workflow.state in ("abandoned", "cancelled") else "completed"
        return self.task_repo.update(
            public_id=existing.public_id,
            status=task_status,
        )
