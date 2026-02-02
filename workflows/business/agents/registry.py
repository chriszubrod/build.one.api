# Python Standard Library Imports
import logging
from typing import Dict, Optional, TYPE_CHECKING

# Local Imports
from workflows.agents.base import Agent
from workflows.capabilities.registry import CapabilityRegistry, get_capability_registry

if TYPE_CHECKING:
    from workflows.agents.email_triage import EmailTriageAgent
    from workflows.agents.bill_extraction import BillExtractionAgent
    from workflows.agents.approval_parser import ApprovalParserAgent
    from workflows.agents.correlation import CorrelationAgent
    from workflows.agents.vendor_match import VendorMatchAgent
    from workflows.agents.project_match import ProjectMatchAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Central registry for all workflow agents.
    
    Provides named access to agents and dependency injection
    of capability instances.
    """
    
    def __init__(self, capabilities: Optional[CapabilityRegistry] = None):
        self._agents: Dict[str, Agent] = {}
        self._capabilities = capabilities or get_capability_registry()
        self._initialized = False
    
    def register(self, agent: Agent) -> None:
        """Register an agent instance."""
        self._agents[agent.name] = agent
        logger.info(f"Registered agent: {agent.name}")
    
    def get(self, name: str) -> Optional[Agent]:
        """Get an agent by name."""
        return self._agents.get(name)
    
    def initialize_all(self) -> None:
        """Initialize all standard agents."""
        if self._initialized:
            return
        
        # Import here to avoid circular imports
        from workflows.agents.email_triage import EmailTriageAgent
        from workflows.agents.bill_extraction import BillExtractionAgent
        from workflows.agents.approval_parser import ApprovalParserAgent
        from workflows.agents.correlation import CorrelationAgent
        from workflows.agents.vendor_match import VendorMatchAgent
        from workflows.agents.project_match import ProjectMatchAgent
        
        # Register matching agents first (used by other agents)
        self.register(VendorMatchAgent(self._capabilities))
        self.register(ProjectMatchAgent(self._capabilities))
        
        # Register workflow agents
        self.register(EmailTriageAgent(self._capabilities))
        self.register(BillExtractionAgent(self._capabilities))
        self.register(ApprovalParserAgent(self._capabilities))
        self.register(CorrelationAgent(self._capabilities))
        
        self._initialized = True
        logger.info("All agents initialized")
    
    @property
    def email_triage(self) -> "EmailTriageAgent":
        """Get email triage agent."""
        return self._agents.get("email_triage")
    
    @property
    def bill_extraction(self) -> "BillExtractionAgent":
        """Get bill extraction agent."""
        return self._agents.get("bill_extraction")
    
    @property
    def approval_parser(self) -> "ApprovalParserAgent":
        """Get approval parser agent."""
        return self._agents.get("approval_parser")
    
    @property
    def correlation(self) -> "CorrelationAgent":
        """Get correlation agent."""
        return self._agents.get("correlation")
    
    @property
    def vendor_match(self) -> "VendorMatchAgent":
        """Get vendor match agent."""
        return self._agents.get("vendor_match")
    
    @property
    def project_match(self) -> "ProjectMatchAgent":
        """Get project match agent."""
        return self._agents.get("project_match")


# Global registry instance
_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _registry.initialize_all()
    return _registry
