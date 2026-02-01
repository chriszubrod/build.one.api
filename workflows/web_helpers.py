# Python Standard Library Imports
from typing import Any, Dict, Optional

# Third-party Imports
from fastapi import Request

# Local Imports
from workflows.router import get_trigger_router, TriggerContext


def get_trigger_context_from_request(
    request: Request,
    current_user: dict,
    form_data: Dict[str, Any],
    workflow_type: str,
) -> TriggerContext:
    """
    Build TriggerContext from a web request.
    
    This helper extracts tenant_id and user_id from the current_user dict
    (populated from JWT token) and builds a TriggerContext suitable for
    routing through the workflow engine.
    
    Args:
        request: FastAPI Request object
        current_user: User dict from get_current_user_web dependency
        form_data: Form field values as a dictionary
        workflow_type: Workflow type string (e.g., "project_create")
        
    Returns:
        TriggerContext configured for web form submission
    """
    router = get_trigger_router()
    return router.from_form_submit(
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        form_data=form_data,
        workflow_type=workflow_type,
        access_token=current_user.get("access_token"),
    )


def get_trigger_context_for_button_click(
    current_user: dict,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> TriggerContext:
    """
    Build TriggerContext for a button click action.
    
    Args:
        current_user: User dict from get_current_user_web dependency
        action: Action name (e.g., "extract", "sync", "approve")
        entity_type: Type of entity being acted on
        entity_id: Public ID of entity
        payload: Additional action data
        
    Returns:
        TriggerContext configured for button click
    """
    router = get_trigger_router()
    return router.from_button_click(
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        access_token=current_user.get("access_token"),
    )


def route_instant_workflow(context: TriggerContext) -> Dict[str, Any]:
    """
    Route an instant workflow and return the result.
    
    Convenience wrapper around TriggerRouter.route_instant().
    
    Args:
        context: TriggerContext for the workflow
        
    Returns:
        Result dict with success status and data/error
    """
    router = get_trigger_router()
    return router.route_instant(context)
