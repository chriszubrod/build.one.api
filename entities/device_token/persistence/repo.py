# entities/device_token/persistence/repo.py

from __future__ import annotations

import logging
import uuid
from typing import Optional

from entities.device_token.business.model import DeviceToken
from shared.database import get_connection, call_procedure, map_database_error

logger = logging.getLogger(__name__)


class DeviceTokenRepository:

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _from_db(self, row) -> DeviceToken:
        return DeviceToken(
            id=                 getattr(row, "Id",                  None),
            public_id=          getattr(row, "PublicId",            None),
            row_version=        getattr(row, "RowVersion",          None),
            created_datetime=   getattr(row, "CreatedDatetime",     None),
            updated_datetime=   getattr(row, "UpdatedDatetime",     None),
            user_id=            getattr(row, "UserId",              None),
            device_token=       getattr(row, "DeviceToken",         None),
            device_type=        getattr(row, "DeviceType",          None),
            app_bundle_id=      getattr(row, "AppBundleId",         None),
            is_active=          bool(row.IsActive) if getattr(row, "IsActive", None) is not None else None,
            last_seen_datetime= getattr(row, "LastSeenDatetime",    None),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        user_id:        int,
        device_token:   str,
        app_bundle_id:  str,
        device_type:    str = "ios",
    ) -> DeviceToken:
        """
        Register or re-activate a device token for a user.
        MERGE on DeviceToken value — handles reinstalls and device transfers.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertDeviceToken",
                    params={
                        "PublicId":     str(uuid.uuid4()),
                        "UserId":       user_id,
                        "DeviceToken":  device_token,
                        "DeviceType":   device_type,
                        "AppBundleId":  app_bundle_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during upsert device token: {error}")
            raise map_database_error(error)

    def deactivate(self, device_token: str) -> Optional[DeviceToken]:
        """Deactivate a specific token on logout."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeactivateDeviceToken",
                    params={"DeviceToken": device_token},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during deactivate device token: {error}")
            raise map_database_error(error)

    def deactivate_all_for_user(self, user_id: int) -> int:
        """
        Deactivate all tokens for a user on full account logout.
        Returns count of deactivated tokens.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeactivateAllDeviceTokensByUserId",
                    params={"UserId": user_id},
                )
                row = cursor.fetchone()
                return int(row.DeactivatedCount) if row else 0
        except Exception as error:
            logger.error(f"Error during deactivate all device tokens: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_active_by_user_id(self, user_id: int) -> list[DeviceToken]:
        """
        Return all active tokens for a user.
        Called by the push notification service when sending to a user.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadActiveDeviceTokensByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows]
        except Exception as error:
            logger.error(f"Error during read active device tokens by user id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[DeviceToken]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadDeviceTokenByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read device token by public id: {error}")
            raise map_database_error(error)
