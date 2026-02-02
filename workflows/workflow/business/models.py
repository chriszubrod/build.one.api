# Python Standard Library Imports
import base64
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Workflow:
    """
    Model representing a workflow instance.
    
    A workflow tracks the execution of a multi-step business process,
    such as bill intake from email.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    tenant_id: Optional[int] = None
    
    # Type and state
    workflow_type: Optional[str] = None  # 'bill_intake', 'payment_inquiry'
    state: Optional[str] = None  # 'received', 'awaiting_approval', etc.
    
    # Parent/child relationship
    parent_workflow_id: Optional[int] = None
    
    # Correlation keys
    conversation_id: Optional[str] = None  # MS Graph conversation ID
    trigger_message_id: Optional[str] = None  # Original email message ID
    
    # Queryable entity references
    vendor_id: Optional[int] = None
    project_id: Optional[int] = None
    bill_id: Optional[int] = None
    
    # Flexible context (stored as JSON)
    context: Optional[dict] = field(default_factory=dict)
    
    # Audit
    created_by: Optional[str] = None
    
    # Timestamps
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    completed_datetime: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None
    
    @property
    def is_completed(self) -> bool:
        return self.completed_datetime is not None
    
    @property
    def is_active(self) -> bool:
        return self.state not in ('completed', 'abandoned', 'cancelled')

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Get a value from the context dict."""
        if self.context is None:
            return default
        return self.context.get(key, default)
    
    def set_context_value(self, key: str, value: Any) -> None:
        """Set a value in the context dict."""
        if self.context is None:
            self.context = {}
        self.context[key] = value


@dataclass
class WorkflowEvent:
    """
    Model representing a workflow event (audit trail entry).
    
    Every state transition, step completion, error, and human response
    is logged as an event.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    workflow_id: Optional[int] = None
    
    # Event details
    event_type: Optional[str] = None  # 'state_changed', 'step_completed', 'error', 'human_response'
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    step_name: Optional[str] = None
    
    # Event data (stored as JSON)
    data: Optional[dict] = field(default_factory=dict)
    
    # Timestamps
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    
    # Audit
    created_by: Optional[str] = None  # 'system', user email, 'agent:email_triage'

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
