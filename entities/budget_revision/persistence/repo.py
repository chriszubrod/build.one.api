# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.budget_revision.business.model import BudgetRevision
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class BudgetRevisionRepository:
    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BudgetRevision]:
        if not row:
            return None
        try:
            return BudgetRevision(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                budget_id=row.BudgetId,
                revision_number=row.RevisionNumber,
                type=row.Type,
                status=row.Status,
                title=row.Title,
                description=row.Description,
                approved_by_user_id=row.ApprovedByUserId,
                approved_datetime=row.ApprovedDatetime,
                effective_date=row.EffectiveDate,
                budget_public_id=getattr(row, "BudgetPublicId", None),
                project_id=getattr(row, "ProjectId", None),
                budget_status=getattr(row, "BudgetStatus", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BudgetRevision: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        budget_id: int,
        type: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        effective_date: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> BudgetRevision:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBudgetRevision",
                    params={
                        "BudgetId": budget_id,
                        "Type": type,
                        "Title": title,
                        "Description": description,
                        "EffectiveDate": effective_date,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateBudgetRevision failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create budget revision: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BudgetRevision]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetRevisionById", params={"Id": id})
            return self._from_db(cursor.fetchone())

    def read_by_public_id(self, public_id: str) -> Optional[BudgetRevision]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetRevisionByPublicId", params={"PublicId": public_id})
            return self._from_db(cursor.fetchone())

    def read_by_budget_id(self, budget_id: int) -> list[BudgetRevision]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadBudgetRevisionsByBudgetId",
                params={"BudgetId": budget_id},
            )
            return [self._from_db(r) for r in cursor.fetchall() if r]

    def update_by_id(
        self,
        *,
        id: int,
        row_version: bytes,
        title: Optional[str] = None,
        description: Optional[str] = None,
        effective_date: Optional[str] = None,
    ) -> Optional[BudgetRevision]:
        """Unconditional SET of Title/Description/EffectiveDate (clearable).

        Returns None on rowversion mismatch (empty result set) — the service
        raises the concurrency conflict.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="UpdateBudgetRevisionById",
                params={
                    "Id": id,
                    "RowVersion": row_version,
                    "Title": title,
                    "Description": description,
                    "EffectiveDate": effective_date,
                },
            )
            return self._from_db(cursor.fetchone())

    def delete_by_id(self, id: int, row_version: bytes) -> bool:
        """Deletes child BudgetLineItems + the revision in one txn.

        Returns False on rowversion mismatch / missing row (empty result
        set) — the service raises the concurrency conflict.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="DeleteBudgetRevisionById",
                params={"Id": id, "RowVersion": row_version},
            )
            return cursor.fetchone() is not None

    def approve_by_id(
        self,
        *,
        id: int,
        row_version: bytes,
        approved_by_user_id: Optional[int],
    ) -> Optional[BudgetRevision]:
        """SET Status='approved' + stamps, gated WHERE Status='draft' in SQL.

        Returns None on rowversion mismatch OR non-draft status (empty result
        set) — the service raises the concurrency/state error.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ApproveBudgetRevisionById",
                params={
                    "Id": id,
                    "RowVersion": row_version,
                    "ApprovedByUserId": approved_by_user_id,
                },
            )
            return self._from_db(cursor.fetchone())
