# Python Standard Library Imports
from typing import List

# Local Imports
from core.workflow.business.definitions.base import WorkflowDefinition, StateDefinition, Transition


# =============================================================================
# Instant Workflow States and Transitions
# =============================================================================
# Minimal state machine for instant (synchronous) workflows.
# These complete in < 1 second and provide audit/logging benefits.

INSTANT_STATES = [
    StateDefinition(name="executing"),
    StateDefinition(name="completed", is_final=True),
    StateDefinition(name="failed", is_final=True),
]

INSTANT_TRANSITIONS = [
    Transition(trigger="complete", source="executing", dest="completed"),
    Transition(trigger="fail", source="executing", dest="failed"),
]


# =============================================================================
# Pre-defined Operations
# =============================================================================

INSTANT_OPERATIONS = ["create", "update", "delete"]


# =============================================================================
# Entity Registry
# =============================================================================
# All services that support instant workflows.
# These map to services in entities/{entity}/business/service.py

SYNCHRONOUS_TASKS = [
    # Core billing entities
    "bill",
    "bill_line_item",
    "bill_line_item_attachment",
    
    # Expense entities
    "expense",
    "expense_line_item",
    "expense_line_item_attachment",
    
    # Bill Credit entities
    "bill_credit",
    "bill_credit_line_item",
    "bill_credit_line_item_attachment",
    
    # Vendor entities
    "vendor",
    "vendor_address",
    "vendor_type",
    
    # Project entities
    "project",
    "project_address",
    
    # Customer and costing
    "customer",
    "cost_code",
    "sub_cost_code",
    "payment_term",
    
    # Organization entities
    "company",
    "organization",
    "module",
    
    # User management
    "user",
    "role",
    "user_role",
    "user_project",
    "role_module",
    "user_module",

    # Attachments
    "attachment",
    "taxpayer",
    "taxpayer_attachment",
    
    # Contact
    "contact",

    # Review workflow
    "review_status",

    # Other
    "integration",
    "contract_labor",

    # Time tracking
    "time_entry",
]


# =============================================================================
# Workflow Definition Factory
# =============================================================================

def get_instant_workflow_definition(entity: str, operation: str) -> WorkflowDefinition:
    """
    Factory for instant workflow definitions.
    
    Creates a lightweight workflow definition for CRUD operations
    that complete synchronously.
    
    Args:
        entity: Entity name (e.g., 'bill', 'vendor', 'project')
        operation: Operation name ('create', 'update', 'delete')
        
    Returns:
        WorkflowDefinition configured for instant execution
        
    Raises:
        ValueError: If entity or operation is not supported
    """
    if entity not in SYNCHRONOUS_TASKS:
        raise ValueError(f"Entity '{entity}' not supported for instant workflows")
    if operation not in INSTANT_OPERATIONS:
        raise ValueError(f"Operation '{operation}' not supported for instant workflows")
    
    return WorkflowDefinition(
        name=f"{entity}_{operation}",
        initial_state="executing",
        states=INSTANT_STATES,
        transitions=INSTANT_TRANSITIONS,
    )


def get_all_instant_workflow_definitions() -> List[WorkflowDefinition]:
    """
    Generate all possible instant workflow definitions.
    
    Returns:
        List of WorkflowDefinition for all entity/operation combinations
    """
    definitions = []
    for entity in SYNCHRONOUS_TASKS:
        for operation in INSTANT_OPERATIONS:
            definitions.append(get_instant_workflow_definition(entity, operation))
    return definitions


def is_instant_workflow_type(workflow_type: str) -> bool:
    """
    Check if a workflow type string represents an instant workflow.
    
    Args:
        workflow_type: Workflow type string (e.g., 'bill_create', 'vendor_update')
        
    Returns:
        True if this is an instant workflow type
    """
    if not workflow_type:
        return False
    
    # Split on last underscore to get entity and operation
    parts = workflow_type.rsplit("_", 1)
    if len(parts) != 2:
        return False
    
    entity, operation = parts
    return entity in SYNCHRONOUS_TASKS and operation in INSTANT_OPERATIONS


def parse_instant_workflow_type(workflow_type: str) -> tuple:
    """
    Parse an instant workflow type into entity and operation.
    
    Args:
        workflow_type: Workflow type string (e.g., 'bill_create')
        
    Returns:
        Tuple of (entity, operation)
        
    Raises:
        ValueError: If workflow_type is not a valid instant workflow type
    """
    if not is_instant_workflow_type(workflow_type):
        raise ValueError(f"'{workflow_type}' is not a valid instant workflow type")
    
    return workflow_type.rsplit("_", 1)
