# Python Standard Library Imports
import base64
import logging
from typing import Optional, List
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from services.contract_labor.business.model import ContractLaborLineItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ContractLaborLineItemRepository:
    """
    Repository for ContractLaborLineItem persistence operations.
    """

    def __init__(self):
        """Initialize the ContractLaborLineItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ContractLaborLineItem]:
        """
        Convert a database row into a ContractLaborLineItem dataclass.
        """
        if not row:
            return None

        try:
            return ContractLaborLineItem(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                contract_labor_id=getattr(row, "ContractLaborId", None),
                line_date=getattr(row, "LineDate", None),
                project_id=getattr(row, "ProjectId", None),
                sub_cost_code_id=getattr(row, "SubCostCodeId", None),
                description=getattr(row, "Description", None),
                hours=Decimal(str(getattr(row, "Hours", None))) if getattr(row, "Hours", None) is not None else None,
                rate=Decimal(str(getattr(row, "Rate", None))) if getattr(row, "Rate", None) is not None else None,
                markup=Decimal(str(getattr(row, "Markup", None))) if getattr(row, "Markup", None) is not None else None,
                price=Decimal(str(getattr(row, "Price", None))) if getattr(row, "Price", None) is not None else None,
                is_billable=getattr(row, "IsBillable", True),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during line item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during line item mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        contract_labor_id: int,
        line_date: Optional[str] = None,
        project_id: Optional[int] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        hours: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        is_billable: bool = True,
    ) -> ContractLaborLineItem:
        """
        Create a new contract labor line item.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateContractLaborLineItem",
                    params={
                        "ContractLaborId": contract_labor_id,
                        "LineDate": line_date,
                        "ProjectId": project_id,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "Hours": float(hours) if hours is not None else None,
                        "Rate": float(rate) if rate is not None else None,
                        "Markup": float(markup) if markup is not None else None,
                        "Price": float(price) if price is not None else None,
                        "IsBillable": is_billable,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateContractLaborLineItem did not return a row.")
                    raise map_database_error(Exception("CreateContractLaborLineItem failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create contract labor line item: {error}")
            raise map_database_error(error)

    def read_by_contract_labor_id(self, contract_labor_id: int) -> List[ContractLaborLineItem]:
        """
        Read all line items for a contract labor entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborLineItemsByContractLaborId",
                    params={"ContractLaborId": contract_labor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read line items by contract labor id: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ContractLaborLineItem]:
        """
        Read a line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read line item by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ContractLaborLineItem]:
        """
        Read a line item by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborLineItemByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read line item by public id: {error}")
            raise map_database_error(error)

    def update_by_id(
        self,
        *,
        id: int,
        row_version: bytes,
        line_date: Optional[str] = None,
        project_id: Optional[int] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        hours: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        is_billable: bool = True,
    ) -> Optional[ContractLaborLineItem]:
        """
        Update a line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractLaborLineItemById",
                    params={
                        "Id": id,
                        "RowVersion": row_version,
                        "LineDate": line_date,
                        "ProjectId": project_id,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "Hours": float(hours) if hours is not None else None,
                        "Rate": float(rate) if rate is not None else None,
                        "Markup": float(markup) if markup is not None else None,
                        "Price": float(price) if price is not None else None,
                        "IsBillable": is_billable,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during update line item by id: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ContractLaborLineItem]:
        """
        Delete a line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteContractLaborLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete line item by id: {error}")
            raise map_database_error(error)

    def delete_by_contract_labor_id(self, contract_labor_id: int) -> int:
        """
        Delete all line items for a contract labor entry.
        Returns the count of deleted items.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteContractLaborLineItemsByContractLaborId",
                    params={"ContractLaborId": contract_labor_id},
                )
                row = cursor.fetchone()
                return row.DeletedCount if row else 0
        except Exception as error:
            logger.error(f"Error during delete line items by contract labor id: {error}")
            raise map_database_error(error)
