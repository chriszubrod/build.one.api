# Python Standard Library Imports
import logging
from typing import Any, Dict, List, Optional

# Local Imports (only from modules that still exist)
from core.workflow.business.definitions.base import WorkflowDefinition
from core.workflow.business.exceptions import WorkflowStepError
from core.workflow.business.models import Workflow
from core.workflow.business.orchestrator import WorkflowOrchestrator
from core.workflow.persistence.repo import WorkflowRepository
from core.workflow_event.persistence.repo import WorkflowEventRepository

logger = logging.getLogger(__name__)

_UNAVAILABLE_MSG = (
    "Async workflow execution (email intake, bill intake, etc.) is not available; "
    "required modules were removed. Use execute_synchronous() for CRUD."
)


class BillIntakeExecutor:
    """
    Stub executor for Bill Intake / email intake workflows.

    The full implementation depended on removed modules (agents, capabilities,
    bill_intake/vendor_compliance definitions). This stub raises a clear error
    if any method is used.
    """

    def __init__(
        self,
        orchestrator: Optional[WorkflowOrchestrator] = None,
        **kwargs: Any,
    ):
        self.orchestrator = orchestrator or WorkflowOrchestrator()
        # Accept but ignore legacy kwargs (capabilities, etc.) so callers don't break

    async def start_from_email(self, **kwargs: Any) -> Workflow:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def process_approval_reply(self, **kwargs: Any) -> Workflow:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def send_reminder(self, **kwargs: Any) -> Workflow:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def run_triage(self, **kwargs: Any) -> Any:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def create_entities(self, **kwargs: Any) -> Any:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def sync_to_qbo(self, **kwargs: Any) -> Any:
        raise RuntimeError(_UNAVAILABLE_MSG)

    async def send_approval_request(self, **kwargs: Any) -> Any:
        raise RuntimeError(_UNAVAILABLE_MSG)
