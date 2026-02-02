# Agent base classes and registry
from workflows.agents.base import Agent, AgentContext, AgentResult
from workflows.agents.registry import AgentRegistry, get_agent_registry

# Workflow agents
from workflows.agents.email_triage import EmailTriageAgent
from workflows.agents.bill_extraction import BillExtractionAgent
from workflows.agents.approval_parser import ApprovalParserAgent
from workflows.agents.correlation import CorrelationAgent

# Matching agents
from workflows.agents.vendor_match import VendorMatchAgent
from workflows.agents.project_match import ProjectMatchAgent

__all__ = [
    # Base classes and registry
    "Agent",
    "AgentContext",
    "AgentResult",
    "AgentRegistry",
    "get_agent_registry",
    # Workflow agents
    "EmailTriageAgent",
    "BillExtractionAgent",
    "ApprovalParserAgent",
    "CorrelationAgent",
    # Matching agents
    "VendorMatchAgent",
    "ProjectMatchAgent",
]
