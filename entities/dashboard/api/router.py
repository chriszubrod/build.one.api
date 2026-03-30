# Python Standard Library Imports
from typing import List

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "dashboard"])


@router.get("/dashboard/summary")
def get_user_summary(current_user: dict = Depends(require_module_api(Modules.DASHBOARD))):
    """
    Get personalized summary for the current user.
    
    TODO: Implement user-specific data:
    - Tasks assigned to user
    - Projects user is involved with
    - Recent activity
    """
    return {
        "message": "User dashboard coming soon",
        "user_id": current_user.get("id"),
    }
