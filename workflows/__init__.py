# Workflows Module
# Agentic workflow framework for automating multi-step business processes

from workflows.workflow.business.models import Workflow, WorkflowEvent

# Lazy imports to avoid circular dependencies and missing module issues
# Import specific components as needed rather than eagerly loading everything

__all__ = [
    "Workflow",
    "WorkflowEvent",
]


def get_orchestrator():
    """Get the WorkflowOrchestrator class."""
    from workflows.workflow.business.orchestrator import WorkflowOrchestrator
    return WorkflowOrchestrator


def get_executor():
    """Get the BillIntakeExecutor class."""
    from workflows.workflow.business.executor import BillIntakeExecutor
    return BillIntakeExecutor


def get_scheduler():
    """Get the WorkflowScheduler class."""
    from workflows.workflow.business.scheduler import WorkflowScheduler
    return WorkflowScheduler


def get_summary_generator():
    """Get the DailySummaryGenerator class."""
    from workflows.workflow.business.notifications.summary import DailySummaryGenerator
    return DailySummaryGenerator
