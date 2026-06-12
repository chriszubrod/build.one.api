# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.box.auth.business.service import BoxAuthService
from integrations.box.base.client import BoxHttpClient, writes_allowed
from integrations.box.base.errors import BoxError
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["api", "box-auth"])


@router.get("/box/auth/status")
def box_auth_status_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Report Box CCG auth health: which credentials are configured, whether a
    minted token is cached (and for how long), and whether the write gate
    is open. Booleans and timestamps only — never secret material.
    """
    status = BoxAuthService().status()
    status["writes_allowed"] = writes_allowed()
    return status


@router.get("/box/auth/test")
def box_auth_test_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Test the Box API connection by minting a CCG token and calling /users/me.
    Returns the service account's identity if successful — useful both as an
    auth proof and to verify which scopes the app authorization granted.
    """
    try:
        # Explicit mint first so token failures surface as auth errors rather
        # than buried inside the request path; the client reuses the cached token.
        BoxAuthService().ensure_valid_token()
        with BoxHttpClient() as client:
            user_data = client.get("users/me", operation_name="auth.test_connection")
        return {
            "message": "Box API connection successful!",
            "status_code": 200,
            "user": {
                "id": user_data.get("id"),
                "name": user_data.get("name"),
                "login": user_data.get("login"),
            },
        }
    except BoxError as e:
        logger.error(f"Box connection test failed: {e}")
        return {"message": str(e), "status_code": e.http_status or 502}
