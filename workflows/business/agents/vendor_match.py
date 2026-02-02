# Python Standard Library Imports
import logging
import re
from typing import Dict, Optional

# Local Imports
from workflows.agents.base import Agent, AgentContext, AgentResult
from workflows.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


class VendorMatchAgent(Agent):
    """
    Agent for matching vendor names to existing vendors.
    
    Uses EntityCapabilities for fuzzy matching with embeddings.
    Auto-matches when confidence >= 85%, otherwise returns candidates
    for human selection.
    """
    
    @property
    def name(self) -> str:
        return "vendor_match"
    
    @property
    def description(self) -> str:
        return "Match incoming vendor names/emails to existing vendors"
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Match vendor by name or email.
        
        Expected trigger_data:
            - vendor_name: Optional[str] - Vendor name guess (from LLM or user)
            - from_address: str - Email address to match
            - email_subject: Optional[str] - Email subject for name extraction
        
        Returns AgentResult with data:
            - matched: bool
            - vendor: Optional[Dict] - {id, public_id, name} if matched
            - match_type: Optional[str] - "exact_email", "exact_name", "fuzzy_high_confidence"
            - confidence: Optional[float] - Match confidence (0-1)
            - candidates: List[Dict] - Candidates for human selection if not matched
        """
        self._log_start(context)
        
        trigger_data = context.trigger_data or {}
        vendor_name = trigger_data.get("vendor_name")
        from_address = trigger_data.get("from_address", "")
        email_subject = trigger_data.get("email_subject")
        
        try:
            result = await self._match_vendor(
                vendor_guess=vendor_name,
                from_address=from_address,
                tenant_id=context.tenant_id,
                email_subject=email_subject,
            )
            
            agent_result = AgentResult.ok(data=result)
            self._log_complete(agent_result)
            return agent_result
            
        except Exception as e:
            logger.exception(f"Vendor matching failed: {e}")
            return AgentResult.fail(error=str(e))
    
    async def match(
        self,
        vendor_name: Optional[str],
        tenant_id: int,
        from_address: str = "",
        email_subject: Optional[str] = None,
    ) -> Dict:
        """
        Public helper for cross-agent calls.
        
        Use this when calling from another agent that already has context.
        Returns the same dict structure as run().data.
        """
        return await self._match_vendor(vendor_name, from_address, tenant_id, email_subject)
    
    async def _match_vendor(
        self,
        vendor_guess: Optional[str],
        from_address: str,
        tenant_id: int,
        email_subject: Optional[str] = None,
    ) -> Dict:
        """Match vendor by name or email."""
        # If no vendor guess, try to extract from email subject
        search_name = vendor_guess
        if not search_name and email_subject:
            # Simple extraction: look for common patterns like "VENDOR NAME INVOICE"
            match = re.search(
                r'^(?:Re:\s*)?(.+?)\s*(?:INVOICE|Invoice|INV|Bill|Statement)',
                email_subject,
                re.IGNORECASE
            )
            if match:
                search_name = match.group(1).strip()
                logger.info(f"Extracted vendor name from subject: {search_name}")
        
        if not search_name and not from_address:
            return {"matched": False, "candidates": []}
        
        result = self.capabilities.entity.match_vendor(
            name=search_name,
            email=from_address,
            tenant_id=tenant_id,
        )
        
        if not result.success:
            return {"matched": False, "error": result.error}
        
        data = result.data
        if data is None:
            return {"matched": False, "candidates": []}
        
        # Check if it's a direct match (dict) or candidates (list)
        if isinstance(data, dict):
            return {
                "matched": True,
                "vendor": data,
                "match_type": result.metadata.get("match_type"),
                "confidence": result.metadata.get("confidence", 1.0),
            }
        elif isinstance(data, list):
            candidates = [
                {
                    "id": c.id,
                    "public_id": c.public_id,
                    "name": c.name,
                    "confidence": c.confidence,
                }
                for c in data
            ]
            
            # Auto-match if top candidate has high confidence (>= 85%)
            if candidates and candidates[0]["confidence"] >= 0.85:
                top = candidates[0]
                return {
                    "matched": True,
                    "vendor": {
                        "id": top["id"],
                        "public_id": top["public_id"],
                        "name": top["name"],
                    },
                    "match_type": "fuzzy_high_confidence",
                    "confidence": top["confidence"],
                }
            
            return {
                "matched": False,
                "candidates": candidates,
            }
        
        return {"matched": False, "candidates": []}
