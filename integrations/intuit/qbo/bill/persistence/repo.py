# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboBillRepository:
    """
    Repository for QboBill persistence operations.
    """

    def __init__(self):
        """Initialize the QboBillRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboBill]:
        """
        Convert a database row into a QboBill dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboBill(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                vendor_ref_value=getattr(row, "VendorRefValue", None),
                vendor_ref_name=getattr(row, "VendorRefName", None),
                txn_date=getattr(row, "TxnDate", None),
                due_date=getattr(row, "DueDate", None),
                doc_number=getattr(row, "DocNumber", None),
                private_note=getattr(row, "PrivateNote", None),
                total_amt=Decimal(str(getattr(row, "TotalAmt"))) if getattr(row, "TotalAmt", None) is not None else None,
                balance=Decimal(str(getattr(row, "Balance"))) if getattr(row, "Balance", None) is not None else None,
                ap_account_ref_value=getattr(row, "ApAccountRefValue", None),
                ap_account_ref_name=getattr(row, "ApAccountRefName", None),
                sales_term_ref_value=getattr(row, "SalesTermRefValue", None),
                sales_term_ref_name=getattr(row, "SalesTermRefName", None),
                currency_ref_value=getattr(row, "CurrencyRefValue", None),
                currency_ref_name=getattr(row, "CurrencyRefName", None),
                exchange_rate=Decimal(str(getattr(row, "ExchangeRate"))) if getattr(row, "ExchangeRate", None) is not None else None,
                department_ref_value=getattr(row, "DepartmentRefValue", None),
                department_ref_name=getattr(row, "DepartmentRefName", None),
                global_tax_calculation=getattr(row, "GlobalTaxCalculation", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo bill mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo bill mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        vendor_ref_value: Optional[str],
        vendor_ref_name: Optional[str],
        txn_date: Optional[str],
        due_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        total_amt: Optional[Decimal],
        balance: Optional[Decimal],
        ap_account_ref_value: Optional[str],
        ap_account_ref_name: Optional[str],
        sales_term_ref_value: Optional[str],
        sales_term_ref_name: Optional[str],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        global_tax_calculation: Optional[str],
    ) -> QboBill:
        """
        Create a new QboBill.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "VendorRefValue": vendor_ref_value,
                        "VendorRefName": vendor_ref_name,
                        "TxnDate": txn_date,
                        "DueDate": due_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "Balance": float(balance) if balance is not None else None,
                        "ApAccountRefValue": ap_account_ref_value,
                        "ApAccountRefName": ap_account_ref_name,
                        "SalesTermRefValue": sales_term_ref_value,
                        "SalesTermRefName": sales_term_ref_name,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling CreateQboBill with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboBill",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo bill did not return a row.")
                        raise map_database_error(Exception("create qbo bill failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo bill: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboBill]:
        """
        Read all QboBills.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBills",
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
            logger.error(f"Error during read all qbo bills: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboBill]:
        """
        Read all QboBills by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillsByRealmId",
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
            logger.error(f"Error during read qbo bills by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboBill]:
        """
        Read a QboBill by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillById",
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
            logger.error(f"Error during read qbo bill by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboBill]:
        """
        Read a QboBill by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillByQboId",
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
            logger.error(f"Error during read qbo bill by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboBill]:
        """
        Read a QboBill by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillByQboIdAndRealmId",
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
            logger.error(f"Error during read qbo bill by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        vendor_ref_value: Optional[str],
        vendor_ref_name: Optional[str],
        txn_date: Optional[str],
        due_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        total_amt: Optional[Decimal],
        balance: Optional[Decimal],
        ap_account_ref_value: Optional[str],
        ap_account_ref_name: Optional[str],
        sales_term_ref_value: Optional[str],
        sales_term_ref_name: Optional[str],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        global_tax_calculation: Optional[str],
    ) -> Optional[QboBill]:
        """
        Update a QboBill by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "RowVersion": row_version,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "VendorRefValue": vendor_ref_value,
                        "VendorRefName": vendor_ref_name,
                        "TxnDate": txn_date,
                        "DueDate": due_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "Balance": float(balance) if balance is not None else None,
                        "ApAccountRefValue": ap_account_ref_value,
                        "ApAccountRefName": ap_account_ref_name,
                        "SalesTermRefValue": sales_term_ref_value,
                        "SalesTermRefName": sales_term_ref_name,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling UpdateQboBillByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboBillByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo bill did not return a row.")
                        raise map_database_error(Exception("update qbo bill by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo bill by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboBill]:
        """
        Delete a QboBill by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboBillByQboId",
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
            logger.error(f"Error during delete qbo bill by QBO ID: {error}")
            raise map_database_error(error)


class QboBillLineRepository:
    """
    Repository for QboBillLine persistence operations.
    """

    def __init__(self):
        """Initialize the QboBillLineRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboBillLine]:
        """
        Convert a database row into a QboBillLine dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboBillLine(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_bill_id=getattr(row, "QboBillId", None),
                qbo_line_id=getattr(row, "QboLineId", None),
                line_num=getattr(row, "LineNum", None),
                description=getattr(row, "Description", None),
                amount=Decimal(str(getattr(row, "Amount"))) if getattr(row, "Amount", None) is not None else None,
                detail_type=getattr(row, "DetailType", None),
                item_ref_value=getattr(row, "ItemRefValue", None),
                item_ref_name=getattr(row, "ItemRefName", None),
                account_ref_value=getattr(row, "AccountRefValue", None),
                account_ref_name=getattr(row, "AccountRefName", None),
                customer_ref_value=getattr(row, "CustomerRefValue", None),
                customer_ref_name=getattr(row, "CustomerRefName", None),
                class_ref_value=getattr(row, "ClassRefValue", None),
                class_ref_name=getattr(row, "ClassRefName", None),
                billable_status=getattr(row, "BillableStatus", None),
                qty=Decimal(str(getattr(row, "Qty"))) if getattr(row, "Qty", None) is not None else None,
                unit_price=Decimal(str(getattr(row, "UnitPrice"))) if getattr(row, "UnitPrice", None) is not None else None,
                markup_percent=Decimal(str(getattr(row, "MarkupPercent"))) if getattr(row, "MarkupPercent", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo bill line mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo bill line mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_bill_id: int,
        qbo_line_id: Optional[str],
        line_num: Optional[int],
        description: Optional[str],
        amount: Optional[Decimal],
        detail_type: Optional[str],
        item_ref_value: Optional[str],
        item_ref_name: Optional[str],
        account_ref_value: Optional[str],
        account_ref_name: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        billable_status: Optional[str],
        qty: Optional[Decimal],
        unit_price: Optional[Decimal],
        markup_percent: Optional[Decimal],
    ) -> QboBillLine:
        """
        Create a new QboBillLine.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboBillId": qbo_bill_id,
                        "QboLineId": qbo_line_id,
                        "LineNum": line_num,
                        "Description": description,
                        "Amount": float(amount) if amount is not None else None,
                        "DetailType": detail_type,
                        "ItemRefValue": item_ref_value,
                        "ItemRefName": item_ref_name,
                        "AccountRefValue": account_ref_value,
                        "AccountRefName": account_ref_name,
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "BillableStatus": billable_status,
                        "Qty": float(qty) if qty is not None else None,
                        "UnitPrice": float(unit_price) if unit_price is not None else None,
                        "MarkupPercent": float(markup_percent) if markup_percent is not None else None,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboBillLine",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo bill line did not return a row.")
                        raise map_database_error(Exception("create qbo bill line failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo bill line: {error}")
            raise map_database_error(error)

    def read_by_qbo_bill_id(self, qbo_bill_id: int) -> List[QboBillLine]:
        """
        Read all QboBillLines for a QboBill.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillLinesByQboBillId",
                        params={"QboBillId": qbo_bill_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo bill lines by qbo bill ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_bill_id_and_qbo_line_id(self, qbo_bill_id: int, qbo_line_id: str) -> Optional[QboBillLine]:
        """
        Read a QboBillLine by QboBill ID and QBO Line ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboBillLineByQboBillIdAndQboLineId",
                        params={"QboBillId": qbo_bill_id, "QboLineId": qbo_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo bill line by qbo bill ID and qbo line ID: {error}")
            raise map_database_error(error)

    def update_by_id(
        self,
        id: int,
        row_version: bytes,
        line_num: Optional[int],
        description: Optional[str],
        amount: Optional[Decimal],
        detail_type: Optional[str],
        item_ref_value: Optional[str],
        item_ref_name: Optional[str],
        account_ref_value: Optional[str],
        account_ref_name: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        billable_status: Optional[str],
        qty: Optional[Decimal],
        unit_price: Optional[Decimal],
        markup_percent: Optional[Decimal],
    ) -> Optional[QboBillLine]:
        """
        Update a QboBillLine by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Id": id,
                        "RowVersion": row_version,
                        "LineNum": line_num,
                        "Description": description,
                        "Amount": float(amount) if amount is not None else None,
                        "DetailType": detail_type,
                        "ItemRefValue": item_ref_value,
                        "ItemRefName": item_ref_name,
                        "AccountRefValue": account_ref_value,
                        "AccountRefName": account_ref_name,
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "BillableStatus": billable_status,
                        "Qty": float(qty) if qty is not None else None,
                        "UnitPrice": float(unit_price) if unit_price is not None else None,
                        "MarkupPercent": float(markup_percent) if markup_percent is not None else None,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboBillLineById",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo bill line did not return a row.")
                        raise map_database_error(Exception("update qbo bill line by ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo bill line by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[QboBillLine]:
        """
        Delete a QboBillLine by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboBillLineById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo bill line by ID: {error}")
            raise map_database_error(error)
