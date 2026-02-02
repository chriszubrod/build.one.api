# Python Standard Library Imports
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Local Imports
from workflows.capabilities.registry import CapabilityRegistry, get_capability_registry

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """
    Context provided to an agent for execution.
    """
    tenant_id: int
    access_token: str  # MS Graph access token for API calls
    workflow_public_id: Optional[str] = None
    workflow_context: Optional[Dict] = None
    trigger_data: Optional[Dict] = None


@dataclass
class AgentResult:
    """
    Result returned by an agent after execution.
    """
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    context_updates: Dict[str, Any] = field(default_factory=dict)  # Updates to merge into workflow context
    next_trigger: Optional[str] = None  # Suggested workflow trigger to fire
    
    @classmethod
    def ok(
        cls,
        data: Any = None,
        context_updates: Optional[Dict] = None,
        next_trigger: Optional[str] = None,
    ) -> "AgentResult":
        """Create a successful result."""
        return cls(
            success=True,
            data=data,
            context_updates=context_updates or {},
            next_trigger=next_trigger,
        )
    
    @classmethod
    def fail(cls, error: str, data: Any = None) -> "AgentResult":
        """Create a failed result."""
        return cls(success=False, error=error, data=data)
    
    @classmethod
    def needs_human_input(
        cls,
        reason: str,
        data: Any = None,
        context_updates: Optional[Dict] = None,
    ) -> "AgentResult":
        """Create a result indicating human input is needed."""
        return cls(
            success=True,
            data=data,
            context_updates={
                **(context_updates or {}),
                "needs_human_input": True,
                "human_input_reason": reason,
            },
        )


class Agent(ABC):
    """
    Base class for all agents.
    
    Agents encapsulate decision-making logic for specific tasks,
    using capabilities to interact with external systems.
    """
    
    def __init__(self, capabilities: Optional[CapabilityRegistry] = None):
        self.capabilities = capabilities or get_capability_registry()
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this agent."""
        pass
    
    @property
    def description(self) -> str:
        """Human-readable description of what this agent does."""
        return ""
    
    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Execute the agent's logic.
        
        Args:
            context: Execution context with tenant, tokens, and workflow data
            
        Returns:
            AgentResult with outcome and any context updates
        """
        pass
    
    def _log_start(self, context: AgentContext) -> None:
        """Log agent execution start."""
        logger.info(
            f"Agent {self.name} starting for workflow {context.workflow_public_id}"
        )
    
    def _log_complete(self, result: AgentResult) -> None:
        """Log agent execution completion."""
        if result.success:
            logger.info(f"Agent {self.name} completed successfully")
        else:
            logger.warning(f"Agent {self.name} failed: {result.error}")
