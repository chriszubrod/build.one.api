# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.budget_line_item.business.model import BudgetLineItem
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class BudgetLineItemRepository:
    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BudgetLineItem]:
        if not row:
            return None
        try:
            return BudgetLineItem(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                budget_revision_id=row.BudgetRevisionId,
                sub_cost_code_id=row.SubCostCodeId,
                description=row.Description,
                quantity=row.Quantity,
                rate=row.Rate,
                amount=row.Amount,
                markup=row.Markup,
                price=row.Price,
                # Join-enriched columns only exist on the Read sprocs;
                # Create/Update OUTPUT rows omit them.
                revision_status=getattr(row, "RevisionStatus", None),
                revision_type=getattr(row, "RevisionType", None),
                budget_id=getattr(row, "BudgetId", None),
                project_id=getattr(row, "ProjectId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BudgetLineItem: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        budget_revision_id: int,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        created_by_user_id: Optional[int] = None,
    ) -> BudgetLineItem:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBudgetLineItem",
                    params={
                        "BudgetRevisionId": budget_revision_id,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "Quantity": quantity,
                        "Rate": rate,
                        "Amount": amount,
                        "Markup": markup,
                        "Price": price,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateBudgetLineItem failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create budget line item: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BudgetLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadBudgetLineItemById", params={"Id": id})
            return self._from_db(cursor.fetchone())

    def read_by_public_id(self, public_id: str) -> Optional[BudgetLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadBudgetLineItemByPublicId",
                params={"PublicId": public_id},
            )
            return self._from_db(cursor.fetchone())

    def read_by_budget_revision_id(self, budget_revision_id: int) -> list[BudgetLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadBudgetLineItemsByBudgetRevisionId",
                params={"BudgetRevisionId": budget_revision_id},
            )
            return [self._from_db(r) for r in cursor.fetchall() if r]

    def update_by_id(
        self,
        *,
        id: int,
        row_version_bytes: bytes,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
    ) -> Optional[BudgetLineItem]:
        """
        Unconditional SET of all business fields (grid clearability) —
        passing None writes NULL. Callers must send the full row state.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateBudgetLineItemById",
                    params={
                        "Id": id,
                        "RowVersion": row_version_bytes,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "Quantity": quantity,
                        "Rate": rate,
                        "Amount": amount,
                        "Markup": markup,
                        "Price": price,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateBudgetLineItemById returned no row (id=%s); possible row-version conflict or record not found.",
                        id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the budget line item may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update budget line item by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int, row_version_bytes: bytes) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBudgetLineItemById",
                    params={"Id": id, "RowVersion": row_version_bytes},
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "DeleteBudgetLineItemById matched no row (id=%s); possible row-version conflict or record not found.",
                        id,
                    )
                    raise map_database_error(
                        Exception(
                            "Delete did not match any row; the budget line item may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
        except Exception as error:
            logger.error(f"Error during delete budget line item by ID: {error}")
            raise map_database_error(error)
