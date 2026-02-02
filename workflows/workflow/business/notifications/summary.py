# Python Standard Library Imports
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Third-party Imports
from jinja2 import Environment, FileSystemLoader

# Local Imports
from workflows.workflow.business.capabilities.registry import CapabilityRegistry, get_capability_registry
from workflows.workflow.persistence.repo import WorkflowRepository

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class DailySummaryGenerator:
    """
    Generates and sends daily workflow summary emails.
    """
    
    def __init__(
        self,
        capabilities: Optional[CapabilityRegistry] = None,
    ):
        self.capabilities = capabilities or get_capability_registry()
        self.workflow_repo = WorkflowRepository()
        
        if TEMPLATE_DIR.exists():
            self.jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        else:
            self.jinja_env = None
            logger.warning(f"Template directory not found: {TEMPLATE_DIR}")
    
    def generate_summary_data(
        self,
        tenant_id: int,
        summary_date: Optional[datetime] = None,
    ) -> Dict:
        """
        Gather all data needed for the daily summary.
        
        Args:
            tenant_id: Tenant ID
            summary_date: Date to generate summary for (defaults to today)
            
        Returns:
            Dict with all summary data
        """
        if summary_date is None:
            summary_date = datetime.utcnow()
        
        start_of_day = summary_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        
        # Get workflows created today
        new_today = self.workflow_repo.read_created_between(
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
        
        # Get workflows needing attention (needs_review state)
        needs_attention = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state="needs_review",
        )
        
        # Get all active workflows
        active_workflows = self.workflow_repo.read_active_workflows(tenant_id=tenant_id)
        
        # Calculate stats
        total_pending_value = 0
        total_days = 0
        approval_count = 0
        
        for wf in awaiting_approval:
            ctx = wf.context or {}
            classification = ctx.get("classification", {})
            amount = classification.get("amount", 0)
            if amount:
                total_pending_value += amount
            
            # Calculate days waiting
            if wf.created_datetime:
                days = (datetime.utcnow() - datetime.fromisoformat(wf.created_datetime)).days
                total_days += days
                approval_count += 1
        
        avg_days = total_days / approval_count if approval_count > 0 else 0
        
        # Format workflow data for template
        def format_workflow(wf, include_days=False):
            ctx = wf.context or {}
            classification = ctx.get("classification", {})
            vendor_match = ctx.get("vendor_match", {})
            
            data = {
                "public_id": wf.public_id,
                "vendor_name": vendor_match.get("vendor", {}).get("name", "Unknown Vendor"),
                "invoice_number": classification.get("invoice_number"),
                "amount": classification.get("amount"),
                "qbo_synced": ctx.get("qbo_sync") == "completed",
            }
            
            if include_days and wf.created_datetime:
                data["days_waiting"] = (datetime.utcnow() - datetime.fromisoformat(wf.created_datetime)).days
            
            return data
        
        def format_needs_attention(wf):
            data = format_workflow(wf)
            ctx = wf.context or {}
            
            # Determine reason for needing attention
            if ctx.get("triage_error"):
                data["reason"] = "Classification failed"
            elif ctx.get("parse_error"):
                data["reason"] = "Could not parse approval response"
            elif ctx.get("entity_error"):
                data["reason"] = "Entity creation failed"
            else:
                data["reason"] = "Manual review required"
            
            return data
        
        return {
            "summary_date": summary_date.strftime("%B %d, %Y"),
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "stats": {
                "new_today": len(new_today),
                "completed_today": len(completed_today),
                "awaiting_approval": len(awaiting_approval),
                "total_active": len(active_workflows),
                "total_pending_value": total_pending_value,
                "avg_days_to_approval": avg_days,
            },
            "awaiting_approval": [format_workflow(wf, include_days=True) for wf in awaiting_approval],
            "completed_today": [format_workflow(wf) for wf in completed_today],
            "needs_attention": [format_needs_attention(wf) for wf in needs_attention],
        }
    
    def render_summary_html(self, summary_data: Dict) -> str:
        """Render the summary data to HTML."""
        if self.jinja_env is None:
            return f"Daily Summary: {summary_data}"
        
        try:
            template = self.jinja_env.get_template("daily_summary.html")
            return template.render(**summary_data)
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            return f"Daily Summary: {summary_data}"
    
    def send_summary(
        self,
        tenant_id: int,
        access_token: str,
        recipients: List[str],
        summary_date: Optional[datetime] = None,
    ) -> bool:
        """
        Generate and send the daily summary email.
        
        Args:
            tenant_id: Tenant ID
            access_token: MS Graph access token
            recipients: List of email addresses
            summary_date: Date for summary (defaults to today)
            
        Returns:
            True if sent successfully
        """
        logger.info(f"Generating daily summary for tenant {tenant_id}")
        
        # Generate summary data
        summary_data = self.generate_summary_data(tenant_id, summary_date)
        
        # Render HTML
        html_body = self.render_summary_html(summary_data)
        
        # Build subject
        stats = summary_data["stats"]
        subject = (
            f"📊 Daily Workflow Summary - "
            f"{stats['new_today']} new, "
            f"{stats['completed_today']} completed, "
            f"{stats['awaiting_approval']} awaiting"
        )
        
        # Send email
        result = self.capabilities.email.send_as_user(
            access_token=access_token,
            to_recipients=recipients,
            subject=subject,
            body=html_body,
            body_type="html",
        )
        
        if result.success:
            logger.info(f"Daily summary sent to {len(recipients)} recipient(s)")
            return True
        else:
            logger.error(f"Failed to send daily summary: {result.error}")
            return False
