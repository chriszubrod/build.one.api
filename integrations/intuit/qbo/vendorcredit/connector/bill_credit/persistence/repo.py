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
class VendorCreditBillCreditMapping:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_vendor_credit_id: Optional[int]
    bill_credit_id: Optional[int]


class VendorCreditBillCreditMappingRepository:
    """Repository for VendorCredit <-> BillCredit mapping."""

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorCreditBillCreditMapping]:
        if not row:
            return None
        try:
            return VendorCreditBillCreditMapping(
                id=row.Id,
                public_id=str(row.PublicId),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                qbo_vendor_credit_id=row.QboVendorCreditId,
                bill_credit_id=row.BillCreditId,
            )
        except Exception as e:
            logger.error(f"Error mapping VendorCreditBillCredit row: {e}")
            raise map_database_error(e)

    def create(self, qbo_vendor_credit_id: int, bill_credit_id: int) -> VendorCreditBillCreditMapping:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "CreateVendorCreditBillCredit", {
                    "QboVendorCreditId": qbo_vendor_credit_id,
                    "BillCreditId": bill_credit_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error creating VendorCreditBillCredit mapping: {e}")
            raise map_database_error(e)

    def read_by_qbo_vendor_credit_id(self, qbo_vendor_credit_id: int) -> Optional[VendorCreditBillCreditMapping]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadVendorCreditBillCreditByQboVendorCreditId", {
                    "QboVendorCreditId": qbo_vendor_credit_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading mapping by QboVendorCreditId: {e}")
            raise map_database_error(e)

    def read_by_bill_credit_id(self, bill_credit_id: int) -> Optional[VendorCreditBillCreditMapping]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadVendorCreditBillCreditByBillCreditId", {
                    "BillCreditId": bill_credit_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading mapping by BillCreditId: {e}")
            raise map_database_error(e)
