# Python Standard Library Imports
from typing import Optional


class WorkflowError(Exception):
    """Base exception for workflow-related errors."""
    pass


class WorkflowNotFoundError(WorkflowError):
    """Raised when a workflow cannot be found."""
    pass


class WorkflowStateError(WorkflowError):
    """Raised when a workflow state transition is invalid."""
    
    def __init__(
        self,
        message: str,
        current_state: Optional[str] = None,
        target_state: Optional[str] = None
    ):
        super().__init__(message)
        self.current_state = current_state
        self.target_state = target_state


class WorkflowStepError(WorkflowError):
    """Raised when a workflow step fails."""
    
    def __init__(
        self,
        message: str,
        step_name: Optional[str] = None,
        retryable: bool = False
    ):
        super().__init__(message)
        self.step_name = step_name
        self.retryable = retryable


class WorkflowTimeoutError(WorkflowError):
    """Raised when a workflow times out waiting for an event."""
    pass
