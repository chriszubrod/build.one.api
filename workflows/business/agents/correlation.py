# Python Standard Library Imports
import logging
from typing import Dict, List, Optional

# Local Imports
from workflows.agents.base import Agent, AgentContext, AgentResult
from workflows.persistence.repo import WorkflowRepository

logger = logging.getLogger(__name__)


class CorrelationAgent(Agent):
    """
    Agent that correlates orphan emails to existing workflows.
    
    When an email arrives without a matching conversation ID,
    this agent uses LLM to try to match it to an open workflow
    based on content analysis.
    """
    
    @property
    def name(self) -> str:
        return "correlation"
    
    @property
    def description(self) -> str:
        return "Matches orphan emails to existing workflows"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.workflow_repo = WorkflowRepository()
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Try to correlate an orphan email to an existing workflow.
        
        Expected context:
        - trigger_data.email_body: The email body to match
        - trigger_data.from_address: Sender email address
        - trigger_data.subject: Email subject
        
        Returns:
        - matched_workflow_id: The public ID of the matched workflow, or None
        - confidence: Confidence score of the match
        """
        self._log_start(context)
        
        try:
            trigger_data = context.trigger_data or {}
            email_body = trigger_data.get("email_body", "")
            from_address = trigger_data.get("from_address", "")
            subject = trigger_data.get("subject", "")
            
            if not email_body:
                return AgentResult.fail("No email_body provided")
            
            # Get open workflows for this tenant
            open_workflows = self._get_open_workflows_for_matching(context.tenant_id)
            
            if not open_workflows:
                return AgentResult.ok(
                    data={"matched_workflow_id": None, "reason": "No open workflows"},
                    context_updates={"correlation_attempted": True},
                )
            
            # Try email-based matching first (fast, no LLM)
            email_match = self._match_by_email(from_address, open_workflows)
            if email_match and email_match.get("confidence", 0) > 0.9:
                return AgentResult.ok(
                    data={
                        "matched_workflow_id": email_match["workflow_public_id"],
                        "confidence": email_match["confidence"],
                        "match_type": "email",
                    },
                    context_updates={
                        "correlation_attempted": True,
                        "correlation_match": email_match,
                    },
                )
            
            # Fall back to LLM matching
            full_text = f"Subject: {subject}\n\n{email_body}"
            llm_result = self.capabilities.llm.match_orphan_email(
                email_body=full_text,
                open_workflows=open_workflows,
            )
            
            if not llm_result.success:
                return AgentResult.fail(f"LLM matching failed: {llm_result.error}")
            
            match_data = llm_result.data
            matched_id = match_data.get("matched_workflow_id")
            confidence = match_data.get("confidence", 0)
            
            # Only accept high-confidence matches
            if matched_id and confidence >= 0.7:
                result = AgentResult.ok(
                    data={
                        "matched_workflow_id": matched_id,
                        "confidence": confidence,
                        "match_type": "llm",
                        "reasoning": match_data.get("reasoning"),
                    },
                    context_updates={
                        "correlation_attempted": True,
                        "correlation_match": match_data,
                    },
                )
            else:
                result = AgentResult.ok(
                    data={
                        "matched_workflow_id": None,
                        "confidence": confidence,
                        "match_type": "none",
                        "reasoning": match_data.get("reasoning"),
                    },
                    context_updates={
                        "correlation_attempted": True,
                        "correlation_no_match_reason": match_data.get("reasoning"),
                    },
                )
            
            self._log_complete(result)
            return result
            
        except Exception as e:
            logger.exception(f"Error in {self.name}")
            return AgentResult.fail(str(e))
    
    def _get_open_workflows_for_matching(
        self,
        tenant_id: int,
        limit: int = 20,
    ) -> List[Dict]:
        """
        Get open workflows with context for matching.
        
        Returns a simplified view suitable for LLM matching.
        """
        try:
            # Get workflows in awaiting_approval state (most likely to receive replies)
            workflows = self.workflow_repo.read_by_tenant_and_state(
                tenant_id=tenant_id,
                state="awaiting_approval",
            )
            
            result = []
            for wf in workflows[:limit]:
                ctx = wf.context or {}
                classification = ctx.get("classification", {})
                vendor_match = ctx.get("vendor_match", {})
                project_match = ctx.get("project_match", {})
                email_info = ctx.get("email", {})
                
                result.append({
                    "public_id": wf.public_id,
                    "vendor_name": vendor_match.get("vendor", {}).get("name"),
                    "vendor_email": email_info.get("from_address"),
                    "project_name": project_match.get("project", {}).get("name"),
                    "amount": classification.get("amount"),
                    "invoice_number": classification.get("invoice_number"),
                    "subject": email_info.get("subject"),
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting open workflows: {e}")
            return []
    
    def _match_by_email(
        self,
        from_address: str,
        open_workflows: List[Dict],
    ) -> Optional[Dict]:
        """
        Try to match by sender email address.
        
        If exactly one workflow has this sender, high confidence match.
        If multiple, return None (ambiguous).
        """
        if not from_address:
            return None
        
        from_lower = from_address.lower()
        matches = [
            wf for wf in open_workflows
            if wf.get("vendor_email", "").lower() == from_lower
        ]
        
        if len(matches) == 1:
            return {
                "workflow_public_id": matches[0]["public_id"],
                "confidence": 0.95,
                "match_type": "email",
            }
        
        return None
