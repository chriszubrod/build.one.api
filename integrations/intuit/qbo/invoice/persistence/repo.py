# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.invoice.business.model import QboInvoice, QboInvoiceLine
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboInvoiceRepository:
    """
    Repository for QboInvoice persistence operations.
    """

    def __init__(self):
        """Initialize the QboInvoiceRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboInvoice]:
        """
        Convert a database row into a QboInvoice dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboInvoice(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                customer_ref_value=getattr(row, "CustomerRefValue", None),
                customer_ref_name=getattr(row, "CustomerRefName", None),
                txn_date=getattr(row, "TxnDate", None),
                due_date=getattr(row, "DueDate", None),
                ship_date=getattr(row, "ShipDate", None),
                doc_number=getattr(row, "DocNumber", None),
                private_note=getattr(row, "PrivateNote", None),
                customer_memo=getattr(row, "CustomerMemo", None),
                bill_email=getattr(row, "BillEmail", None),
                total_amt=Decimal(str(getattr(row, "TotalAmt"))) if getattr(row, "TotalAmt", None) is not None else None,
                balance=Decimal(str(getattr(row, "Balance"))) if getattr(row, "Balance", None) is not None else None,
                deposit=Decimal(str(getattr(row, "Deposit"))) if getattr(row, "Deposit", None) is not None else None,
                sales_term_ref_value=getattr(row, "SalesTermRefValue", None),
                sales_term_ref_name=getattr(row, "SalesTermRefName", None),
                currency_ref_value=getattr(row, "CurrencyRefValue", None),
                currency_ref_name=getattr(row, "CurrencyRefName", None),
                exchange_rate=Decimal(str(getattr(row, "ExchangeRate"))) if getattr(row, "ExchangeRate", None) is not None else None,
                department_ref_value=getattr(row, "DepartmentRefValue", None),
                department_ref_name=getattr(row, "DepartmentRefName", None),
                class_ref_value=getattr(row, "ClassRefValue", None),
                class_ref_name=getattr(row, "ClassRefName", None),
                ship_method_ref_value=getattr(row, "ShipMethodRefValue", None),
                ship_method_ref_name=getattr(row, "ShipMethodRefName", None),
                tracking_num=getattr(row, "TrackingNum", None),
                print_status=getattr(row, "PrintStatus", None),
                email_status=getattr(row, "EmailStatus", None),
                allow_online_ach_payment=getattr(row, "AllowOnlineACHPayment", None),
                allow_online_credit_card_payment=getattr(row, "AllowOnlineCreditCardPayment", None),
                apply_tax_after_discount=getattr(row, "ApplyTaxAfterDiscount", None),
                global_tax_calculation=getattr(row, "GlobalTaxCalculation", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo invoice mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo invoice mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        txn_date: Optional[str],
        due_date: Optional[str],
        ship_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        customer_memo: Optional[str],
        bill_email: Optional[str],
        total_amt: Optional[Decimal],
        balance: Optional[Decimal],
        deposit: Optional[Decimal],
        sales_term_ref_value: Optional[str],
        sales_term_ref_name: Optional[str],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        ship_method_ref_value: Optional[str],
        ship_method_ref_name: Optional[str],
        tracking_num: Optional[str],
        print_status: Optional[str],
        email_status: Optional[str],
        allow_online_ach_payment: Optional[bool],
        allow_online_credit_card_payment: Optional[bool],
        apply_tax_after_discount: Optional[bool],
        global_tax_calculation: Optional[str],
    ) -> QboInvoice:
        """
        Create a new QboInvoice.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "TxnDate": txn_date,
                        "DueDate": due_date,
                        "ShipDate": ship_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "CustomerMemo": customer_memo,
                        "BillEmail": bill_email,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "Balance": float(balance) if balance is not None else None,
                        "Deposit": float(deposit) if deposit is not None else None,
                        "SalesTermRefValue": sales_term_ref_value,
                        "SalesTermRefName": sales_term_ref_name,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "ShipMethodRefValue": ship_method_ref_value,
                        "ShipMethodRefName": ship_method_ref_name,
                        "TrackingNum": tracking_num,
                        "PrintStatus": print_status,
                        "EmailStatus": email_status,
                        "AllowOnlineACHPayment": allow_online_ach_payment,
                        "AllowOnlineCreditCardPayment": allow_online_credit_card_payment,
                        "ApplyTaxAfterDiscount": apply_tax_after_discount,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling CreateQboInvoice with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboInvoice",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo invoice did not return a row.")
                        raise map_database_error(Exception("create qbo invoice failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo invoice: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboInvoice]:
        """
        Read all QboInvoices.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoices",
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
            logger.error(f"Error during read all qbo invoices: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboInvoice]:
        """
        Read all QboInvoices by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoicesByRealmId",
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
            logger.error(f"Error during read qbo invoices by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboInvoice]:
        """
        Read a QboInvoice by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoiceById",
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
            logger.error(f"Error during read qbo invoice by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboInvoice]:
        """
        Read a QboInvoice by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoiceByQboId",
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
            logger.error(f"Error during read qbo invoice by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboInvoice]:
        """
        Read a QboInvoice by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoiceByQboIdAndRealmId",
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
            logger.error(f"Error during read qbo invoice by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        txn_date: Optional[str],
        due_date: Optional[str],
        ship_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        customer_memo: Optional[str],
        bill_email: Optional[str],
        total_amt: Optional[Decimal],
        balance: Optional[Decimal],
        deposit: Optional[Decimal],
        sales_term_ref_value: Optional[str],
        sales_term_ref_name: Optional[str],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        ship_method_ref_value: Optional[str],
        ship_method_ref_name: Optional[str],
        tracking_num: Optional[str],
        print_status: Optional[str],
        email_status: Optional[str],
        allow_online_ach_payment: Optional[bool],
        allow_online_credit_card_payment: Optional[bool],
        apply_tax_after_discount: Optional[bool],
        global_tax_calculation: Optional[str],
    ) -> Optional[QboInvoice]:
        """
        Update a QboInvoice by QBO ID.
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
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "TxnDate": txn_date,
                        "DueDate": due_date,
                        "ShipDate": ship_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "CustomerMemo": customer_memo,
                        "BillEmail": bill_email,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "Balance": float(balance) if balance is not None else None,
                        "Deposit": float(deposit) if deposit is not None else None,
                        "SalesTermRefValue": sales_term_ref_value,
                        "SalesTermRefName": sales_term_ref_name,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "ShipMethodRefValue": ship_method_ref_value,
                        "ShipMethodRefName": ship_method_ref_name,
                        "TrackingNum": tracking_num,
                        "PrintStatus": print_status,
                        "EmailStatus": email_status,
                        "AllowOnlineACHPayment": allow_online_ach_payment,
                        "AllowOnlineCreditCardPayment": allow_online_credit_card_payment,
                        "ApplyTaxAfterDiscount": apply_tax_after_discount,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling UpdateQboInvoiceByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboInvoiceByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo invoice did not return a row.")
                        raise map_database_error(Exception("update qbo invoice by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo invoice by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboInvoice]:
        """
        Delete a QboInvoice by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboInvoiceByQboId",
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
            logger.error(f"Error during delete qbo invoice by QBO ID: {error}")
            raise map_database_error(error)


class QboInvoiceLineRepository:
    """
    Repository for QboInvoiceLine persistence operations.
    """

    def __init__(self):
        """Initialize the QboInvoiceLineRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboInvoiceLine]:
        """
        Convert a database row into a QboInvoiceLine dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboInvoiceLine(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_invoice_id=getattr(row, "QboInvoiceId", None),
                qbo_line_id=getattr(row, "QboLineId", None),
                line_num=getattr(row, "LineNum", None),
                description=getattr(row, "Description", None),
                amount=Decimal(str(getattr(row, "Amount"))) if getattr(row, "Amount", None) is not None else None,
                detail_type=getattr(row, "DetailType", None),
                item_ref_value=getattr(row, "ItemRefValue", None),
                item_ref_name=getattr(row, "ItemRefName", None),
                class_ref_value=getattr(row, "ClassRefValue", None),
                class_ref_name=getattr(row, "ClassRefName", None),
                qty=Decimal(str(getattr(row, "Qty"))) if getattr(row, "Qty", None) is not None else None,
                unit_price=Decimal(str(getattr(row, "UnitPrice"))) if getattr(row, "UnitPrice", None) is not None else None,
                tax_code_ref_value=getattr(row, "TaxCodeRefValue", None),
                tax_code_ref_name=getattr(row, "TaxCodeRefName", None),
                service_date=getattr(row, "ServiceDate", None),
                discount_rate=Decimal(str(getattr(row, "DiscountRate"))) if getattr(row, "DiscountRate", None) is not None else None,
                discount_amt=Decimal(str(getattr(row, "DiscountAmt"))) if getattr(row, "DiscountAmt", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo invoice line mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo invoice line mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_invoice_id: int,
        qbo_line_id: Optional[str],
        line_num: Optional[int],
        description: Optional[str],
        amount: Optional[Decimal],
        detail_type: Optional[str],
        item_ref_value: Optional[str],
        item_ref_name: Optional[str],
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        qty: Optional[Decimal],
        unit_price: Optional[Decimal],
        tax_code_ref_value: Optional[str],
        tax_code_ref_name: Optional[str],
        service_date: Optional[str],
        discount_rate: Optional[Decimal],
        discount_amt: Optional[Decimal],
    ) -> QboInvoiceLine:
        """
        Create a new QboInvoiceLine.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboInvoiceId": qbo_invoice_id,
                        "QboLineId": qbo_line_id,
                        "LineNum": line_num,
                        "Description": description,
                        "Amount": float(amount) if amount is not None else None,
                        "DetailType": detail_type,
                        "ItemRefValue": item_ref_value,
                        "ItemRefName": item_ref_name,
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "Qty": float(qty) if qty is not None else None,
                        "UnitPrice": float(unit_price) if unit_price is not None else None,
                        "TaxCodeRefValue": tax_code_ref_value,
                        "TaxCodeRefName": tax_code_ref_name,
                        "ServiceDate": service_date,
                        "DiscountRate": float(discount_rate) if discount_rate is not None else None,
                        "DiscountAmt": float(discount_amt) if discount_amt is not None else None,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboInvoiceLine",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo invoice line did not return a row.")
                        raise map_database_error(Exception("create qbo invoice line failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo invoice line: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboInvoiceLine]:
        """
        Read all QboInvoiceLines (for pre-loading into memory).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT * FROM [qbo].[InvoiceLine]")
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all qbo invoice lines: {error}")
            raise map_database_error(error)

    def read_by_qbo_invoice_id(self, qbo_invoice_id: int) -> List[QboInvoiceLine]:
        """
        Read all QboInvoiceLines for a QboInvoice.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoiceLinesByQboInvoiceId",
                        params={"QboInvoiceId": qbo_invoice_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo invoice lines by qbo invoice ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_invoice_id_and_qbo_line_id(self, qbo_invoice_id: int, qbo_line_id: str) -> Optional[QboInvoiceLine]:
        """
        Read a QboInvoiceLine by QboInvoice ID and QBO Line ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboInvoiceLineByQboInvoiceIdAndQboLineId",
                        params={"QboInvoiceId": qbo_invoice_id, "QboLineId": qbo_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo invoice line by qbo invoice ID and qbo line ID: {error}")
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
        class_ref_value: Optional[str],
        class_ref_name: Optional[str],
        qty: Optional[Decimal],
        unit_price: Optional[Decimal],
        tax_code_ref_value: Optional[str],
        tax_code_ref_name: Optional[str],
        service_date: Optional[str],
        discount_rate: Optional[Decimal],
        discount_amt: Optional[Decimal],
    ) -> Optional[QboInvoiceLine]:
        """
        Update a QboInvoiceLine by ID.
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
                        "ClassRefValue": class_ref_value,
                        "ClassRefName": class_ref_name,
                        "Qty": float(qty) if qty is not None else None,
                        "UnitPrice": float(unit_price) if unit_price is not None else None,
                        "TaxCodeRefValue": tax_code_ref_value,
                        "TaxCodeRefName": tax_code_ref_name,
                        "ServiceDate": service_date,
                        "DiscountRate": float(discount_rate) if discount_rate is not None else None,
                        "DiscountAmt": float(discount_amt) if discount_amt is not None else None,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboInvoiceLineById",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo invoice line did not return a row.")
                        raise map_database_error(Exception("update qbo invoice line by ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo invoice line by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[QboInvoiceLine]:
        """
        Delete a QboInvoiceLine by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboInvoiceLineById",
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
            logger.error(f"Error during delete qbo invoice line by ID: {error}")
            raise map_database_error(error)
