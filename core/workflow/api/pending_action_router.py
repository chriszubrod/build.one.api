# Python Standard Library Imports
from typing import Any, Dict, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# Local Imports
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.business.execute_pending_action import execute_pending_action

router = APIRouter(prefix="/api/v1", tags=["api", "workflow", "pending_action"])


class PendingActionExecuteRequest(BaseModel):
    """Request to approve or reject a pending action."""
    action_id: str = Field(..., description="Id of the pending action")
    type: str = Field(..., description="Action type: update_taxpayer, backfill_w9, etc.")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Action payload")
    decision: str = Field(..., description="approve or reject")


@router.post("/execute/pending-action")
def execute_pending_action_router(
    body: PendingActionExecuteRequest,
    current_user: dict = Depends(require_module_api(Modules.PENDING_ACTIONS, "can_create")),
):
    """
    Execute or reject a pending action (approval gate).
    Call with the pending action as returned by an agent (id, type, payload) and decision=approve|reject.
    """
    if body.decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="decision must be 'approve' or 'reject'",
        )
    result = execute_pending_action(
        action_type=body.type,
        payload=body.payload,
        decision=body.decision,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Execution failed"),
        )
    return {
        "executed": result.get("executed", False),
        "result": result.get("result"),
        "message": result.get("message"),
    }
