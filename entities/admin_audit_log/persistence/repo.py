# Python Standard Library Imports
import json
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.admin_audit_log.business.model import AdminAuditLog
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AdminAuditLogRepository:
    """Repository for AdminAuditLog persistence operations."""

    def _parse_detail(self, raw: Optional[str]) -> Optional[dict]:
        if raw is None or raw == "":
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            logger.warning("AdminAuditLog detail is not valid JSON")
            return None

    def _from_db(self, row: pyodbc.Row) -> AdminAuditLog:
        try:
            return AdminAuditLog(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId is not None else None,
                created_datetime=row.CreatedDatetime,
                actor_user_id=row.ActorUserId,
                actor_is_system_admin=bool(row.ActorIsSystemAdmin),
                action=row.Action,
                target_user_id=row.TargetUserId,
                detail=self._parse_detail(getattr(row, "Detail", None)),
            )
        except Exception as error:
            logger.error(f"Error during admin audit log mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        actor_user_id: Optional[int],
        actor_is_system_admin: bool,
        action: str,
        target_user_id: Optional[int],
        detail: Optional[str],
    ) -> AdminAuditLog:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAdminAuditLog",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": actor_is_system_admin,
                        "Action": action,
                        "TargetUserId": target_user_id,
                        "Detail": detail,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateAdminAuditLog did not return a row.")
                    raise map_database_error(Exception("CreateAdminAuditLog failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create admin audit log: {error}")
            raise map_database_error(error)

    def _read_list(self, *, name: str, params: dict) -> list[AdminAuditLog]:
        """Shared read envelope: connect, call the sproc, map every row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name=name, params=params)
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read admin audit log ({name}): {error}")
            raise map_database_error(error)

    def read_by_target_user_id(self, user_id: int, limit: int) -> list[AdminAuditLog]:
        return self._read_list(
            name="ReadAdminAuditLogByTargetUserId",
            params={"TargetUserId": user_id, "Limit": limit},
        )

    def read_recent(self, limit: int) -> list[AdminAuditLog]:
        return self._read_list(
            name="ReadAdminAuditLogRecent",
            params={"Limit": limit},
        )
