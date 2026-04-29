# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.device_token.business.model import DeviceToken
from entities.device_token.persistence.repo import DeviceTokenRepository

logger = logging.getLogger(__name__)


class DeviceTokenService:
    """
    Business layer for device token registration / deactivation. Thin
    wrapper around the repository — there's no workflow, no audit trail
    routing, and no module RBAC. Authentication is enforced at the
    router level via `get_current_user_api`.
    """

    def __init__(self):
        self.repo = DeviceTokenRepository()

    def register(
        self,
        *,
        user_id: int,
        token: str,
        app_bundle_id: str,
        platform: str = "ios",
    ) -> Optional[DeviceToken]:
        return self.repo.register(
            user_id=user_id,
            token=token,
            app_bundle_id=app_bundle_id,
            platform=platform,
        )

    def deactivate(
        self,
        *,
        user_id: int,
        token: str,
    ) -> Optional[DeviceToken]:
        return self.repo.deactivate(user_id=user_id, token=token)

    def read_active_by_user_id(self, user_id: int) -> list[DeviceToken]:
        return self.repo.read_active_by_user_id(user_id=user_id)
