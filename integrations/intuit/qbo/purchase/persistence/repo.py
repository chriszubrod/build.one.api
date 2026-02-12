# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboPurchaseRepository:
    """
    Repository for QboPurchase persistence operations.
    """

    def __init__(self):
        """Initialize the QboPurchaseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboPurchase]:
        """
        Convert a database row into a QboPurchase dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboPurchase(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                payment_type=getattr(row, "PaymentType", None),
                account_ref_value=getattr(row, "AccountRefValue", None),
                account_ref_name=getattr(row, "AccountRefName", None),
                entity_ref_value=getattr(row, "EntityRefValue", None),
                entity_ref_name=getattr(row, "EntityRefName", None),
                credit=getattr(row, "Credit", None),
                txn_date=getattr(row, "TxnDate", None),
                doc_number=getattr(row, "DocNumber", None),
                private_note=getattr(row, "PrivateNote", None),
                total_amt=Decimal(str(getattr(row, "TotalAmt"))) if getattr(row, "TotalAmt", None) is not None else None,
                currency_ref_value=getattr(row, "CurrencyRefValue", None),
                currency_ref_name=getattr(row, "CurrencyRefName", None),
                exchange_rate=Decimal(str(getattr(row, "ExchangeRate"))) if getattr(row, "ExchangeRate", None) is not None else None,
                department_ref_value=getattr(row, "DepartmentRefValue", None),
                department_ref_name=getattr(row, "DepartmentRefName", None),
                global_tax_calculation=getattr(row, "GlobalTaxCalculation", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo purchase mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo purchase mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        payment_type: Optional[str],
        account_ref_value: Optional[str],
        account_ref_name: Optional[str],
        entity_ref_value: Optional[str],
        entity_ref_name: Optional[str],
        credit: Optional[bool],
        txn_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        total_amt: Optional[Decimal],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        global_tax_calculation: Optional[str],
    ) -> QboPurchase:
        """
        Create a new QboPurchase.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "SyncToken": sync_token,
                        "RealmId": realm_id,
                        "PaymentType": payment_type,
                        "AccountRefValue": account_ref_value,
                        "AccountRefName": account_ref_name,
                        "EntityRefValue": entity_ref_value,
                        "EntityRefName": entity_ref_name,
                        "Credit": credit,
                        "TxnDate": txn_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling CreateQboPurchase with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboPurchase",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo purchase did not return a row.")
                        raise map_database_error(Exception("create qbo purchase failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo purchase: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboPurchase]:
        """
        Read all QboPurchases.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchases",
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
            logger.error(f"Error during read all qbo purchases: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboPurchase]:
        """
        Read all QboPurchases by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchasesByRealmId",
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
            logger.error(f"Error during read qbo purchases by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseById",
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
            logger.error(f"Error during read qbo purchase by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseByQboId",
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
            logger.error(f"Error during read qbo purchase by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseByQboIdAndRealmId",
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
            logger.error(f"Error during read qbo purchase by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        payment_type: Optional[str],
        account_ref_value: Optional[str],
        account_ref_name: Optional[str],
        entity_ref_value: Optional[str],
        entity_ref_name: Optional[str],
        credit: Optional[bool],
        txn_date: Optional[str],
        doc_number: Optional[str],
        private_note: Optional[str],
        total_amt: Optional[Decimal],
        currency_ref_value: Optional[str],
        currency_ref_name: Optional[str],
        exchange_rate: Optional[Decimal],
        department_ref_value: Optional[str],
        department_ref_name: Optional[str],
        global_tax_calculation: Optional[str],
    ) -> Optional[QboPurchase]:
        """
        Update a QboPurchase by QBO ID.
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
                        "PaymentType": payment_type,
                        "AccountRefValue": account_ref_value,
                        "AccountRefName": account_ref_name,
                        "EntityRefValue": entity_ref_value,
                        "EntityRefName": entity_ref_name,
                        "Credit": credit,
                        "TxnDate": txn_date,
                        "DocNumber": doc_number,
                        "PrivateNote": private_note,
                        "TotalAmt": float(total_amt) if total_amt is not None else None,
                        "CurrencyRefValue": currency_ref_value,
                        "CurrencyRefName": currency_ref_name,
                        "ExchangeRate": float(exchange_rate) if exchange_rate is not None else None,
                        "DepartmentRefValue": department_ref_value,
                        "DepartmentRefName": department_ref_name,
                        "GlobalTaxCalculation": global_tax_calculation,
                    }
                    logger.debug(f"Calling UpdateQboPurchaseByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboPurchaseByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo purchase did not return a row.")
                        raise map_database_error(Exception("update qbo purchase by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo purchase by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboPurchase]:
        """
        Delete a QboPurchase by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboPurchaseByQboId",
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
            logger.error(f"Error during delete qbo purchase by QBO ID: {error}")
            raise map_database_error(error)


class QboPurchaseLineRepository:
    """
    Repository for QboPurchaseLine persistence operations.
    """

    def __init__(self):
        """Initialize the QboPurchaseLineRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboPurchaseLine]:
        """
        Convert a database row into a QboPurchaseLine dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboPurchaseLine(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_purchase_id=getattr(row, "QboPurchaseId", None),
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
            logger.error(f"Attribute error during qbo purchase line mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo purchase line mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_purchase_id: int,
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
    ) -> QboPurchaseLine:
        """
        Create a new QboPurchaseLine.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboPurchaseId": qbo_purchase_id,
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
                        name="CreateQboPurchaseLine",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo purchase line did not return a row.")
                        raise map_database_error(Exception("create qbo purchase line failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo purchase line: {error}")
            raise map_database_error(error)

    def read_by_qbo_purchase_id(self, qbo_purchase_id: int) -> List[QboPurchaseLine]:
        """
        Read all QboPurchaseLines for a QboPurchase.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseLinesByQboPurchaseId",
                        params={"QboPurchaseId": qbo_purchase_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo purchase lines by qbo purchase ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_purchase_id_and_qbo_line_id(self, qbo_purchase_id: int, qbo_line_id: str) -> Optional[QboPurchaseLine]:
        """
        Read a QboPurchaseLine by QboPurchase ID and QBO Line ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseLineByQboPurchaseIdAndQboLineId",
                        params={"QboPurchaseId": qbo_purchase_id, "QboLineId": qbo_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo purchase line by qbo purchase ID and qbo line ID: {error}")
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
    ) -> Optional[QboPurchaseLine]:
        """
        Update a QboPurchaseLine by ID.
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
                        name="UpdateQboPurchaseLineById",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo purchase line did not return a row.")
                        raise map_database_error(Exception("update qbo purchase line by ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo purchase line by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[QboPurchaseLine]:
        """
        Delete a QboPurchaseLine by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboPurchaseLineById",
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
            logger.error(f"Error during delete qbo purchase line by ID: {error}")
            raise map_database_error(error)

    def read_lines_needing_update(self, realm_id: Optional[str] = None) -> List[dict]:
        """
        Read purchase lines with AccountRefName = 'NEED TO UPDATE' and no ExpenseLineItem link.
        Returns list of dicts with QboPurchaseId, QboPurchasePublicId, DocNumber, TxnDate,
        EntityRefName, RealmId, QboPurchaseLineId, LineNum, LineDescription, LineAmount, AccountRefName.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboPurchaseLinesNeedingUpdate",
                        params={"RealmId": realm_id},
                    )
                    rows = cursor.fetchall()
                    result = []
                    for row in rows:
                        if not row:
                            continue
                        qbo_purchase_public_id = getattr(row, "QboPurchasePublicId", None)
                        result.append({
                            "qbo_purchase_id": getattr(row, "QboPurchaseId", None),
                            "qbo_purchase_public_id": str(qbo_purchase_public_id) if qbo_purchase_public_id else None,
                            "doc_number": getattr(row, "DocNumber", None),
                            "txn_date": getattr(row, "TxnDate", None),
                            "entity_ref_name": getattr(row, "EntityRefName", None),
                            "realm_id": getattr(row, "RealmId", None),
                            "qbo_purchase_line_id": getattr(row, "QboPurchaseLineId", None),
                            "line_num": getattr(row, "LineNum", None),
                            "line_description": getattr(row, "LineDescription", None),
                            "line_amount": float(getattr(row, "LineAmount", 0)) if getattr(row, "LineAmount", None) is not None else None,
                            "account_ref_name": getattr(row, "AccountRefName", None),
                        })
                    return result
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read lines needing update: {error}")
            raise map_database_error(error)
