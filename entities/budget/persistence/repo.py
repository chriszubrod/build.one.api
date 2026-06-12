# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.budget.business.model import Budget
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _bit(flag):
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0


class BudgetRepository:
    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Budget]:
        if not row:
            return None
        try:
            return Budget(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                project_id=row.ProjectId,
                status=row.Status,
                notes=row.Notes,
                project_name=getattr(row, "ProjectName", None),
                project_public_id=getattr(row, "ProjectPublicId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping Budget: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        project_id: int,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Budget:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBudget",
                    params={
                        "ProjectId": project_id,
                        "Notes": notes,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateBudget failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create budget: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Budget]:
        """Read budgets, scoped by UserProject for non-admin actors.

        ReadBudgets fails closed on a NULL actor — no rows come back.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBudgets",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error during read all budgets: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Budget]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetById", params={"Id": id})
            return self._from_db(cursor.fetchone())

    def read_by_public_id(self, public_id: str) -> Optional[Budget]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetByPublicId", params={"PublicId": public_id})
            return self._from_db(cursor.fetchone())

    def read_by_project_id(self, project_id: int) -> Optional[Budget]:
        """The live (non-archived) budget for a project, if one exists."""
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetByProjectId", params={"ProjectId": project_id})
            return self._from_db(cursor.fetchone())

    def update_by_id(self, budget: Budget) -> Optional[Budget]:
        """Notes-only update. Returns None when the rowversion check fails
        (empty OUTPUT result set = concurrency conflict, raised in service)."""
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="UpdateBudgetById",
                params={
                    "Id": budget.id,
                    "RowVersion": budget.row_version_bytes,
                    "Notes": budget.notes,
                },
            )
            return self._from_db(cursor.fetchone())

    def delete_by_id(self, id: int, row_version_bytes: bytes) -> int:
        """Returns DeletedCount — 0 means concurrency conflict (or already
        deleted), raised in the service."""
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="DeleteBudgetById",
                params={"Id": id, "RowVersion": row_version_bytes},
            )
            row = cursor.fetchone()
            return int(row.DeletedCount) if row else 0

    def read_variance_by_project_id(
        self,
        project_id: int,
        *,
        budget_id: Optional[int] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[dict]:
        """SCC-grain variance rows (budget vs actual cost vs drawn). Decimals
        come back as Decimal from pyodbc and are preserved — NEVER float.
        Empty list when the actor can't access the project (fail-closed).
        budget_id pins the budget side to the caller's resolved Budget so an
        archived budget never reports a different live budget's revisions."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBudgetVarianceByProjectId",
                    params={
                        "ProjectId": project_id,
                        "BudgetId": budget_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as error:
            logger.error(f"Error during read budget variance: {error}")
            raise map_database_error(error)

    def read_list_rollups(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> dict[int, dict]:
        """Per-budget contract value + drawn for the list page, keyed by
        Budget.Id. Same actor scoping as ReadBudgets (fail-closed)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBudgetListRollups",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                return {
                    int(row.BudgetId): {
                        "contract_value": row.ContractValue,
                        "drawn_price": row.DrawnPrice,
                        "remaining_to_draw": row.RemainingToDraw,
                    }
                    for row in cursor.fetchall()
                }
        except Exception as error:
            logger.error(f"Error during read budget list rollups: {error}")
            raise map_database_error(error)

    def activate_by_id(
        self,
        id: int,
        row_version_bytes: bytes,
        approved_by_user_id: Optional[int] = None,
    ) -> Optional[Budget]:
        """Single-txn draft→active + Rev-0 approval. Returns None when the
        sproc's guarded UPDATE matched zero rows (stale rowversion or wrong
        state) — the service maps that to a 409-style conflict."""
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ActivateBudgetById",
                params={
                    "Id": id,
                    "RowVersion": row_version_bytes,
                    "ApprovedByUserId": approved_by_user_id,
                },
            )
            try:
                row = cursor.fetchone()
            except pyodbc.ProgrammingError:
                # No result set at all — the guarded UPDATE matched 0 rows.
                return None
            return self._from_db(row)
