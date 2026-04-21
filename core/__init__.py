# Core Workflows Module
# State-machine workflow framework for multi-step business processes.
# Public import path: `core.workflow` and `core.workflow_event`.

from core.workflow.business.models import Workflow, WorkflowEvent

# Lazy imports to avoid circular dependencies and missing module issues
# Import specific components as needed rather than eagerly loading everything

__all__ = [
    "Workflow",
    "WorkflowEvent",
]


def get_orchestrator():
    """Get the WorkflowOrchestrator class."""
    from core.workflow.business.orchestrator import WorkflowOrchestrator
    return WorkflowOrchestrator


def get_scheduler():
    """Get the WorkflowScheduler class, or None if not available."""
    try:
        from core.workflow.business.scheduler import WorkflowScheduler
        return WorkflowScheduler
    except ImportError:
        return None


