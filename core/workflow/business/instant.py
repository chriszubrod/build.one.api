# Python Standard Library Imports
import importlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Local Imports
from core.workflow.business.definitions.instant import (
    SYNCHRONOUS_TASKS,
    INSTANT_OPERATIONS,
    get_instant_workflow_definition,
    is_instant_workflow_type,
    parse_instant_workflow_type,
)
from core.workflow.business.models import Workflow
from core.workflow.business.orchestrator import WorkflowOrchestrator
from core.workflow.api.process_engine import TriggerContext

logger = logging.getLogger(__name__)


# =============================================================================
# Service Registry
# =============================================================================
# Maps entity names to their service module paths.
# All services follow the pattern: entities/{entity}/business/service.py

PROCESS_REGISTRY: Dict[str, str] = {
    # Core billing entities
    "bill": "entities.bill.business.service.BillService",
    "bill_line_item": "entities.bill_line_item.business.service.BillLineItemService",
    "bill_line_item_attachment": "entities.bill_line_item_attachment.business.service.BillLineItemAttachmentService",
    
    # Expense entities
    "expense": "entities.expense.business.service.ExpenseService",
    "expense_line_item": "entities.expense_line_item.business.service.ExpenseLineItemService",
    "expense_line_item_attachment": "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService",
    
    # Bill Credit entities
    "bill_credit": "entities.bill_credit.business.service.BillCreditService",
    "bill_credit_line_item": "entities.bill_credit_line_item.business.service.BillCreditLineItemService",
    "bill_credit_line_item_attachment": "entities.bill_credit_line_item_attachment.business.service.BillCreditLineItemAttachmentService",
    
    # Vendor entities
    "vendor": "entities.vendor.business.service.VendorService",
    "vendor_address": "entities.vendor_address.business.service.VendorAddressService",
    "vendor_type": "entities.vendor_type.business.service.VendorTypeService",
    
    # Project entities
    "project": "entities.project.business.service.ProjectService",
    "project_address": "entities.project_address.business.service.ProjectAddressService",
    
    # Customer and costing
    "customer": "entities.customer.business.service.CustomerService",
    "cost_code": "entities.cost_code.business.service.CostCodeService",
    "sub_cost_code": "entities.sub_cost_code.business.service.SubCostCodeService",
    "payment_term": "entities.payment_term.business.service.PaymentTermService",
    
    # Organization entities
    "company": "entities.company.business.service.CompanyService",
    "organization": "entities.organization.business.service.OrganizationService",
    "module": "entities.module.business.service.ModuleService",
    
    # User management
    "user": "entities.user.business.service.UserService",
    "role": "entities.role.business.service.RoleService",
    "user_role": "entities.user_role.business.service.UserRoleService",
    "user_project": "entities.user_project.business.service.UserProjectService",
    "role_module": "entities.role_module.business.service.RoleModuleService",
    "user_module": "entities.user_module.business.service.UserModuleService",
    
    # Attachments
    "attachment": "entities.attachment.business.service.AttachmentService",
    "taxpayer": "entities.taxpayer.business.service.TaxpayerService",
    "taxpayer_attachment": "entities.taxpayer_attachment.business.service.TaxpayerAttachmentService",
    
    # Contact
    "contact": "entities.contact.business.service.ContactService",

    # Review workflow
    "review_status": "entities.review_status.business.service.ReviewStatusService",

    # Other
    "integration": "entities.integration.business.service.IntegrationService",
    "contract_labor": "entities.contract_labor.business.service.ContractLaborService",

    # Time tracking
    "time_entry": "entities.time_entry.business.service.TimeEntryService",
}


# =============================================================================
# Method Name Mapping
# =============================================================================
# Maps operation names to service method names.

METHOD_MAPPING = {
    "create": "create",
    "update": "update_by_public_id",
    "delete": "delete_by_public_id",
}


# =============================================================================
# Result Dataclass
# =============================================================================

@dataclass
class InstantWorkflowResult:
    """
    Result of an instant workflow execution.
    
    Attributes:
        success: Whether the operation completed successfully
        workflow_id: Public ID of the created workflow record
        data: Result data from the service call (on success)
        error: Error message (on failure)
    """
    success: bool
    workflow_id: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "success": self.success,
            "workflow_id": self.workflow_id,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        return result


# =============================================================================
# Instant Workflow Handler
# =============================================================================

class InstantWorkflowHandler:
    """
    Executes instant workflows for CRUD operations.
    
    Provides workflow benefits (audit trail, logging, state persistence)
    for synchronous operations that complete in < 1 second.
    
    Usage:
        handler = InstantWorkflowHandler()
        result = handler.execute(
            context=trigger_context,
            entity="project",
            operation="create",
            name="New Project",
            description="Project description",
        )
        
        if result.success:
            project = result.data
        else:
            print(f"Error: {result.error}")
    """
    
    def __init__(self, orchestrator: Optional[WorkflowOrchestrator] = None):
        """
        Initialize the handler.
        
        Args:
            orchestrator: WorkflowOrchestrator instance (created if not provided)
        """
        self._orchestrator = orchestrator
        self._definitions_registered = False
    
    @property
    def orchestrator(self) -> WorkflowOrchestrator:
        """Lazy-load the orchestrator."""
        if self._orchestrator is None:
            self._orchestrator = WorkflowOrchestrator()
        
        # Register definitions on first access
        if not self._definitions_registered:
            self._register_definitions()
            self._definitions_registered = True
        
        return self._orchestrator
    
    def _register_definitions(self) -> None:
        """Register all instant workflow definitions with the orchestrator."""
        for entity in SYNCHRONOUS_TASKS:
            for operation in INSTANT_OPERATIONS:
                try:
                    definition = get_instant_workflow_definition(entity, operation)
                    self._orchestrator.register_definition(definition)
                except Exception as e:
                    logger.warning(f"Failed to register {entity}_{operation}: {e}")
    
    def _get_service(self, entity: str):
        """
        Get service instance for an entity.
        
        Args:
            entity: Entity name (e.g., 'bill', 'vendor')
            
        Returns:
            Service instance
            
        Raises:
            ValueError: If entity is not in the registry
        """
        if entity not in PROCESS_REGISTRY:
            raise ValueError(f"No service registered for entity '{entity}'")

        module_path = PROCESS_REGISTRY[entity]
        module_name, class_name = module_path.rsplit(".", 1)
        
        try:
            module = importlib.import_module(module_name)
            service_class = getattr(module, class_name)
            return service_class()
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Failed to load service for '{entity}': {e}")
    
    def _get_method_name(self, operation: str) -> str:
        """
        Get the service method name for an operation.
        
        Args:
            operation: Operation name ('create', 'update', 'delete')
            
        Returns:
            Service method name
        """
        return METHOD_MAPPING.get(operation, operation)
    
    def _serialize_result(self, result: Any) -> Optional[Dict[str, Any]]:
        """
        Serialize a service result to a dictionary.
        
        Args:
            result: Service method return value
            
        Returns:
            Dictionary representation or None
        """
        if result is None:
            return None
        if hasattr(result, 'to_dict'):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return [self._serialize_result(item) for item in result]
        return {"value": str(result)}
    
    def execute(
        self,
        context: TriggerContext,
        entity: str,
        operation: str,
        **kwargs,
    ) -> InstantWorkflowResult:
        """
        Execute an instant workflow.
        
        Steps:
        1. Create workflow record in 'executing' state
        2. Call the appropriate service method
        3. Log the result as an event
        4. Transition to 'completed' or 'failed'
        5. Return result to caller
        
        Args:
            context: TriggerContext with tenant/user info
            entity: Entity name (e.g., 'project', 'vendor')
            operation: Operation name ('create', 'update', 'delete')
            **kwargs: Arguments to pass to the service method
            
        Returns:
            InstantWorkflowResult with success status and data/error
        """
        workflow_type = f"{entity}_{operation}"
        
        logger.info(f"Executing instant workflow: {workflow_type}")
        
        # Validate entity and operation
        if entity not in SYNCHRONOUS_TASKS:
            return InstantWorkflowResult(
                success=False,
                workflow_id="",
                error=f"Entity '{entity}' not supported for instant workflows",
            )
        
        if operation not in INSTANT_OPERATIONS:
            return InstantWorkflowResult(
                success=False,
                workflow_id="",
                error=f"Operation '{operation}' not supported for instant workflows",
            )
        
        # Determine created_by value
        created_by = f"user:{context.user_id}" if context.user_id else "system"
        
        # Create the workflow record
        try:
            workflow, _ = self.orchestrator.create_workflow(
                tenant_id=context.tenant_id,
                workflow_type=workflow_type,
                context={
                    "trigger_type": context.trigger_type.value if hasattr(context.trigger_type, 'value') else str(context.trigger_type),
                    "trigger_source": context.trigger_source.value if hasattr(context.trigger_source, 'value') else str(context.trigger_source),
                    "payload": context.payload,
                    "kwargs": kwargs,
                },
                created_by=created_by,
            )
        except Exception as e:
            logger.error(f"Failed to create workflow record: {e}")
            return InstantWorkflowResult(
                success=False,
                workflow_id="",
                error=f"Failed to create workflow record: {e}",
            )
        
        # Execute the service call
        try:
            service = self._get_service(entity)
            method_name = self._get_method_name(operation)
            method = getattr(service, method_name)
            
            # Inject tenant_id from context into kwargs for tenant isolation
            # Services should accept tenant_id as a keyword argument
            kwargs_with_tenant = {"tenant_id": context.tenant_id, **kwargs}
            
            # Call the service method
            result = method(**kwargs_with_tenant)
            
            # Serialize the result
            serialized_result = self._serialize_result(result)
            
            # Log step completion
            self.orchestrator.log_step(
                workflow_id=workflow.id,
                step_name=f"{entity}_{operation}",
                data={"result": serialized_result},
                created_by="instant_workflow_handler",
            )
            
            # Transition to completed
            self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="complete",
                context_updates={"result": serialized_result},
                created_by="instant_workflow_handler",
            )
            
            logger.info(f"Instant workflow {workflow.public_id} completed successfully")
            
            return InstantWorkflowResult(
                success=True,
                workflow_id=workflow.public_id,
                data=serialized_result,
            )
            
        except Exception as e:
            logger.error(f"Instant workflow {workflow.public_id} failed: {e}")
            
            # Log the error
            self.orchestrator.log_error(
                workflow_id=workflow.id,
                step_name=f"{entity}_{operation}",
                error=e,
                created_by="instant_workflow_handler",
            )
            
            # Transition to failed
            try:
                self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="fail",
                    context_updates={"error": str(e), "error_type": type(e).__name__},
                    created_by="instant_workflow_handler",
                )
            except Exception as transition_error:
                logger.error(f"Failed to transition workflow to failed state: {transition_error}")
            
            return InstantWorkflowResult(
                success=False,
                workflow_id=workflow.public_id,
                error=str(e),
            )
    
    def execute_from_workflow_type(
        self,
        context: TriggerContext,
        workflow_type: str,
        **kwargs,
    ) -> InstantWorkflowResult:
        """
        Execute an instant workflow from a workflow_type string.
        
        Convenience method that parses the workflow_type into entity and operation.
        
        Args:
            context: TriggerContext with tenant/user info
            workflow_type: Workflow type string (e.g., 'project_create')
            **kwargs: Arguments to pass to the service method
            
        Returns:
            InstantWorkflowResult with success status and data/error
        """
        if not is_instant_workflow_type(workflow_type):
            return InstantWorkflowResult(
                success=False,
                workflow_id="",
                error=f"'{workflow_type}' is not a valid instant workflow type",
            )
        
        entity, operation = parse_instant_workflow_type(workflow_type)
        return self.execute(context, entity, operation, **kwargs)
