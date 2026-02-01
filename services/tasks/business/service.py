# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Dict, List, Optional

# Local Imports
from services.tasks.business.model import Task
from services.tasks.persistence.repo import TaskRepository
from workflows.admin import WorkflowAdmin
from workflows.models import Workflow
from workflows.persistence.repo import WorkflowRepository

logger = logging.getLogger(__name__)

TERMINAL_WORKFLOW_STATES = {"completed", "abandoned", "cancelled"}


class TaskService:
    """Service for task lifecycle and list/detail. Creates/updates Task rows from workflow lifecycle."""

    def __init__(self):
        self.task_repo = TaskRepository()
        self.workflow_repo = WorkflowRepository()
        self.workflow_admin = WorkflowAdmin()

    def _workflow_title(self, workflow: Workflow) -> Optional[str]:
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

    def upsert_task_for_workflow(
        self,
        workflow: Workflow,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> Optional[Task]:
        """
        Create or update a Task for this workflow (one Task per TaskType + ReferenceId).
        Call when a workflow is created or updated and is in a requires-review state.
        """
        if not workflow.id or not workflow.tenant_id:
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
            return updated
        return self.task_repo.create(
            tenant_id=workflow.tenant_id,
            task_type="workflow",
            reference_id=workflow.id,
            title=title,
            status=status,
            source_type=source_type,
            source_id=source_id,
        )

    def close_task_for_workflow(self, workflow: Workflow) -> Optional[Task]:
        """
        Update the Task for this workflow to completed/cancelled when workflow reaches terminal state.
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

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
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
        Return list of task dicts for the list page, optionally enriched with workflow summary (subject, state, etc.).
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
            if task.task_type == "workflow" and task.reference_id:
                workflow = self.workflow_repo.read_by_id(task.reference_id)
                if workflow:
                    summary = self.workflow_admin._format_workflow_summary(workflow)
                    ctx = workflow.context or {}
                    email = ctx.get("email") or {}
                    classification = ctx.get("classification") or {}
                    vendor_match = ctx.get("vendor_match") or {}
                    item["subject"] = task.title or email.get("subject") or "No subject"
                    item["from_name"] = email.get("from_name")
                    item["from_address"] = email.get("from_address")
                    item["entity_label"] = (vendor_match.get("vendor") or {}).get("name") or workflow.workflow_type
                    item["confidence"] = classification.get("confidence")
                    item["state"] = workflow.state
                    item["has_attachments"] = bool(ctx.get("total_attachments") or ctx.get("all_conversation_attachments"))
                    item["needs_attention"] = workflow.state == "needs_review"
                    item["workflow_public_id"] = workflow.public_id
                    created = self._parse_datetime(workflow.created_datetime)
                    item["days_ago"] = (datetime.utcnow() - created).days if created else None
                else:
                    item["subject"] = task.title or "No subject"
                    item["state"] = task.status
                    item["needs_attention"] = task.status == "needs_review"
                    item["days_ago"] = None
            else:
                item["subject"] = task.title or "No subject"
                item["state"] = task.status
                item["needs_attention"] = task.status == "needs_review"
                item["days_ago"] = None
            result.append(item)
        return result

    def get_task_detail(self, public_id: str) -> Optional[Dict]:
        """
        Load task by public_id and resolve underlying entity (e.g. workflow with events).
        Returns dict with task + workflow (and events) when task_type=workflow, or None if not found.
        """
        task = self.task_repo.read_by_public_id(public_id)
        if not task:
            return None
        out = {"task": task.to_dict()}
        if task.task_type == "workflow" and task.reference_id:
            workflow = self.workflow_repo.read_by_id(task.reference_id)
            if workflow:
                detail = self.workflow_admin.get_workflow_with_events(str(workflow.public_id))
                if detail:
                    out["workflow"] = detail.get("workflow")
                    out["events"] = detail.get("events", [])
                    out["summary"] = detail.get("summary")
        return out
