# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.base.errors import QboValidationError
from integrations.intuit.qbo.invoice.external.schemas import (
    QboInvoice,
    QboInvoiceCreate,
    QboInvoiceResponse,
    QboInvoiceUpdate,
)

logger = logging.getLogger(__name__)


def reject_reimburse_charge_txndate_filter(
    start_date: Optional[str], end_date: Optional[str]
) -> None:
    """
    QBO rejects TxnDate filters on ReimburseCharge outright (confirmed empirically,
    2026-08-16 — 'SELECT * FROM ReimburseCharge WHERE TxnDate ...' -> "Invalid
    query"). Single source of truth for this guard: called both here (the query
    builder) and by QboReimburseChargeService.sync_from_qbo (which must fail before
    even constructing this client) so the condition and message can't drift apart.
    """
    if start_date or end_date:
        raise QboValidationError(
            "ReimburseCharge does not support TxnDate filtering — QBO rejects "
            "'SELECT * FROM ReimburseCharge WHERE TxnDate ...' outright (confirmed "
            "empirically, 2026-08-16). Supported filters for this entity are "
            "Metadata.LastUpdatedTime (incremental, via last_updated_time) and "
            "boolean/reference fields (HasBeenInvoiced, CustomerRef). Do not pass "
            "start_date/end_date to query_reimburse_charges_page/query_all_reimburse_charges."
        )


def _format_datetime_for_qbo_query(datetime_input) -> Optional[str]:
    """Format a datetime for a QBO query WHERE clause (ISO 8601 with +HH:MM offset)."""
    if not datetime_input:
        return None if datetime_input is None else str(datetime_input)

    if isinstance(datetime_input, datetime):
        datetime_str = datetime_input.isoformat()
    else:
        datetime_str = str(datetime_input)

    dt_str = datetime_str.rstrip("Z")
    if dt_str.endswith("+00:00"):
        dt_str = dt_str[:-6]

    try:
        if "T" in dt_str:
            if "." in dt_str:
                dt_str = dt_str.split(".")[0]
            if dt_str.count(":") == 1:
                dt_str += ":00"
        else:
            dt_str += "T00:00:00"
        return f"{dt_str}+00:00"
    except Exception as error:
        logger.warning(
            f"Failed to format datetime '{datetime_str}' for QBO query: {error}. Using as-is."
        )
        return datetime_str


class QboInvoiceClient:
    """
    Client for QBO Invoice endpoints. Composes `QboHttpClient` for transport.
    """

    def __init__(
        self,
        *,
        realm_id: str,
        http_client: Optional[QboHttpClient] = None,
        minor_version: int = 65,
    ):
        self.realm_id = realm_id
        self._owns_http_client = http_client is None
        self._http_client = http_client or QboHttpClient(
            realm_id=realm_id,
            minor_version=minor_version,
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "QboInvoiceClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_invoice(
        self,
        invoice: QboInvoiceCreate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboInvoice:
        """
        Create an invoice in QuickBooks.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.
        """
        payload = invoice.model_dump(by_alias=True, exclude_none=True, mode="json")
        data = self._http_client.post(
            "invoice",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.invoice.create",
        )
        return QboInvoiceResponse(**data).invoice

    def update_invoice(
        self,
        invoice: QboInvoiceUpdate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboInvoice:
        """
        Update an invoice in QuickBooks.

        Note: QBO requires a full-record update (sparse=False or all fields)
        to avoid losing existing lines.
        """
        payload = invoice.model_dump(by_alias=True, exclude_none=True, mode="json")
        data = self._http_client.post(
            "invoice",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.invoice.update",
        )
        return QboInvoiceResponse(**data).invoice

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_invoice(self, invoice_id: str) -> QboInvoice:
        """Retrieve a single invoice by ID from QuickBooks."""
        data = self._http_client.get(
            f"invoice/{invoice_id}",
            operation_name="qbo.invoice.get",
        )
        return QboInvoiceResponse(**data).invoice

    def query_invoices(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        customer_ref: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboInvoice]:
        """Query invoices from QuickBooks with optional filters."""
        where_clauses: List[str] = []

        if last_updated_time:
            formatted_time = _format_datetime_for_qbo_query(last_updated_time)
            where_clauses.append(f"Metadata.LastUpdatedTime > '{formatted_time}'")
            logger.debug(f"Adding WHERE clause: Metadata.LastUpdatedTime > '{formatted_time}'")

        if start_date:
            where_clauses.append(f"TxnDate >= '{start_date}'")
            logger.debug(f"Adding WHERE clause: TxnDate >= '{start_date}'")

        if end_date:
            where_clauses.append(f"TxnDate <= '{end_date}'")
            logger.debug(f"Adding WHERE clause: TxnDate <= '{end_date}'")

        if customer_ref:
            where_clauses.append(f"CustomerRef = '{customer_ref}'")
            logger.debug(f"Adding WHERE clause: CustomerRef = '{customer_ref}'")

        if where_clauses:
            where_clause = " AND ".join(where_clauses)
            query_string = (
                f"SELECT * FROM Invoice WHERE {where_clause} "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
        else:
            query_string = (
                f"SELECT * FROM Invoice STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        logger.debug(f"QBO Query: {query_string}")
        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.invoice.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        invoices_data = query_response.get("Invoice", [])
        if not invoices_data:
            return []
        if isinstance(invoices_data, dict):
            return [QboInvoice(**invoices_data)]
        return [QboInvoice(**invoice) for invoice in invoices_data]

    def query_all_invoices(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        customer_ref: Optional[str] = None,
    ) -> List[QboInvoice]:
        """Query all invoices from QuickBooks, handling pagination."""
        all_invoices: List[QboInvoice] = []
        start_position = 1
        max_results = 1000

        while True:
            invoices = self.query_invoices(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                customer_ref=customer_ref,
                start_position=start_position,
                max_results=max_results,
            )
            if not invoices:
                break
            all_invoices.extend(invoices)
            if len(invoices) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_invoices)} invoices from QBO")
        return all_invoices

    def query_reimburse_charges(self, customer_ref: str) -> List[Dict[str, Any]]:
        """
        Query ReimburseCharge records from QuickBooks for a given customer.

        QBO automatically creates a ReimburseCharge for every Bill/Purchase line
        marked Billable with a CustomerRef. These are the intermediate records
        that appear as "Suggested Transactions" in QBO's invoice UI. Each
        ReimburseCharge carries a LinkedTxn back to the source Bill/Purchase
        and line, which is what we use to build the LinkedTxn on invoice lines.

        Args:
            customer_ref: QBO Customer ID (the ``value`` from CustomerRef)

        Returns:
            List of raw ReimburseCharge dicts from QBO
        """
        query_string = f"SELECT * FROM ReimburseCharge WHERE CustomerRef = '{customer_ref}'"
        logger.info(f"Querying ReimburseCharge for customer {customer_ref}")
        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.invoice.reimburse_charge_query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        records = query_response.get("ReimburseCharge", [])
        if not records:
            logger.info(f"No ReimburseCharge records found for customer {customer_ref}")
            return []
        if isinstance(records, dict):
            records = [records]
        logger.info(f"ReimburseCharge query result ({len(records)} records): {records}")
        return records

    def query_reimburse_charges_page(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Query one page of realm-wide ReimburseCharge records with optional filters.

        Returns raw QBO RC dicts (the loosely-shaped payload consumed by
        `parse_reimburse_charge`). Used by the durable RC staging pull (U-186).
        """
        where_clauses: List[str] = []

        if last_updated_time:
            formatted_time = _format_datetime_for_qbo_query(last_updated_time)
            where_clauses.append(f"Metadata.LastUpdatedTime > '{formatted_time}'")
            logger.debug(f"Adding WHERE clause: Metadata.LastUpdatedTime > '{formatted_time}'")

        reject_reimburse_charge_txndate_filter(start_date, end_date)

        if where_clauses:
            where_clause = " AND ".join(where_clauses)
            query_string = (
                f"SELECT * FROM ReimburseCharge WHERE {where_clause} "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
        else:
            query_string = (
                f"SELECT * FROM ReimburseCharge STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        logger.debug(f"QBO Query: {query_string}")
        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.invoice.reimburse_charge_query_all",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        records = query_response.get("ReimburseCharge", [])
        if not records:
            return []
        if isinstance(records, dict):
            return [records]
        return records

    def query_all_reimburse_charges(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query all realm-wide ReimburseCharge records, handling pagination.

        Mirrors `query_all_invoices`. Returns raw QBO RC dicts for the durable
        staging pull (U-186) — captured while un-invoiced so the RC's reverse
        LinkedTxn (dropped once HasBeenInvoiced=true, KI-32) is preserved.
        """
        all_records: List[Dict[str, Any]] = []
        start_position = 1
        max_results = 1000

        while True:
            records = self.query_reimburse_charges_page(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                start_position=start_position,
                max_results=max_results,
            )
            if not records:
                break
            all_records.extend(records)
            if len(records) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_records)} reimburse charges from QBO")
        return all_records
