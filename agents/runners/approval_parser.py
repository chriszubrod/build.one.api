# Python Standard Library Imports
import logging
from typing import Dict, Optional

# Local Imports
from agents.capabilities.llm import ParsedReply
from agents.runners.base import Agent, AgentContext, AgentResult

logger = logging.getLogger(__name__)


class ApprovalParserAgent(Agent):
    """
    Agent that parses approval response emails.
    
    Takes a reply email and extracts:
    - Approval decision (approved, rejected, question)
    - Project confirmation/correction
    - Cost code
    - Any notes or questions
    """
    
    @property
    def name(self) -> str:
        return "approval_parser"
    
    @property
    def description(self) -> str:
        return "Parses approval response emails"
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Parse an approval response email.
        
        Expected context:
        - trigger_data.reply_body: The reply email body
        - trigger_data.reply_subject: The reply subject
        - workflow_context.classification: Original classification
        - workflow_context.vendor_match: Original vendor match
        - workflow_context.project_match: Original project match
        
        Returns:
        - decision: approved, rejected, question, unclear
        - project_id: Confirmed or corrected project ID
        - cost_code: Extracted cost code
        - notes: Any additional notes
        """
        self._log_start(context)
        
        try:
            trigger_data = context.trigger_data or {}
            reply_body = trigger_data.get("reply_body", "")
            reply_subject = trigger_data.get("reply_subject", "")
            
            if not reply_body:
                return AgentResult.fail("No reply_body provided")
            
            # Get original context for better parsing
            workflow_context = context.workflow_context or {}
            original_context = {
                "vendor": workflow_context.get("vendor_match", {}).get("vendor", {}).get("name"),
                "project_guess": workflow_context.get("project_match", {}).get("project", {}).get("name"),
                "amount": workflow_context.get("classification", {}).get("amount"),
                "invoice_number": workflow_context.get("classification", {}).get("invoice_number"),
            }
            
            # Parse the reply
            full_reply = f"Subject: {reply_subject}\n\n{reply_body}" if reply_subject else reply_body
            
            parse_result = self.capabilities.llm.parse_approval_reply(
                reply_body=full_reply,
                original_context=original_context,
            )
            
            if not parse_result.success:
                return AgentResult.fail(f"Parsing failed: {parse_result.error}")
            
            parsed: ParsedReply = parse_result.data
            
            # Try to resolve project if mentioned
            project_id = None
            if parsed.project_name:
                project_match = await self._resolve_project(
                    parsed.project_name,
                    context.tenant_id,
                )
                if project_match:
                    project_id = project_match.get("id")
            
            # Build context updates
            context_updates = {
                "approval_response": {
                    "decision": parsed.decision,
                    "confidence": parsed.confidence,
                    "project_name": parsed.project_name,
                    "project_id": project_id,
                    "cost_code": parsed.cost_code,
                    "notes": parsed.notes,
                    "question_text": parsed.question_text,
                },
            }
            
            # Determine next trigger based on decision
            trigger_map = {
                "approved": "approval_granted",
                "rejected": "approval_denied",
                "question": "approval_question",
                "unclear": "parse_failed",
            }
            next_trigger = trigger_map.get(parsed.decision, "parse_failed")
            
            # If decision is unclear with low confidence, flag for review
            if parsed.decision == "unclear" or parsed.confidence < 0.7:
                return AgentResult.needs_human_input(
                    reason=f"Could not parse approval response clearly (confidence: {parsed.confidence:.0%})",
                    data=parsed,
                    context_updates=context_updates,
                )
            
            result = AgentResult.ok(
                data=parsed,
                context_updates=context_updates,
                next_trigger=next_trigger,
            )
            
            self._log_complete(result)
            return result
            
        except Exception as e:
            logger.exception(f"Error in {self.name}")
            return AgentResult.fail(str(e))
    
    async def _resolve_project(
        self,
        project_name: str,
        tenant_id: int,
    ) -> Optional[Dict]:
        """Resolve a project name to a project record."""
        result = self.capabilities.entity.match_project(
            name=project_name,
            tenant_id=tenant_id,
        )
        
        if result.success and isinstance(result.data, dict):
            return result.data
        
        return None
