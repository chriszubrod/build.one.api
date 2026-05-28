# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ReviewInboxRepository:
    """
    Calls the cross-entity inbox sprocs that drive the Task module.

    Kept separate from `ReviewRepository` because these sprocs read across
    Bill / Expense / BillCredit / Invoice + UserProject — outside the
    audit-log read/write scope of regular Review CRUD.
    """

    def read_inbox(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
        scope: str,
        entity_type: Optional[str],
        status_public_id: Optional[str],
        page: int,
        page_size: int,
    ) -> list[pyodbc.Row]:
        params = {
            "CurrentUserId":  int(current_user_id),
            "IsSystemAdmin":  1 if is_system_admin else 0,
            "Scope":          scope,
            "EntityType":     entity_type,
            "StatusPublicId": status_public_id,
            "Page":           int(page),
            "PageSize":       int(page_size),
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInboxTasks", params=params)
                return cursor.fetchall()
        except Exception as error:
            logger.error("Error during ReadInboxTasks: %s", error)
            raise map_database_error(error)

    def read_inbox_counts(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
    ) -> list[pyodbc.Row]:
        params = {
            "CurrentUserId": int(current_user_id),
            "IsSystemAdmin": 1 if is_system_admin else 0,
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInboxTaskCounts", params=params)
                return cursor.fetchall()
        except Exception as error:
            logger.error("Error during ReadInboxTaskCounts: %s", error)
            raise map_database_error(error)


def _decimal_or_none(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
