# Python Standard Library Imports
import logging
from typing import Dict, List, Optional

# Local Imports
from workflows.agents.base import Agent, AgentContext, AgentResult
from workflows.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


class ProjectMatchAgent(Agent):
    """
    Agent for matching project names to existing projects.
    
    Uses EntityCapabilities for fuzzy matching with embeddings.
    Auto-matches when confidence >= threshold (default 70%),
    otherwise returns candidates for human selection.
    """
    
    # Default auto-match threshold (70% confidence)
    DEFAULT_THRESHOLD = 0.70
    
    @property
    def name(self) -> str:
        return "project_match"
    
    @property
    def description(self) -> str:
        return "Match project hints to existing projects"
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Match project by name or address hint.
        
        Expected trigger_data:
            - project_hint: str - Project name/address to search
            - auto_match_threshold: Optional[float] - Override default threshold (0.70)
        
        Returns AgentResult with data:
            - matched: bool
            - project: Optional[Dict] - {id, public_id, name} if matched
            - match_type: Optional[str] - "exact", "fuzzy_high_confidence"
            - confidence: Optional[float] - Match confidence (0-1)
            - candidates: List[Dict] - Candidates for human selection if not matched
        """
        self._log_start(context)
        
        trigger_data = context.trigger_data or {}
        project_hint = trigger_data.get("project_hint")
        threshold = trigger_data.get("auto_match_threshold", self.DEFAULT_THRESHOLD)
        
        try:
            result = await self._match_project(
                project_hint=project_hint,
                tenant_id=context.tenant_id,
                threshold=threshold,
            )
            
            agent_result = AgentResult.ok(data=result)
            self._log_complete(agent_result)
            return agent_result
            
        except Exception as e:
            logger.exception(f"Project matching failed: {e}")
            return AgentResult.fail(error=str(e))
    
    async def match(
        self,
        project_hint: str,
        tenant_id: int,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Dict:
        """
        Public helper for cross-agent calls.
        
        Use this when calling from another agent that already has context.
        Returns the same dict structure as run().data.
        """
        return await self._match_project(project_hint, tenant_id, threshold)
    
    async def _match_project(
        self,
        project_hint: Optional[str],
        tenant_id: int,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Dict:
        """Match project by name or address hint."""
        if not project_hint:
            return {"matched": False, "candidates": []}
        
        result = self.capabilities.entity.match_project(
            name=project_hint,
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
                "project": data,
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
            
            # Auto-match if top candidate meets threshold
            if candidates and candidates[0]["confidence"] >= threshold:
                top = candidates[0]
                return {
                    "matched": True,
                    "project": {
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
