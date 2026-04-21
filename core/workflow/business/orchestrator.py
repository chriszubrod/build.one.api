# Python Standard Library Imports
import logging
from typing import Any, Optional, Type

# Third-party Imports
from transitions import Machine

# Local Imports
from core.workflow.business.definitions.base import WorkflowDefinition, StepDefinition
from core.workflow.business.exceptions import (
    WorkflowNotFoundError,
    WorkflowStateError,
    WorkflowStepError,
)
from core.workflow.business.models import Workflow, WorkflowEvent
from core.workflow.persistence.repo import WorkflowRepository
from core.workflow_event.persistence.repo import WorkflowEventRepository

logger = logging.getLogger(__name__)


class WorkflowStateMachine:
    """
    A state machine wrapper for a single workflow instance.
    
    Uses the `transitions` library to manage state transitions
    while persisting to the database.
    """
    
    def __init__(
        self,
        workflow: Workflow,
        definition: WorkflowDefinition,
        workflow_repo: WorkflowRepository,
        event_repo: WorkflowEventRepository,
    ):
        self.workflow = workflow
        self.definition = definition
        self.workflow_repo = workflow_repo
        self.event_repo = event_repo
        
        # Build the state machine
        config = definition.to_transitions_config()
        self.machine = Machine(
            model=self,
            states=config["states"],
            initial=workflow.state,
            transitions=config["transitions"],
            auto_transitions=False,
            send_event=True,  # Pass event data to callbacks
        )
    
    @property
    def state(self) -> str:
        """Current state of the workflow."""
        return self.workflow.state
    
    @state.setter
    def state(self, value: str) -> None:
        """Set the workflow state (used by transitions library)."""
        self.workflow.state = value
        # Persist to database
        self.workflow_repo.update_state(self.workflow.public_id, value)
    
    def _log_transition(
        self,
        from_state: str,
        to_state: str,
        trigger: str,
        data: Optional[dict] = None,
        created_by: str = "system",
    ) -> WorkflowEvent:
        """Log a state transition event."""
        return self.event_repo.create(
            workflow_id=self.workflow.id,
            event_type="state_changed",
            from_state=from_state,
            to_state=to_state,
            step_name=trigger,
            data=data,
            created_by=created_by,
        )
    
    def transition_to(
        self,
        trigger: str,
        context_updates: Optional[dict] = None,
        created_by: str = "system",
    ) -> Workflow:
        """
        Trigger a state transition.
        
        Args:
            trigger: The transition trigger name
            context_updates: Optional updates to merge into workflow context
            created_by: Who initiated the transition
            
        Returns:
            Updated workflow instance
            
        Raises:
            WorkflowStateError: If the transition is not valid
        """
        from_state = self.workflow.state
        
        # Check if transition is valid
        trigger_method = getattr(self, trigger, None)
        if trigger_method is None or not callable(trigger_method):
            raise WorkflowStateError(
                f"Invalid trigger '{trigger}' for workflow type '{self.definition.name}'",
                current_state=from_state,
            )
        
        # Prepare new context
        new_context = dict(self.workflow.context or {})
        if context_updates:
            new_context.update(context_updates)
        
        # Add transition history
        history = new_context.get("history", [])
        history.append({
            "trigger": trigger,
            "from_state": from_state,
            "created_by": created_by,
        })
        new_context["history"] = history
        
        try:
            # Execute the transition (updates self.state via transitions library)
            trigger_method()
            to_state = self.workflow.state
            
            # Persist the state change
            updated = self.workflow_repo.update_state(
                public_id=self.workflow.public_id,
                state=to_state,
                context=new_context,
            )
            
            # Log the transition event
            self._log_transition(
                from_state=from_state,
                to_state=to_state,
                trigger=trigger,
                data=context_updates,
                created_by=created_by,
            )
            
            self.workflow = updated
            # Keep Task in sync: update status/title on every transition; close when terminal
            try:
                from entities.tasks.business.service import TaskService
                TaskService().upsert_task_for_workflow(updated)
            except (ModuleNotFoundError, ImportError):
                logger.debug("entities.tasks not available; skipping task upsert for workflow %s", updated.public_id)
            except Exception as e:
                logger.warning("Failed to upsert task for workflow %s: %s", updated.public_id, e)
            return updated
            
        except Exception as e:
            logger.error(f"Transition failed: {e}")
            raise WorkflowStateError(
                f"Transition '{trigger}' failed: {e}",
                current_state=from_state,
                target_state=None,
            )
    
    def can_transition(self, trigger: str) -> bool:
        """Check if a transition is valid from current state."""
        trigger_method = getattr(self, f"may_{trigger}", None)
        if trigger_method and callable(trigger_method):
            return trigger_method()
        return False


class WorkflowOrchestrator:
    """
    Orchestrates workflow execution.
    
    Responsible for:
    - Creating new workflows
    - Loading and resuming existing workflows
    - Executing state transitions
    - Running steps within states
    - Logging events
    """
    
    def __init__(
        self,
        workflow_repo: Optional[WorkflowRepository] = None,
        event_repo: Optional[WorkflowEventRepository] = None,
    ):
        self.workflow_repo = workflow_repo or WorkflowRepository()
        self.event_repo = event_repo or WorkflowEventRepository()
        self._definitions: dict[str, WorkflowDefinition] = {}
    
    def register_definition(self, definition: WorkflowDefinition) -> None:
        """Register a workflow definition."""
        self._definitions[definition.name] = definition
        logger.info(f"Registered workflow definition: {definition.name}")
    
    def get_definition(self, workflow_type: str) -> WorkflowDefinition:
        """Get a registered workflow definition."""
        if workflow_type not in self._definitions:
            raise ValueError(f"Unknown workflow type: {workflow_type}")
        return self._definitions[workflow_type]
    
    def create_workflow(
        self,
        *,
        tenant_id: int,
        workflow_type: str,
        conversation_id: Optional[str] = None,
        trigger_message_id: Optional[str] = None,
        parent_workflow_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        context: Optional[dict] = None,
        created_by: str = "system",
    ) -> Workflow:
        """
        Create a new workflow instance.
        
        Args:
            tenant_id: Tenant ID for multi-tenancy
            workflow_type: Type of workflow (must be registered)
            conversation_id: MS Graph conversation ID for correlation
            trigger_message_id: Message ID that triggered this workflow
            parent_workflow_id: Parent workflow ID for child workflows
            vendor_id: Associated vendor ID
            project_id: Associated project ID
            context: Initial context data
            created_by: Who created the workflow
            
        Returns:
            Created workflow instance
        """
        # Validate workflow type
        definition = self.get_definition(workflow_type)
        
        # Check for duplicate by trigger message ID AND workflow type
        # This allows child workflows to share the same trigger_message_id as parent
        if trigger_message_id:
            existing = self.workflow_repo.read_by_trigger_message_id_and_type(
                trigger_message_id, workflow_type
            )
            if existing:
                logger.warning(
                    f"Workflow already exists for trigger message {trigger_message_id}"
                )
                return (existing, False)  # (workflow, created=False)
        
        # Create the workflow
        workflow = self.workflow_repo.create(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            state=definition.initial_state,
            parent_workflow_id=parent_workflow_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            vendor_id=vendor_id,
            project_id=project_id,
            context=context or {},
        )
        
        # Log creation event
        self.event_repo.create(
            workflow_id=workflow.id,
            event_type="workflow_created",
            to_state=definition.initial_state,
            data={
                "workflow_type": workflow_type,
                "trigger_message_id": trigger_message_id,
                "conversation_id": conversation_id,
            },
            created_by=created_by,
        )
        
        logger.info(
            f"Created workflow {workflow.public_id} of type {workflow_type} "
            f"in state {definition.initial_state}"
        )
        
        return (workflow, True)  # (workflow, created=True)
    
    def load_workflow(self, public_id: str) -> WorkflowStateMachine:
        """
        Load a workflow and return its state machine.
        
        Args:
            public_id: Workflow public ID
            
        Returns:
            WorkflowStateMachine for the workflow
            
        Raises:
            WorkflowNotFoundError: If workflow doesn't exist
        """
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise WorkflowNotFoundError(f"Workflow not found: {public_id}")
        
        definition = self.get_definition(workflow.workflow_type)
        
        return WorkflowStateMachine(
            workflow=workflow,
            definition=definition,
            workflow_repo=self.workflow_repo,
            event_repo=self.event_repo,
        )
    
    def transition(
        self,
        public_id: str,
        trigger: str,
        context_updates: Optional[dict] = None,
        created_by: str = "system",
    ) -> Workflow:
        """
        Trigger a state transition on a workflow.
        
        Args:
            public_id: Workflow public ID
            trigger: Transition trigger name
            context_updates: Optional context updates
            created_by: Who initiated the transition
            
        Returns:
            Updated workflow
        """
        machine = self.load_workflow(public_id)
        return machine.transition_to(
            trigger=trigger,
            context_updates=context_updates,
            created_by=created_by,
        )
    
    def log_step(
        self,
        workflow_id: int,
        step_name: str,
        data: Optional[dict] = None,
        created_by: str = "system",
    ) -> WorkflowEvent:
        """Log a step completion event."""
        return self.event_repo.create(
            workflow_id=workflow_id,
            event_type="step_completed",
            step_name=step_name,
            data=data,
            created_by=created_by,
        )
    
    def log_error(
        self,
        workflow_id: int,
        step_name: str,
        error: Exception,
        data: Optional[dict] = None,
        created_by: str = "system",
    ) -> WorkflowEvent:
        """Log an error event."""
        error_data = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            **(data or {}),
        }
        return self.event_repo.create(
            workflow_id=workflow_id,
            event_type="error",
            step_name=step_name,
            data=error_data,
            created_by=created_by,
        )
    
    def log_human_response(
        self,
        workflow_id: int,
        response_data: dict,
        created_by: str,
    ) -> WorkflowEvent:
        """Log a human response event."""
        return self.event_repo.create(
            workflow_id=workflow_id,
            event_type="human_response",
            data=response_data,
            created_by=created_by,
        )
    
    def get_workflow_history(self, public_id: str) -> list[WorkflowEvent]:
        """Get the event history for a workflow."""
        workflow = self.workflow_repo.read_by_public_id(public_id)
        if not workflow:
            raise WorkflowNotFoundError(f"Workflow not found: {public_id}")
        
        return self.event_repo.read_by_workflow_id(workflow.id)
    
    def get_active_workflows(self, tenant_id: int) -> list[Workflow]:
        """Get all active (non-completed) workflows for a tenant."""
        return self.workflow_repo.read_active_workflows(tenant_id)
    
    def get_workflows_by_state(
        self,
        tenant_id: int,
        state: str,
    ) -> list[Workflow]:
        """Get all workflows in a specific state."""
        return self.workflow_repo.read_by_tenant_and_state(tenant_id, state)
    
    def get_workflows_past_timeout(
        self,
        tenant_id: int,
        state: str,
        timeout_days: int,
    ) -> list[Workflow]:
        """Get workflows that have been in a state longer than timeout."""
        return self.workflow_repo.read_past_timeout(tenant_id, state, timeout_days)
    
    def create_child_workflow(
        self,
        parent: Workflow,
        context: Optional[dict] = None,
        created_by: str = "system",
    ) -> Workflow:
        """
        Create a child workflow from a parent.
        
        Inherits tenant, type, and correlation from parent.
        """
        workflow, _ = self.create_workflow(
            tenant_id=parent.tenant_id,
            workflow_type=parent.workflow_type,
            conversation_id=parent.conversation_id,
            parent_workflow_id=parent.id,
            vendor_id=parent.vendor_id,
            project_id=parent.project_id,
            context=context,
            created_by=created_by,
        )
        return workflow
