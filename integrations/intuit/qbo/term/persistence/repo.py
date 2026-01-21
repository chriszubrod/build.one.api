# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.term.business.model import QboTerm
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboTermRepository:
    """
    Repository for QboTerm persistence operations.
    """

    def __init__(self):
        """Initialize the QboTermRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboTerm]:
        """
        Convert a database row into a QboTerm dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboTerm(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                name=getattr(row, "Name", None),
                discount_percent=Decimal(str(getattr(row, "DiscountPercent"))) if getattr(row, "DiscountPercent", None) is not None else None,
                discount_days=getattr(row, "DiscountDays", None),
                active=getattr(row, "Active", None),
                type=getattr(row, "Type", None),
                day_of_month_due=getattr(row, "DayOfMonthDue", None),
                discount_day_of_month=getattr(row, "DiscountDayOfMonth", None),
                due_next_month_days=getattr(row, "DueNextMonthDays", None),
                due_days=getattr(row, "DueDays", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo term mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo term mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        discount_percent: Optional[Decimal],
        discount_days: Optional[int],
        active: Optional[bool],
        type: Optional[str],
        day_of_month_due: Optional[int],
        discount_day_of_month: Optional[int],
        due_next_month_days: Optional[int],
        due_days: Optional[int],
    ) -> QboTerm:
        """
        Create a new QboTerm.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    # Prepare parameters, ensuring BIT types are handled correctly
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "Name": name,
                        "DiscountPercent": float(discount_percent) if discount_percent is not None else None,
                        "DiscountDays": discount_days,
                        "Active": 1 if active is True else (0 if active is False else None),
                        "Type": type,
                        "DayOfMonthDue": day_of_month_due,
                        "DiscountDayOfMonth": discount_day_of_month,
                        "DueNextMonthDays": due_next_month_days,
                        "DueDays": due_days,
                    }
                    logger.debug(f"Calling CreateQboTerm with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboTerm",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo term did not return a row.")
                        raise map_database_error(Exception("create qbo term failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo term: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboTerm]:
        """
        Read all QboTerms.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboTerms",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all qbo terms: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboTerm]:
        """
        Read all QboTerms by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboTermsByRealmId",
                        params={"RealmId": realm_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo terms by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboTerm]:
        """
        Read a QboTerm by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboTermById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo term by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboTerm]:
        """
        Read a QboTerm by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboTermByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo term by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboTerm]:
        """
        Read a QboTerm by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboTermByQboIdAndRealmId",
                        params={"QboId": qbo_id, "RealmId": realm_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo term by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        discount_percent: Optional[Decimal],
        discount_days: Optional[int],
        active: Optional[bool],
        type: Optional[str],
        day_of_month_due: Optional[int],
        discount_day_of_month: Optional[int],
        due_next_month_days: Optional[int],
        due_days: Optional[int],
    ) -> Optional[QboTerm]:
        """
        Update a QboTerm by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    # Prepare parameters, ensuring BIT types are handled correctly
                    params = {
                        "QboId": qbo_id,
                        "RowVersion": row_version,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "Name": name,
                        "DiscountPercent": float(discount_percent) if discount_percent is not None else None,
                        "DiscountDays": discount_days,
                        "Active": 1 if active is True else (0 if active is False else None),
                        "Type": type,
                        "DayOfMonthDue": day_of_month_due,
                        "DiscountDayOfMonth": discount_day_of_month,
                        "DueNextMonthDays": due_next_month_days,
                        "DueDays": due_days,
                    }
                    logger.debug(f"Calling UpdateQboTermByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboTermByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo term did not return a row.")
                        raise map_database_error(Exception("update qbo term by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo term by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboTerm]:
        """
        Delete a QboTerm by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboTermByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo term by QBO ID: {error}")
            raise map_database_error(error)
