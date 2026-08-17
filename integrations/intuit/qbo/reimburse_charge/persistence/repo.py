# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.reimburse_charge.business.model import QboReimburseCharge
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboReimburseChargeRepository:
    """
    Repository for QboReimburseCharge persistence operations.

    Pull-only staging: create + upsert-update + reads. No delete sproc.
    SourceTxn* preserve on UPDATE is defensive/forward-compatible — measured
    2026-08-16 (U-242) found no reverse Bill/Purchase LinkedTxn from QBO; see
    docs/rc_source_linking_signal_2026_08_16.md.
    """

    def __init__(self):
        """Initialize the QboReimburseChargeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboReimburseCharge]:
        """
        Convert a database row into a QboReimburseCharge dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            has_been_invoiced = getattr(row, "HasBeenInvoiced", None)
            return QboReimburseCharge(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                realm_id=getattr(row, "RealmId", None),
                customer_ref_value=getattr(row, "CustomerRefValue", None),
                customer_ref_name=getattr(row, "CustomerRefName", None),
                txn_date=getattr(row, "TxnDate", None),
                amount=Decimal(str(getattr(row, "Amount"))) if getattr(row, "Amount", None) is not None else None,
                has_been_invoiced=bool(has_been_invoiced) if has_been_invoiced is not None else None,
                source_txn_type=getattr(row, "SourceTxnType", None),
                source_txn_id=getattr(row, "SourceTxnId", None),
                source_txn_line_id=getattr(row, "SourceTxnLineId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo reimburse charge mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo reimburse charge mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        txn_date: Optional[str],
        amount: Optional[Decimal],
        has_been_invoiced: Optional[bool],
        source_txn_type: Optional[str],
        source_txn_id: Optional[str],
        source_txn_line_id: Optional[str],
    ) -> QboReimburseCharge:
        """
        Create a new QboReimburseCharge.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "RealmId": realm_id,
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "TxnDate": txn_date,
                        "Amount": float(amount) if amount is not None else None,
                        "HasBeenInvoiced": int(has_been_invoiced) if has_been_invoiced is not None else None,
                        "SourceTxnType": source_txn_type,
                        "SourceTxnId": source_txn_id,
                        "SourceTxnLineId": source_txn_line_id,
                    }
                    logger.debug(f"Calling CreateQboReimburseCharge with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboReimburseCharge",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo reimburse charge did not return a row.")
                        raise map_database_error(Exception("create qbo reimburse charge failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo reimburse charge: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboReimburseCharge]:
        """
        Read all QboReimburseCharges by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboReimburseChargesByRealmId",
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
            logger.error(f"Error during read qbo reimburse charges by realm ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboReimburseCharge]:
        """
        Read a QboReimburseCharge by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboReimburseChargeByQboIdAndRealmId",
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
            logger.error(f"Error during read qbo reimburse charge by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        realm_id: Optional[str],
        customer_ref_value: Optional[str],
        customer_ref_name: Optional[str],
        txn_date: Optional[str],
        amount: Optional[Decimal],
        has_been_invoiced: Optional[bool],
        source_txn_type: Optional[str],
        source_txn_id: Optional[str],
        source_txn_line_id: Optional[str],
    ) -> Optional[QboReimburseCharge]:
        """
        Update a QboReimburseCharge by QBO ID.

        The sproc CASE-WHEN-preserves SourceTxn* when the passed value is NULL,
        so a re-pull never nulls a stored pointer (defensive — QBO does not
        currently populate these fields; see docs/rc_source_linking_signal_2026_08_16.md).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "QboId": qbo_id,
                        "RowVersion": row_version,
                        "RealmId": realm_id,
                        "CustomerRefValue": customer_ref_value,
                        "CustomerRefName": customer_ref_name,
                        "TxnDate": txn_date,
                        "Amount": float(amount) if amount is not None else None,
                        "HasBeenInvoiced": int(has_been_invoiced) if has_been_invoiced is not None else None,
                        "SourceTxnType": source_txn_type,
                        "SourceTxnId": source_txn_id,
                        "SourceTxnLineId": source_txn_line_id,
                    }
                    logger.debug(f"Calling UpdateQboReimburseChargeByQboId with QboId: {qbo_id}, RealmId: {realm_id}")
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboReimburseChargeByQboId",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo reimburse charge did not return a row.")
                        raise map_database_error(Exception("update qbo reimburse charge by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo reimburse charge by QBO ID: {error}")
            raise map_database_error(error)
