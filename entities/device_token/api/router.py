# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from entities.auth.business.service import AuthService, get_current_user_api
from entities.device_token.api.schemas import (
    DeactivateDeviceTokenRequest,
    RegisterDeviceTokenRequest,
)
from entities.device_token.business.service import DeviceTokenService
from shared.api.responses import item_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "device_token"])


def _resolve_user_id(current_user: dict) -> int:
    """
    JWT sub is the Auth row's public_id; the user-scoped tables are keyed
    on User.Id. Same lookup pattern used by `_resolve_me_payload` in the
    auth router.
    """
    sub = current_user.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing subject.")
    auth = AuthService().read_by_public_id(public_id=sub)
    if auth is None or auth.user_id is None:
        raise HTTPException(status_code=404, detail="Auth profile not found.")
    return auth.user_id


@router.post("/mobile/device-token/register")
def register_device_token_router(
    body: RegisterDeviceTokenRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Register an APNs device token for the authenticated user. Idempotent —
    re-registering the same (token, app_bundle_id) updates the existing
    row rather than creating a duplicate. Different users on the same
    physical device are supported by overwriting `UserId`.
    """
    user_id = _resolve_user_id(current_user)

    try:
        record = DeviceTokenService().register(
            user_id=user_id,
            token=body.device_token,
            app_bundle_id=body.app_bundle_id,
        )
    except Exception as e:
        logger.exception("Failed to register device token.")
        raise HTTPException(status_code=500, detail="Failed to register device token.") from e

    return item_response({
        "registered": True,
        "public_id": record.public_id if record else None,
    })


@router.post("/mobile/device-token/deactivate")
def deactivate_device_token_router(
    body: DeactivateDeviceTokenRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Deactivate a device token on logout. Scoped to the calling user so
    user A can't deactivate user B's tokens. Returns `deactivated: true`
    even when no row matched — logout is idempotent and the client
    shouldn't fail on "already gone".
    """
    user_id = _resolve_user_id(current_user)

    try:
        DeviceTokenService().deactivate(
            user_id=user_id,
            token=body.device_token,
        )
    except Exception as e:
        logger.exception("Failed to deactivate device token.")
        raise HTTPException(status_code=500, detail="Failed to deactivate device token.") from e

    return item_response({"deactivated": True})
