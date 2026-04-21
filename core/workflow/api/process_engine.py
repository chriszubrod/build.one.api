# Python Standard Library Imports
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# =============================================================================
# Event Types and Channels
# =============================================================================

class EventType(str, Enum):
    """Types of events that can initiate a workflow."""

    # User-initiated
    API_CALL        = "API_CALL"        # REST API call from any client
    FORM_SUBMIT     = "FORM_SUBMIT"     # Web UI form submission
    USER_ACTION     = "USER_ACTION"     # Button, tap, or explicit user trigger
    MANUAL          = "MANUAL"          # Human-initiated outside normal flow

    # Channel-driven
    EMAIL_RECEIVED  = "EMAIL_RECEIVED"  # Inbound email, new or reply
    FILE_DROP       = "FILE_DROP"       # File uploaded or dropped (SharePoint/blob)
    WEBHOOK         = "WEBHOOK"         # External system callback (QBO, etc.)

    # System-driven
    SCHEDULED       = "SCHEDULED"       # APScheduler triggered
    SYSTEM_EVENT    = "SYSTEM_EVENT"    # Internal cross-component signal
    SLA_BREACH      = "SLA_BREACH"      # Stage exceeded max allowed time


class Channel(str, Enum):
    """Source/entry point of a trigger."""
    
    WEB = "web"            # Browser-based interaction
    API = "api"            # REST API call
    SCHEDULER = "scheduler"  # Background job/polling
    WEBHOOK = "webhook"    # External callback
    CLI = "cli"            # Command-line/script


# =============================================================================
# Attachment Model
# =============================================================================

@dataclass
class TriggerAttachment:
    """Normalized attachment metadata."""
    
    filename: str
    content_type: str
    size: Optional[int] = None
    
    # Source-specific identifiers
    blob_url: Optional[str] = None        # Azure Blob URL if already uploaded
    message_id: Optional[str] = None      # Email message ID if from email
    attachment_id: Optional[str] = None   # Email attachment ID
    
    # Content (for small files or in-memory)
    content: Optional[bytes] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "blob_url": self.blob_url,
            "message_id": self.message_id,
            "attachment_id": self.attachment_id,
        }


# =============================================================================
# Trigger Context
# =============================================================================

@dataclass
class TriggerContext:
    """
    Normalized context for all workflow triggers.
    
    This is the single entry point data structure for all operations,
    regardless of whether they come from web forms, API calls, email polling,
    or webhooks.
    """
    
    # Source identification
    trigger_type: EventType
    trigger_source: Channel
    
    # Tenant/user context
    tenant_id: int
    user_id: Optional[int] = None          # None for system triggers
    access_token: Optional[str] = None     # MS Graph or other service token
    
    # Payload
    payload: Dict[str, Any] = field(default_factory=dict)
    attachments: List[TriggerAttachment] = field(default_factory=list)
    
    # Execution mode
    expects_response: bool = True          # Sync (wait) vs async (fire-and-forget)
    workflow_type: Optional[str] = None    # Explicit workflow type, or inferred
    
    # Correlation
    conversation_id: Optional[str] = None      # Email thread ID
    parent_workflow_id: Optional[str] = None   # For child workflows
    correlation_id: Optional[str] = None       # For tracing/logging
    
    # Timestamps
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    
    def __post_init__(self):
        """Generate correlation_id if not provided."""
        if self.correlation_id is None:
            self.correlation_id = str(uuid.uuid4())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "trigger_type": self.trigger_type.value if isinstance(self.trigger_type, EventType) else self.trigger_type,
            "trigger_source": self.trigger_source.value if isinstance(self.trigger_source, Channel) else self.trigger_source,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "payload": self.payload,
            "attachments": [a.to_dict() for a in self.attachments],
            "expects_response": self.expects_response,
            "workflow_type": self.workflow_type,
            "conversation_id": self.conversation_id,
            "parent_workflow_id": self.parent_workflow_id,
            "correlation_id": self.correlation_id,
            "triggered_at": self.triggered_at.isoformat(),
        }
    
    def with_workflow_type(self, workflow_type: str) -> "TriggerContext":
        """Return a copy with the specified workflow type."""
        return TriggerContext(
            trigger_type=self.trigger_type,
            trigger_source=self.trigger_source,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            access_token=self.access_token,
            payload=self.payload,
            attachments=self.attachments,
            expects_response=self.expects_response,
            workflow_type=workflow_type,
            conversation_id=self.conversation_id,
            parent_workflow_id=self.parent_workflow_id,
            correlation_id=self.correlation_id,
            triggered_at=self.triggered_at,
        )


# =============================================================================
# Process Engine
# =============================================================================

class ProcessEngine:
    """
    Routes triggers from all entry points to the workflow engine.

    Responsibilities:
    1. Normalize inputs from different sources into TriggerContext
    2. Determine workflow type from trigger type + payload (if not specified)
    3. Route to workflow engine - create workflow and return handle
    4. Handle sync vs async - wait for result or return workflow ID

    Supports two types of workflows:
    - Instant workflows: Synchronous CRUD operations (< 1 second)
    - Long-running workflows: Multi-step async processes (email intake, approvals)
    """
    
    def __init__(self):
        # Lazy import to avoid circular dependencies
        self._orchestrator = None
        self._instant_handler = None
    
    @property
    def orchestrator(self):
        """Lazy-load the workflow orchestrator."""
        if self._orchestrator is None:
            from core.workflow.business.orchestrator import WorkflowOrchestrator
            self._orchestrator = WorkflowOrchestrator()
        return self._orchestrator

    @property
    def executor(self):
        """Async workflow execution was removed with the AI layer."""
        raise RuntimeError(
            "Async workflow execution (email intake, bill intake, etc.) is not available; "
            "required modules were removed. Use execute_synchronous() for CRUD."
        )

    @property
    def instant_handler(self):
        """Lazy-load the instant workflow handler."""
        if self._instant_handler is None:
            from core.workflow.business.instant import InstantWorkflowHandler
            self._instant_handler = InstantWorkflowHandler()
        return self._instant_handler
    
    # =========================================================================
    # Factory Methods - Create TriggerContext from various sources
    # =========================================================================
    
    def from_form_submit(
        self,
        tenant_id: int,
        user_id: int,
        form_data: Dict[str, Any],
        workflow_type: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> TriggerContext:
        """
        Create TriggerContext from a web form submission.
        
        Args:
            tenant_id: Tenant ID
            user_id: User who submitted the form
            form_data: Form field values
            workflow_type: Explicit workflow type (e.g., "bill_create")
            access_token: Optional access token for downstream API calls
        """
        return TriggerContext(
            trigger_type=EventType.FORM_SUBMIT,
            trigger_source=Channel.WEB,
            tenant_id=tenant_id,
            user_id=user_id,
            access_token=access_token,
            payload=form_data,
            expects_response=True,
            workflow_type=workflow_type,
        )
    
    def from_file_drop(
        self,
        tenant_id: int,
        user_id: int,
        attachments: List[TriggerAttachment],
        context_data: Optional[Dict[str, Any]] = None,
        workflow_type: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> TriggerContext:
        """
        Create TriggerContext from a file drag-and-drop.
        
        Args:
            tenant_id: Tenant ID
            user_id: User who dropped the file
            attachments: List of dropped files
            context_data: Additional context (e.g., which form/entity)
            workflow_type: Explicit workflow type (e.g., "document_extract")
            access_token: Optional access token
        """
        return TriggerContext(
            trigger_type=EventType.FILE_DROP,
            trigger_source=Channel.WEB,
            tenant_id=tenant_id,
            user_id=user_id,
            access_token=access_token,
            payload=context_data or {},
            attachments=attachments,
            expects_response=True,
            workflow_type=workflow_type,
        )
    
    def from_button_click(
        self,
        tenant_id: int,
        user_id: int,
        action: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
    ) -> TriggerContext:
        """
        Create TriggerContext from a button click action.
        
        Args:
            tenant_id: Tenant ID
            user_id: User who clicked the button
            action: Action name (e.g., "extract", "sync", "approve")
            entity_type: Type of entity being acted on
            entity_id: Public ID of entity
            payload: Additional action data
            access_token: Optional access token
        """
        return TriggerContext(
            trigger_type=EventType.USER_ACTION,
            trigger_source=Channel.WEB,
            tenant_id=tenant_id,
            user_id=user_id,
            access_token=access_token,
            payload={
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                **(payload or {}),
            },
            expects_response=True,
            workflow_type=f"{entity_type}_{action}" if entity_type else action,
        )
    
    def from_api_call(
        self,
        tenant_id: int,
        endpoint: str,
        method: str,
        body: Dict[str, Any],
        user_id: Optional[int] = None,
        access_token: Optional[str] = None,
        expects_response: bool = True,
    ) -> TriggerContext:
        """
        Create TriggerContext from a programmatic API call.
        
        Args:
            tenant_id: Tenant ID
            endpoint: API endpoint path
            method: HTTP method
            body: Request body
            user_id: Optional user ID (may be service account)
            access_token: Optional access token
            expects_response: Whether caller waits for result
        """
        return TriggerContext(
            trigger_type=EventType.API_CALL,
            trigger_source=Channel.API,
            tenant_id=tenant_id,
            user_id=user_id,
            access_token=access_token,
            payload={
                "endpoint": endpoint,
                "method": method,
                "body": body,
            },
            expects_response=expects_response,
        )
    
    def from_webhook(
        self,
        tenant_id: int,
        source_system: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> TriggerContext:
        """
        Create TriggerContext from an external webhook.
        
        Args:
            tenant_id: Tenant ID
            source_system: External system (e.g., "qbo", "sharepoint")
            event_type: Event type (e.g., "bill.created", "file.modified")
            payload: Webhook payload
        """
        return TriggerContext(
            trigger_type=EventType.WEBHOOK,
            trigger_source=Channel.WEBHOOK,
            tenant_id=tenant_id,
            user_id=None,
            payload={
                "source_system": source_system,
                "event_type": event_type,
                "webhook_payload": payload,
            },
            expects_response=False,
        )
    
    # =========================================================================
    # Routing
    # =========================================================================
    
    async def route(self, context: TriggerContext) -> Dict[str, Any]:
        """
        Route a trigger to the appropriate workflow handler.
        
        Args:
            context: Normalized trigger context
            
        Returns:
            For sync (expects_response=True): Operation result
            For async (expects_response=False): {"workflow_id": "...", "status": "started"}
        """
        workflow_type = context.workflow_type or self._infer_workflow_type(context)
        print(f"[ProcessEngine] route trigger_type={context.trigger_type.value} workflow_type={workflow_type} correlation_id={context.correlation_id}")
        logger.info(
            f"Routing trigger: type={context.trigger_type.value}, "
            f"source={context.trigger_source.value}, "
            f"workflow_type={workflow_type}, "
            f"correlation_id={context.correlation_id}"
        )
        
        # Route based on workflow type
        handler = self._get_handler(workflow_type)
        
        if handler is None:
            print(f"[ProcessEngine] no handler for workflow_type={workflow_type}")
            logger.warning(f"No handler for workflow type: {workflow_type}")
            return {
                "success": False,
                "error": f"Unknown workflow type: {workflow_type}",
                "correlation_id": context.correlation_id,
            }
        
        try:
            result = await handler(context)
            return {
                "success": True,
                "correlation_id": context.correlation_id,
                **result,
            }
        except Exception as e:
            logger.exception(f"Error routing trigger: {e}")
            return {
                "success": False,
                "error": str(e),
                "correlation_id": context.correlation_id,
            }
    
    def _infer_workflow_type(self, context: TriggerContext) -> str:
        """
        Infer workflow type from trigger type and payload.
        
        This is a fallback when workflow_type is not explicitly set.
        """
        # EMAIL_RECEIVED covers both new inbound emails and replies;
        # is_reply (from email headers) determines the branch.
        if context.trigger_type == EventType.EMAIL_RECEIVED:
            if context.metadata.get("is_reply"):
                return "approval_response_workflow"
            return "new_email_workflow"

        # Timeout checks trigger reminders
        if context.trigger_type == EventType.SLA_BREACH:
            return "timeout_handler"

        # Button clicks use the action from payload
        if context.trigger_type == EventType.USER_ACTION:
            action = context.payload.get("action", "unknown")
            entity_type = context.payload.get("entity_type", "")
            if entity_type:
                return f"{entity_type}_{action}"
            return action
        
        # Form submissions - check payload for entity type
        if context.trigger_type == EventType.FORM_SUBMIT:
            entity_type = context.payload.get("entity_type")
            if entity_type:
                return f"{entity_type}_create"
        
        # Default: use trigger type as workflow type
        return context.trigger_type.value
    
    def _is_instant_workflow(self, workflow_type: str) -> bool:
        """
        Check if a workflow type is an instant workflow.
        
        Instant workflows are synchronous CRUD operations that complete
        in < 1 second (e.g., 'project_create', 'vendor_update').
        """
        from core.workflow.business.definitions.instant import is_instant_workflow_type
        return is_instant_workflow_type(workflow_type)
    
    def _get_handler(self, workflow_type: str):
        """
        Get the handler function for a workflow type.
        
        Returns an async callable or None if no handler found.
        
        Priority:
        1. Check if it's an instant workflow (CRUD operations)
        2. Check registered long-running workflow handlers
        """
        if self._is_instant_workflow(workflow_type):
            return self._handle_instant_workflow

        return None

    async def _handle_instant_workflow(self, context: TriggerContext) -> Dict[str, Any]:
        """
        Handle instant workflow (synchronous CRUD operations).
        
        Instant workflows complete in < 1 second and provide audit/logging
        benefits for simple operations like create, update, delete.
        
        Args:
            context: TriggerContext with workflow_type like 'project_create'
            
        Returns:
            Result dict with success status and data/error
        """
        from core.workflow.business.definitions.instant import parse_instant_workflow_type
        
        workflow_type = context.workflow_type
        
        # Parse entity and operation from workflow_type
        try:
            entity, operation = parse_instant_workflow_type(workflow_type)
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
            }
        
        # Execute the instant workflow
        result = self.instant_handler.execute(
            context=context,
            entity=entity,
            operation=operation,
            **context.payload,
        )
        
        return {
            "workflow_id": result.workflow_id,
            "workflow_type": workflow_type,
            "state": "completed" if result.success else "failed",
            "data": result.data,
            "error": result.error,
        }


    # =========================================================================
    # Synchronous Routing (for instant workflows)
    # =========================================================================
    
    def execute_synchronous(self, context: TriggerContext) -> Dict[str, Any]:
        """
        Synchronously route an instant workflow.

        This is a convenience method for callers that don't need async.
        Only works for instant workflows (CRUD operations).

        Args:
            context: TriggerContext with workflow_type like 'project_create'

        Returns:
            Result dict with success status and data/error

        Raises:
            ValueError: If workflow_type is not an instant workflow
        """
        workflow_type = context.workflow_type or self._infer_workflow_type(context)

        if not self._is_instant_workflow(workflow_type):
            raise ValueError(
                f"execute_synchronous() only supports instant workflows. "
                f"'{workflow_type}' is not an instant workflow. Use route() instead."
            )
        
        from core.workflow.business.definitions.instant import parse_instant_workflow_type
        
        entity, operation = parse_instant_workflow_type(workflow_type)
        
        result = self.instant_handler.execute(
            context=context,
            entity=entity,
            operation=operation,
            **context.payload,
        )
        
        return {
            "success": result.success,
            "workflow_id": result.workflow_id,
            "workflow_type": workflow_type,
            "data": result.data,
            "error": result.error,
        }


# =============================================================================
# Module-level convenience
# =============================================================================

_engine_instance: Optional[ProcessEngine] = None


def get_process_engine() -> ProcessEngine:
    """Get or create the singleton ProcessEngine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ProcessEngine()
    return _engine_instance
