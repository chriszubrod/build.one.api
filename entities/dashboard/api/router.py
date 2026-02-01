# Python Standard Library Imports
from typing import List

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "dashboard"])


@router.get("/dashboard/summary")
def get_user_summary(current_user: dict = Depends(get_current_user_api)):
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
