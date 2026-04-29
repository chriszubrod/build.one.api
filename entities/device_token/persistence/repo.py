# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.device_token.business.model import DeviceToken
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DeviceTokenRepository:
    """
    Repository for DeviceToken persistence. Holds APNs (and future FCM)
    push tokens registered against a user. One row per (token, app_bundle_id)
    pair — re-registration UPDATEs in place.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DeviceToken]:
        if not row:
            return None
        try:
            return DeviceToken(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                deactivated_datetime=row.DeactivatedDatetime,
                user_id=row.UserId,
                token=row.Token,
                app_bundle_id=row.AppBundleId,
                platform=row.Platform,
                is_active=bool(row.IsActive),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during device token mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during device token mapping: {error}")
            raise map_database_error(error)

    def register(
        self,
        *,
        user_id: int,
        token: str,
        app_bundle_id: str,
        platform: str = "ios",
    ) -> Optional[DeviceToken]:
        """
        Upsert a device token. Returns the resulting row.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RegisterDeviceToken",
                    params={
                        "UserId": user_id,
                        "Token": token,
                        "AppBundleId": app_bundle_id,
                        "Platform": platform,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during register device token: {error}")
            raise map_database_error(error)

    def deactivate(
        self,
        *,
        user_id: int,
        token: str,
    ) -> Optional[DeviceToken]:
        """
        Deactivate a device token owned by `user_id`. Returns the row when
        a match was found, else None (caller can decide whether to treat
        that as success — usually yes, since logout may be retried).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeactivateDeviceToken",
                    params={
                        "UserId": user_id,
                        "Token": token,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during deactivate device token: {error}")
            raise map_database_error(error)

    def read_active_by_user_id(self, user_id: int) -> list[DeviceToken]:
        """
        All active tokens for a user. Used by the future push sender.
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
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read active device tokens: {error}")
            raise map_database_error(error)
