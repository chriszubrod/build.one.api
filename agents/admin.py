# Python Standard Library Imports
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

# Local Imports
from agents.models import Workflow, WorkflowEvent
from agents.persistence.repo import WorkflowRepository, WorkflowEventRepository

logger = logging.getLogger(__name__)


def _parse_datetime(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse a datetime value that may be a string or datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # Try ISO format first
            return datetime.fromisoformat(value.replace('Z', '+00:00').replace('+00:00', ''))
        except ValueError:
            try:
                # Try common SQL Server format
                return datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
    return None


def _format_datetime(value: Union[str, datetime, None]) -> Optional[str]:
    """Format a datetime value to ISO string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value  # Already a string
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class WorkflowAdmin:
    """
    Admin utilities for querying and managing workflows.
    
    Provides helper methods for observability and troubleshooting.
    """
    
    def __init__(self):
        self.workflow_repo = WorkflowRepository()
        self.event_repo = WorkflowEventRepository()
    
    def get_workflow_status(self, public_id: str) -> Optional[Dict]:
        """
        Get detailed status of a workflow.
        
        Returns:
            Dict with workflow details and event history
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            return None
        
        events = self.event_repo.read_by_workflow_id(workflow.id)
        
        ctx = workflow.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        project_match = ctx.get("project_match", {})
        
        return {
            "public_id": workflow.public_id,
            "state": workflow.state,
            "workflow_type": workflow.workflow_type,
            "created_at": _format_datetime(workflow.created_at),
            "updated_at": _format_datetime(workflow.updated_at),
            "vendor": vendor_match.get("vendor", {}).get("name"),
            "invoice_number": classification.get("invoice_number"),
            "amount": classification.get("amount"),
            "project": project_match.get("project", {}).get("name"),
            "classification_category": classification.get("category"),
            "classification_confidence": classification.get("confidence"),
            "qbo_sync_status": ctx.get("qbo_sync"),
            "reminder_count": ctx.get("reminder_count", 0),
            "event_count": len(events),
            "events": [
                {
                    "type": e.event_type,
                    "from_state": e.from_state,
                    "to_state": e.to_state,
                    "step_name": e.step_name,
                    "created_at": _format_datetime(e.created_at),
                    "created_by": e.created_by,
                }
                for e in events[-10:]  # Last 10 events
            ],
        }
    
    def get_stuck_workflows(
        self,
        tenant_id: int,
        stuck_hours: int = 24,
    ) -> List[Dict]:
        """
        Find workflows that haven't progressed in the given time.
        
        Args:
            tenant_id: Tenant ID
            stuck_hours: Hours of inactivity to consider "stuck"
            
        Returns:
            List of stuck workflow summaries
        """
        cutoff = datetime.utcnow() - timedelta(hours=stuck_hours)
        
        # Get all active workflows
        active = self.workflow_repo.read_active_workflows(tenant_id)
        
        stuck = []
        for wf in active:
            updated = _parse_datetime(wf.updated_at)
            if updated and updated < cutoff:
                ctx = wf.context or {}
                classification = ctx.get("classification", {})
                vendor_match = ctx.get("vendor_match", {})
                
                stuck.append({
                    "public_id": wf.public_id,
                    "state": wf.state,
                    "vendor": vendor_match.get("vendor", {}).get("name"),
                    "invoice_number": classification.get("invoice_number"),
                    "last_update": _format_datetime(wf.updated_at),
                    "hours_stuck": int((datetime.utcnow() - updated).total_seconds() / 3600),
                })
        
        return sorted(stuck, key=lambda x: x["hours_stuck"], reverse=True)
    
    def get_workflow_metrics(
        self,
        tenant_id: int,
        days: int = 30,
    ) -> Dict:
        """
        Get workflow metrics for the given period.
        
        Args:
            tenant_id: Tenant ID
            days: Number of days to analyze
            
        Returns:
            Dict with various metrics
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Get workflows created in period
        created = self.workflow_repo.read_created_between(
            tenant_id=tenant_id,
            start=start_date,
            end=datetime.utcnow(),
        )
        
        # Get workflows completed in period
        completed = self.workflow_repo.read_completed_between(
            tenant_id=tenant_id,
            start=start_date,
            end=datetime.utcnow(),
        )
        
        # Calculate metrics
        total_created = len(created)
        total_completed = len(completed)
        
        # Calculate average completion time
        completion_times = []
        for wf in completed:
            created = _parse_datetime(wf.created_at)
            updated = _parse_datetime(wf.updated_at)
            if created and updated:
                delta = updated - created
                completion_times.append(delta.total_seconds() / 3600)  # Hours
        
        avg_completion_hours = sum(completion_times) / len(completion_times) if completion_times else 0
        
        # Calculate total value processed
        total_value = 0
        for wf in completed:
            ctx = wf.context or {}
            amount = ctx.get("classification", {}).get("amount", 0)
            if amount:
                total_value += amount
        
        # State distribution
        active = self.workflow_repo.read_active_workflows(tenant_id)
        state_counts = {}
        for wf in active:
            state_counts[wf.state] = state_counts.get(wf.state, 0) + 1
        
        return {
            "period_days": days,
            "total_created": total_created,
            "total_completed": total_completed,
            "completion_rate": total_completed / total_created if total_created > 0 else 0,
            "avg_completion_hours": avg_completion_hours,
            "total_value_processed": total_value,
            "active_workflows": len(active),
            "state_distribution": state_counts,
        }
    
    def get_error_workflows(
        self,
        tenant_id: int,
        limit: int = 20,
    ) -> List[Dict]:
        """
        Get workflows that encountered errors.
        
        Args:
            tenant_id: Tenant ID
            limit: Maximum number to return
            
        Returns:
            List of workflows with error details
        """
        # Get workflows in error states
        needs_review = self.workflow_repo.read_by_tenant_and_state(tenant_id, "needs_review")
        
        errors = []
        for wf in needs_review[:limit]:
            ctx = wf.context or {}
            
            # Find the error
            error_detail = (
                ctx.get("triage_error") or
                ctx.get("parse_error") or
                ctx.get("entity_error") or
                ctx.get("qbo_error") or
                "Unknown error"
            )
            
            classification = ctx.get("classification", {})
            vendor_match = ctx.get("vendor_match", {})
            
            errors.append({
                "public_id": wf.public_id,
                "state": wf.state,
                "vendor": vendor_match.get("vendor", {}).get("name"),
                "invoice_number": classification.get("invoice_number"),
                "error": error_detail,
                "created_at": _format_datetime(wf.created_at),
            })
        
        return errors
    
    def retry_workflow(
        self,
        public_id: str,
        from_state: Optional[str] = None,
    ) -> bool:
        """
        Attempt to retry a failed workflow.
        
        Args:
            public_id: Workflow public ID
            from_state: State to retry from (defaults to current state)
            
        Returns:
            True if retry was initiated
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            logger.error(f"Workflow not found: {public_id}")
            return False
        
        # Reset the state if specified
        if from_state and from_state != workflow.state:
            self.workflow_repo.update_state(
                public_id=public_id,
                state=from_state,
            )
            logger.info(f"Reset workflow {public_id} to state {from_state}")
        
        # Clear error context
        ctx = workflow.context or {}
        error_keys = ["triage_error", "parse_error", "entity_error", "qbo_error"]
        for key in error_keys:
            ctx.pop(key, None)
        
        self.workflow_repo.update_context(public_id=public_id, context=ctx)
        
        logger.info(f"Workflow {public_id} ready for retry")
        return True
    
    def get_vendor_summary(
        self,
        tenant_id: int,
        days: int = 30,
    ) -> List[Dict]:
        """
        Get workflow summary grouped by vendor.
        
        Returns:
            List of vendor summaries sorted by total value
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        
        workflows = self.workflow_repo.read_created_between(
            tenant_id=tenant_id,
            start=start_date,
            end=datetime.utcnow(),
        )
        
        vendor_data = {}
        
        for wf in workflows:
            ctx = wf.context or {}
            vendor_match = ctx.get("vendor_match", {})
            vendor_name = vendor_match.get("vendor", {}).get("name", "Unknown")
            
            if vendor_name not in vendor_data:
                vendor_data[vendor_name] = {
                    "vendor_name": vendor_name,
                    "workflow_count": 0,
                    "total_value": 0,
                    "completed_count": 0,
                    "pending_count": 0,
                }
            
            vendor_data[vendor_name]["workflow_count"] += 1
            
            amount = ctx.get("classification", {}).get("amount", 0)
            if amount:
                vendor_data[vendor_name]["total_value"] += amount
            
            if wf.state == "completed":
                vendor_data[vendor_name]["completed_count"] += 1
            elif wf.state not in ["rejected", "abandoned"]:
                vendor_data[vendor_name]["pending_count"] += 1
        
        return sorted(
            vendor_data.values(),
            key=lambda x: x["total_value"],
            reverse=True,
        )
