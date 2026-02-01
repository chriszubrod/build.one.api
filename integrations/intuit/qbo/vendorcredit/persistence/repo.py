# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class QboVendorCreditRepository:
    """Repository for QBO VendorCredit cache persistence."""

    def _from_db(self, row: pyodbc.Row) -> Optional[QboVendorCredit]:
        if not row:
            return None
        try:
            return QboVendorCredit(
                id=row.Id,
                public_id=str(row.PublicId),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                realm_id=row.RealmId,
                qbo_id=row.QboId,
                sync_token=row.SyncToken,
                vendor_ref_value=row.VendorRefValue,
                vendor_ref_name=row.VendorRefName,
                txn_date=row.TxnDate,
                doc_number=row.DocNumber,
                total_amt=Decimal(str(row.TotalAmt)) if row.TotalAmt else None,
                private_note=row.PrivateNote,
                ap_account_ref_value=row.APAccountRefValue,
                ap_account_ref_name=row.APAccountRefName,
                currency_ref_value=row.CurrencyRefValue,
                currency_ref_name=row.CurrencyRefName,
            )
        except Exception as e:
            logger.error(f"Error mapping VendorCredit row: {e}")
            raise map_database_error(e)

    def _line_from_db(self, row: pyodbc.Row) -> Optional[QboVendorCreditLine]:
        if not row:
            return None
        try:
            return QboVendorCreditLine(
                id=row.Id,
                public_id=str(row.PublicId),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                qbo_vendor_credit_id=row.QboVendorCreditId,
                qbo_line_id=row.QboLineId,
                line_num=row.LineNum,
                description=row.Description,
                amount=Decimal(str(row.Amount)) if row.Amount else None,
                detail_type=row.DetailType,
                item_ref_value=row.ItemRefValue,
                item_ref_name=row.ItemRefName,
                class_ref_value=row.ClassRefValue,
                class_ref_name=row.ClassRefName,
                unit_price=Decimal(str(row.UnitPrice)) if row.UnitPrice else None,
                qty=Decimal(str(row.Qty)) if row.Qty else None,
                billable_status=row.BillableStatus,
                customer_ref_value=row.CustomerRefValue,
                customer_ref_name=row.CustomerRefName,
                account_ref_value=row.AccountRefValue,
                account_ref_name=row.AccountRefName,
            )
        except Exception as e:
            logger.error(f"Error mapping VendorCreditLine row: {e}")
            raise map_database_error(e)

    def create(self, vendor_credit: QboVendorCredit) -> QboVendorCredit:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "CreateQboVendorCredit", {
                    "RealmId": vendor_credit.realm_id,
                    "QboId": vendor_credit.qbo_id,
                    "SyncToken": vendor_credit.sync_token,
                    "VendorRefValue": vendor_credit.vendor_ref_value,
                    "VendorRefName": vendor_credit.vendor_ref_name,
                    "TxnDate": vendor_credit.txn_date,
                    "DocNumber": vendor_credit.doc_number,
                    "TotalAmt": float(vendor_credit.total_amt) if vendor_credit.total_amt else None,
                    "PrivateNote": vendor_credit.private_note,
                    "APAccountRefValue": vendor_credit.ap_account_ref_value,
                    "APAccountRefName": vendor_credit.ap_account_ref_name,
                    "CurrencyRefValue": vendor_credit.currency_ref_value,
                    "CurrencyRefName": vendor_credit.currency_ref_name,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error creating VendorCredit: {e}")
            raise map_database_error(e)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboVendorCredit]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadQboVendorCreditByQboIdAndRealmId", {
                    "QboId": qbo_id,
                    "RealmId": realm_id,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading VendorCredit by QboId and RealmId: {e}")
            raise map_database_error(e)

    def read_by_realm_id(self, realm_id: str) -> list[QboVendorCredit]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadQboVendorCreditsByRealmId", {"RealmId": realm_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as e:
            logger.error(f"Error reading VendorCredits by RealmId: {e}")
            raise map_database_error(e)

    def read_by_id(self, id: int) -> Optional[QboVendorCredit]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadQboVendorCreditById", {"Id": id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error reading VendorCredit by Id: {e}")
            raise map_database_error(e)

    def update_by_qbo_id(self, vendor_credit: QboVendorCredit) -> Optional[QboVendorCredit]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "UpdateQboVendorCreditByQboId", {
                    "QboId": vendor_credit.qbo_id,
                    "RealmId": vendor_credit.realm_id,
                    "RowVersion": vendor_credit.row_version_bytes,
                    "SyncToken": vendor_credit.sync_token,
                    "VendorRefValue": vendor_credit.vendor_ref_value,
                    "VendorRefName": vendor_credit.vendor_ref_name,
                    "TxnDate": vendor_credit.txn_date,
                    "DocNumber": vendor_credit.doc_number,
                    "TotalAmt": float(vendor_credit.total_amt) if vendor_credit.total_amt else None,
                    "PrivateNote": vendor_credit.private_note,
                    "APAccountRefValue": vendor_credit.ap_account_ref_value,
                    "APAccountRefName": vendor_credit.ap_account_ref_name,
                    "CurrencyRefValue": vendor_credit.currency_ref_value,
                    "CurrencyRefName": vendor_credit.currency_ref_name,
                })
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error updating VendorCredit: {e}")
            raise map_database_error(e)

    # Line item methods
    def create_line(self, line: QboVendorCreditLine) -> QboVendorCreditLine:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "CreateQboVendorCreditLine", {
                    "QboVendorCreditId": line.qbo_vendor_credit_id,
                    "QboLineId": line.qbo_line_id,
                    "LineNum": line.line_num,
                    "Description": line.description,
                    "Amount": float(line.amount) if line.amount else None,
                    "DetailType": line.detail_type,
                    "ItemRefValue": line.item_ref_value,
                    "ItemRefName": line.item_ref_name,
                    "ClassRefValue": line.class_ref_value,
                    "ClassRefName": line.class_ref_name,
                    "UnitPrice": float(line.unit_price) if line.unit_price else None,
                    "Qty": float(line.qty) if line.qty else None,
                    "BillableStatus": line.billable_status,
                    "CustomerRefValue": line.customer_ref_value,
                    "CustomerRefName": line.customer_ref_name,
                    "AccountRefValue": line.account_ref_value,
                    "AccountRefName": line.account_ref_name,
                })
                row = cursor.fetchone()
                return self._line_from_db(row)
        except Exception as e:
            logger.error(f"Error creating VendorCreditLine: {e}")
            raise map_database_error(e)

    def read_lines_by_vendor_credit_id(self, qbo_vendor_credit_id: int) -> list[QboVendorCreditLine]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "ReadQboVendorCreditLinesByVendorCreditId", {
                    "QboVendorCreditId": qbo_vendor_credit_id
                })
                rows = cursor.fetchall()
                return [self._line_from_db(row) for row in rows if row]
        except Exception as e:
            logger.error(f"Error reading VendorCreditLines: {e}")
            raise map_database_error(e)

    def delete_lines_by_vendor_credit_id(self, qbo_vendor_credit_id: int) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor, "DeleteQboVendorCreditLinesByVendorCreditId", {
                    "QboVendorCreditId": qbo_vendor_credit_id
                })
        except Exception as e:
            logger.error(f"Error deleting VendorCreditLines: {e}")
            raise map_database_error(e)
