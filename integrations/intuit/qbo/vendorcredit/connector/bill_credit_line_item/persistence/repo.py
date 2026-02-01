# Python Standard Library Imports
import base64
import logging
from typing import Optional
from dataclasses import dataclass

# Third-party Imports
import pyodbc

# Local Imports
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


@dataclass
class VendorCreditLineItemBillCreditLineItemMapping:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_vendor_credit_line_id: Optional[int]
    bill_credit_line_item_id: Optional[int]


class VendorCreditLineItemBillCreditLineItemMappingRepository:
    """Repository for VendorCreditLine <-> BillCreditLineItem mapping."""

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorCreditLineItemBillCreditLineItemMapping]:
        if not row:
            return None
        try:
            return VendorCreditLineItemBillCreditLineItemMapping(
                id=row.Id,
                public_id=str(row.PublicId),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                qbo_vendor_credit_line_id=row.QboVendorCreditLineId,
                bill_credit_line_item_id=row.BillCreditLineItemId,
            )
        except Exception as e:
            logger.error(f"Error mapping VendorCreditLineItemBillCreditLineItem row: {e}")
            raise map_database_error(e)

    def create(self, qbo_vendor_credit_line_id: int, bill_credit_line_item_id: int) -> VendorCreditLineItemBillCreditLineItemMapping:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "CreateVendorCreditLineItemBillCreditLineItem", {
                    "QboVendorCreditLineId": qbo_vendor_credit_line_id,
                    "BillCreditLineItemId": bill_credit_line_item_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error creating VendorCreditLineItemBillCreditLineItem mapping: {e}")
            raise map_database_error(e)

    def read_by_qbo_line_id(self, qbo_vendor_credit_line_id: int) -> Optional[VendorCreditLineItemBillCreditLineItemMapping]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadVendorCreditLineItemBillCreditLineItemByQboLineId", {
                    "QboVendorCreditLineId": qbo_vendor_credit_line_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading mapping by QboVendorCreditLineId: {e}")
            raise map_database_error(e)

    def read_by_bill_credit_line_item_id(self, bill_credit_line_item_id: int) -> Optional[VendorCreditLineItemBillCreditLineItemMapping]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId", {
                    "BillCreditLineItemId": bill_credit_line_item_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading mapping by BillCreditLineItemId: {e}")
            raise map_database_error(e)
