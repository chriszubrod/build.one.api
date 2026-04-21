# Python Standard Library Imports
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Local Imports
from core.workflow.business.models import Workflow, WorkflowEvent
from core.workflow.persistence.repo import WorkflowRepository
from core.workflow_event.persistence.repo import WorkflowEventRepository
from core.workflow.business.orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)


class WorkflowAdmin:
    """
    Administrative utilities for workflow management.
    
    Provides query methods for dashboards, debugging, and audit.
    """
    
    def __init__(self):
        self.workflow_repo = WorkflowRepository()
        self.event_repo = WorkflowEventRepository()
    
    def get_dashboard_stats(self, tenant_id: int) -> Dict:
        """
        Get summary statistics for dashboard.
        
        Args:
            tenant_id: Tenant ID
            
        Returns:
            Dict with dashboard statistics
        """
        now = datetime.utcnow()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        
        # Get workflows created today
        workflows_today = self.workflow_repo.read_created_between(
            tenant_id=tenant_id,
            start=start_of_day,
            end=end_of_day,
        )
        
        # Get workflows completed today
        completed_today = self.workflow_repo.read_completed_between(
            tenant_id=tenant_id,
            start=start_of_day,
            end=end_of_day,
        )
        
        # Get workflows awaiting approval
        awaiting_approval = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state="awaiting_approval",
        )
        
        # Get failed workflows (last 24 hours)
        start_24h = now - timedelta(hours=24)
        failed_workflows = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state="failed",
        )
        # Filter to last 24 hours
        recent_failed = [
            wf for wf in failed_workflows
            if wf.modified_datetime and self._parse_datetime(wf.modified_datetime) >= start_24h
        ]
        
        # Get all active workflows
        active_workflows = self.workflow_repo.read_active_workflows(tenant_id=tenant_id)
        
        # Calculate average completion time (for workflows completed today)
        total_completion_seconds = 0
        completion_count = 0
        for wf in completed_today:
            if wf.created_datetime and wf.completed_datetime:
                created = self._parse_datetime(wf.created_datetime)
                completed = self._parse_datetime(wf.completed_datetime)
                if created and completed:
                    duration = (completed - created).total_seconds()
                    total_completion_seconds += duration
                    completion_count += 1
        
        avg_completion_minutes = (
            (total_completion_seconds / completion_count / 60)
            if completion_count > 0
            else 0
        )
        
        return {
            "workflows_today": len(workflows_today),
            "completed_today": len(completed_today),
            "awaiting_approval": len(awaiting_approval),
            "failed_24h": len(recent_failed),
            "active_workflows": len(active_workflows),
            "avg_completion_minutes": round(avg_completion_minutes, 1),
            "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        }
    
    def get_recent_workflows(
        self,
        tenant_id: int,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Get recent workflows with key details.
        
        Args:
            tenant_id: Tenant ID
            limit: Maximum number of workflows to return
            
        Returns:
            List of workflow dicts with key details
        """
        # Get active workflows first (most relevant)
        active = self.workflow_repo.read_active_workflows(tenant_id=tenant_id)
        
        # Get recently completed (last 7 days) to fill up to limit
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        completed = self.workflow_repo.read_completed_between(
            tenant_id=tenant_id,
            start=week_ago,
            end=now,
        )
        
        # Combine and sort by updated_at descending
        all_workflows = active + completed
        all_workflows.sort(
            key=lambda wf: self._parse_datetime(wf.modified_datetime) or datetime.min,
            reverse=True,
        )
        
        # Take top N
        recent = all_workflows[:limit]
        
        return [self._format_workflow_summary(wf) for wf in recent]
    
    def search_workflows(
        self,
        tenant_id: int,
        q: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Search workflows by vendor name, invoice number, or amount.
        
        Args:
            tenant_id: Tenant ID
            q: Search term - matches vendor name, invoice number, or amount
            state: Optional state filter
            limit: Maximum number of workflows to return
            
        Returns:
            List of workflow dicts matching the search criteria
        """
        # Get active workflows and recent completed (same as get_recent_workflows)
        active = self.workflow_repo.read_active_workflows(tenant_id=tenant_id)
        
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        completed = self.workflow_repo.read_completed_between(
            tenant_id=tenant_id,
            start=week_ago,
            end=now,
        )
        
        all_workflows = active + completed
        
        # Filter by state if provided
        if state:
            all_workflows = [wf for wf in all_workflows if wf.state == state]
        
        # Filter by search term if provided
        if q:
            all_workflows = [wf for wf in all_workflows if self._matches_search(wf, q)]
        
        # Sort by updated_at descending
        all_workflows.sort(
            key=lambda wf: self._parse_datetime(wf.modified_datetime) or datetime.min,
            reverse=True,
        )
        
        # Take top N
        results = all_workflows[:limit]
        
        return [self._format_workflow_summary(wf) for wf in results]
    
    def _matches_search(self, wf: Workflow, q: str) -> bool:
        """
        Check if a workflow matches the search term.
        
        Matches against vendor name, invoice number, or amount.
        For multi-word queries, matches if ANY term matches ANY field (OR logic).
        """
        ctx = wf.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        
        # Extract searchable fields
        vendor_name = vendor_match.get("vendor", {}).get("name") or ""
        invoice_number = classification.get("invoice_number") or ""
        amount = classification.get("amount")
        
        # Normalize amount to string for searching
        amount_str = ""
        if amount is not None:
            # Convert to string, keeping various formats searchable
            amount_str = str(amount)
        
        # Split search into terms and check each
        search_terms = q.lower().split()
        
        for term in search_terms:
            # Normalize term (strip common currency symbols/commas for amount matching)
            normalized_term = term.replace(",", "").replace("$", "")
            
            # Check if term matches any field
            if term in vendor_name.lower():
                return True
            if term in invoice_number.lower():
                return True
            if normalized_term in amount_str.replace(",", ""):
                return True
        
        return False
    
    def get_workflow_with_events(self, public_id: str) -> Optional[Dict]:
        """
        Get workflow detail with full event history.
        
        Args:
            public_id: Workflow public ID
            
        Returns:
            Dict with workflow and events, or None if not found
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            return None
        
        events = self.event_repo.read_by_workflow_id(workflow.id)
        
        return {
            "workflow": workflow.to_dict(),
            "events": [event.to_dict() for event in events],
            "summary": self._format_workflow_summary(workflow),
        }
    
    def get_workflows_by_state(
        self,
        tenant_id: int,
        state: str,
    ) -> List[Dict]:
        """
        Get workflows in a specific state.
        
        Args:
            tenant_id: Tenant ID
            state: State to filter by
            
        Returns:
            List of workflow dicts
        """
        workflows = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state=state,
        )
        return [self._format_workflow_summary(wf) for wf in workflows]
    
    def get_failed_workflows(
        self,
        tenant_id: int,
        limit: int = 20,
    ) -> List[Dict]:
        """
        Get recent failed workflows for debugging.
        
        Args:
            tenant_id: Tenant ID
            limit: Maximum number to return
            
        Returns:
            List of failed workflow dicts with error details
        """
        failed = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state="failed",
        )
        
        # Sort by updated_at descending
        failed.sort(
            key=lambda wf: self._parse_datetime(wf.modified_datetime) or datetime.min,
            reverse=True,
        )
        
        # Take top N and format with error details
        result = []
        for wf in failed[:limit]:
            summary = self._format_workflow_summary(wf)
            
            # Extract error info from context
            ctx = wf.context or {}
            summary["error"] = ctx.get("error") or ctx.get("last_error")
            summary["error_step"] = ctx.get("error_step") or ctx.get("last_step")
            
            result.append(summary)
        
        return result
    
    def get_recent_notifications(
        self,
        tenant_id: int,
        since: datetime,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Get recent workflow notifications for polling.
        
        Returns state transition events to actionable states (failed, awaiting_approval,
        completed) that occurred after the given timestamp.
        
        Args:
            tenant_id: Tenant ID to filter by
            since: Only return events created after this timestamp
            limit: Maximum number of notifications to return
            
        Returns:
            List of notification dicts with workflow details and summary
        """
        from shared.database import get_connection
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                
                # Query WorkflowEvent joined with Workflow for state transitions
                # to actionable states since the given timestamp
                sql = """
                    SELECT TOP (?)
                        e.Id AS EventId,
                        e.FromState,
                        e.ToState,
                        e.CreatedDatetime,
                        w.PublicId,
                        w.WorkflowType
                    FROM dbo.WorkflowEvent e
                    INNER JOIN dbo.Workflow w ON e.WorkflowId = w.Id
                    WHERE w.TenantId = ?
                      AND e.EventType = 'state_changed'
                      AND e.ToState IN ('failed', 'awaiting_approval', 'completed')
                      AND e.CreatedDatetime > ?
                    ORDER BY e.CreatedDatetime DESC
                """
                
                cursor.execute(sql, [limit, tenant_id, since])
                rows = cursor.fetchall()
                
                notifications = []
                for row in rows:
                    public_id = str(row.PublicId) if row.PublicId else None
                    workflow_type = row.WorkflowType
                    from_state = row.FromState
                    to_state = row.ToState
                    created_datetime = str(row.CreatedDatetime) if row.CreatedDatetime else None
                    
                    # Build human-readable summary
                    summary = self._build_notification_summary(workflow_type, to_state)
                    
                    notifications.append({
                        "id": row.EventId,
                        "workflow_public_id": public_id,
                        "workflow_type": workflow_type,
                        "from_state": from_state,
                        "to_state": to_state,
                        "created_datetime": created_datetime,
                        "summary": summary,
                    })
                
                return notifications
                
        except Exception as error:
            logger.error("Error fetching notifications: %s", error)
            return []
    
    def _build_notification_summary(self, workflow_type: str, to_state: str) -> str:
        """Build a human-readable notification summary."""
        type_names = {
            "bill_intake": "Bill extraction",
            "payment_inquiry": "Payment inquiry",
        }
        
        # Map states to action descriptions
        state_actions = {
            "failed": "failed",
            "awaiting_approval": "needs approval",
            "completed": "completed",
        }
        
        type_name = type_names.get(workflow_type, workflow_type.replace("_", " ").title())
        action = state_actions.get(to_state, to_state)
        
        return f"{type_name} {action}"
    
    def _format_workflow_summary(self, wf: Workflow) -> Dict:
        """Format a workflow for display in lists."""
        ctx = wf.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        
        return {
            "public_id": wf.public_id,
            "workflow_type": wf.workflow_type,
            "state": wf.state,
            "vendor_name": vendor_match.get("vendor", {}).get("name"),
            "vendor_id": wf.vendor_id,
            "project_id": wf.project_id,
            "bill_id": wf.bill_id,
            "invoice_number": classification.get("invoice_number"),
            "amount": classification.get("amount"),
            "created_datetime": wf.created_datetime,
            "modified_datetime": wf.modified_datetime,
            "completed_datetime": wf.completed_datetime,
            "is_active": wf.is_active,
        }
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string to datetime object."""
        if not dt_str:
            return None
        try:
            # Handle various formats
            if "T" in dt_str:
                # ISO format
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00").split("+")[0])
            else:
                return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None
    
    def _get_final_states(self) -> set:
        """Return set of final workflow states that cannot be modified."""
        return {"completed", "cancelled", "abandoned", "rejected"}
    
    # -------------------------------------------------------------------------
    # Workflow Action Methods
    # -------------------------------------------------------------------------
    
    def retry_workflow(self, public_id: str, user_id: int) -> Dict:
        """
        Retry a failed workflow from its last step.
        
        Only works for workflows in 'failed' or 'needs_review' state.
        
        Args:
            public_id: Workflow public ID
            user_id: ID of user performing the action
            
        Returns:
            Dict with success status, new state, and message
            
        Raises:
            ValueError: If workflow not found or in invalid state
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {public_id}")
        
        allowed_states = {"failed", "needs_review"}
        if workflow.state not in allowed_states:
            raise ValueError(
                f"Cannot retry workflow in state '{workflow.state}'. "
                f"Allowed states: {', '.join(allowed_states)}"
            )
        
        retry_trigger = "retry_step"
        
        # Log the manual action before transition
        self.event_repo.create(
            workflow_id=workflow.id,
            event_type="manual_action",
            from_state=workflow.state,
            step_name="retry",
            data={"action": "retry", "user_id": user_id},
            created_by=f"user:{user_id}",
        )
        
        # Perform the transition
        orchestrator = WorkflowOrchestrator()
        try:
            updated_workflow = orchestrator.transition(
                public_id=public_id,
                trigger=retry_trigger,
                created_by=f"user:{user_id}",
            )
            
            logger.info(
                f"Workflow {public_id} retried by user {user_id}: "
                f"{workflow.state} -> {updated_workflow.state}"
            )
            
            return {
                "success": True,
                "state": updated_workflow.state,
                "message": f"Workflow retried successfully. New state: {updated_workflow.state}",
            }
        except Exception as e:
            logger.error(f"Failed to retry workflow {public_id}: {e}")
            raise ValueError(f"Failed to retry workflow: {str(e)}")
    
    def cancel_workflow(self, public_id: str, user_id: int, reason: Optional[str] = None) -> Dict:
        """
        Cancel a workflow that is stuck or unwanted.
        
        Works for any non-final state.
        
        Args:
            public_id: Workflow public ID
            user_id: ID of user performing the action
            reason: Optional reason for cancellation
            
        Returns:
            Dict with success status, new state, and message
            
        Raises:
            ValueError: If workflow not found or in final state
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {public_id}")
        
        final_states = self._get_final_states()
        if workflow.state in final_states:
            raise ValueError(
                f"Cannot cancel workflow in final state '{workflow.state}'"
            )
        
        # Log the manual action before transition
        self.event_repo.create(
            workflow_id=workflow.id,
            event_type="manual_action",
            from_state=workflow.state,
            step_name="cancel",
            data={"action": "cancel", "user_id": user_id, "reason": reason},
            created_by=f"user:{user_id}",
        )
        
        # Perform the transition
        orchestrator = WorkflowOrchestrator()
        try:
            context_updates = {}
            if reason:
                context_updates["cancellation_reason"] = reason
            
            updated_workflow = orchestrator.transition(
                public_id=public_id,
                trigger="cancel_workflow",
                context_updates=context_updates,
                created_by=f"user:{user_id}",
            )
            
            logger.info(
                f"Workflow {public_id} cancelled by user {user_id}: "
                f"{workflow.state} -> {updated_workflow.state}"
            )
            
            return {
                "success": True,
                "state": updated_workflow.state,
                "message": "Workflow cancelled successfully.",
            }
        except Exception as e:
            logger.error(f"Failed to cancel workflow {public_id}: {e}")
            raise ValueError(f"Failed to cancel workflow: {str(e)}")
    
    def approve_workflow(
        self,
        public_id: str,
        user_id: int,
        project_id: int,
        cost_code: str,
    ) -> Dict:
        """
        Manually approve a workflow awaiting approval.
        
        Alternative to email-based approval flow.
        
        Args:
            public_id: Workflow public ID
            user_id: ID of user performing the action
            project_id: Project ID for the approval
            cost_code: Cost code for the approval
            
        Returns:
            Dict with success status, new state, and message
            
        Raises:
            ValueError: If workflow not found or not awaiting approval
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {public_id}")
        
        if workflow.state != "awaiting_approval":
            raise ValueError(
                f"Cannot approve workflow in state '{workflow.state}'. "
                f"Only 'awaiting_approval' workflows can be approved."
            )
        
        # Log the manual action before transition
        self.event_repo.create(
            workflow_id=workflow.id,
            event_type="manual_action",
            from_state=workflow.state,
            step_name="approve",
            data={
                "action": "approve",
                "user_id": user_id,
                "project_id": project_id,
                "cost_code": cost_code,
            },
            created_by=f"user:{user_id}",
        )
        
        # Perform the transition with approval context
        orchestrator = WorkflowOrchestrator()
        try:
            context_updates = {
                "approval": {
                    "approved_by": user_id,
                    "project_id": project_id,
                    "cost_code": cost_code,
                    "approved_at": datetime.utcnow().isoformat(),
                    "method": "dashboard",
                }
            }
            
            updated_workflow = orchestrator.transition(
                public_id=public_id,
                trigger="approval_granted",
                context_updates=context_updates,
                created_by=f"user:{user_id}",
            )
            
            logger.info(
                f"Workflow {public_id} approved by user {user_id}: "
                f"project={project_id}, cost_code={cost_code}"
            )
            
            return {
                "success": True,
                "state": updated_workflow.state,
                "message": "Workflow approved successfully.",
            }
        except Exception as e:
            logger.error(f"Failed to approve workflow {public_id}: {e}")
            raise ValueError(f"Failed to approve workflow: {str(e)}")
    
    def reject_workflow(self, public_id: str, user_id: int, reason: str) -> Dict:
        """
        Reject a workflow awaiting approval.
        
        Args:
            public_id: Workflow public ID
            user_id: ID of user performing the action
            reason: Reason for rejection
            
        Returns:
            Dict with success status, new state, and message
            
        Raises:
            ValueError: If workflow not found or not awaiting approval
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise ValueError(f"Workflow not found: {public_id}")
        
        if workflow.state != "awaiting_approval":
            raise ValueError(
                f"Cannot reject workflow in state '{workflow.state}'. "
                f"Only 'awaiting_approval' workflows can be rejected."
            )
        
        # Log the manual action before transition
        self.event_repo.create(
            workflow_id=workflow.id,
            event_type="manual_action",
            from_state=workflow.state,
            step_name="reject",
            data={"action": "reject", "user_id": user_id, "reason": reason},
            created_by=f"user:{user_id}",
        )
        
        # Perform the transition
        orchestrator = WorkflowOrchestrator()
        try:
            context_updates = {
                "rejection": {
                    "rejected_by": user_id,
                    "reason": reason,
                    "rejected_at": datetime.utcnow().isoformat(),
                    "method": "dashboard",
                }
            }
            
            updated_workflow = orchestrator.transition(
                public_id=public_id,
                trigger="approval_denied",
                context_updates=context_updates,
                created_by=f"user:{user_id}",
            )
            
            logger.info(
                f"Workflow {public_id} rejected by user {user_id}: {reason}"
            )
            
            return {
                "success": True,
                "state": updated_workflow.state,
                "message": "Workflow rejected.",
            }
        except Exception as e:
            logger.error(f"Failed to reject workflow {public_id}: {e}")
            raise ValueError(f"Failed to reject workflow: {str(e)}")
