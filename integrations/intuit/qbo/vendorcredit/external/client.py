# Python Standard Library Imports
import logging
from typing import Any, List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient, _format_datetime_for_qbo_query
from integrations.intuit.qbo.base.errors import QboUnexpectedError
from integrations.intuit.qbo.base.ids import normalize_qbo_id
from integrations.intuit.qbo.base.paging import page_all_record_ids
from integrations.intuit.qbo.vendorcredit.external.schemas import (
    QboVendorCredit,
    QboVendorCreditCreate,
    QboVendorCreditResponse,
    QboVendorCreditUpdate,
)

logger = logging.getLogger(__name__)

_BY_IDS_BATCH_SIZE = 100


def _strict_parse_vendor_credit_query_response(
    data: Any, *, where: str
) -> List[QboVendorCredit]:
    if not isinstance(data, dict):
        raise QboUnexpectedError(
            "QBO VendorCredit query returned a non-dict body",
            detail=where,
        )
    if "Fault" in data:
        raise QboUnexpectedError(
            "QBO VendorCredit query returned a Fault",
            detail=where,
        )
    if "QueryResponse" not in data:
        raise QboUnexpectedError(
            "QBO VendorCredit query returned no QueryResponse",
            detail=where,
        )
    query_response = data.get("QueryResponse")
    if not isinstance(query_response, dict):
        raise QboUnexpectedError(
            "QBO VendorCredit query returned a null or non-dict QueryResponse",
            detail=where,
        )
    if "Fault" in query_response:
        raise QboUnexpectedError(
            "QBO VendorCredit query returned a Fault inside QueryResponse",
            detail=where,
        )
    if "VendorCredit" not in query_response:
        rows: List[Any] = []
    else:
        rows = query_response.get("VendorCredit")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise QboUnexpectedError(
            "QBO VendorCredit query returned a non-list VendorCredit payload",
            detail=where,
        )
    return [QboVendorCredit(**vc) for vc in rows]


class QboVendorCreditClient:
    """
    Client for QBO VendorCredit endpoints. Composes `QboHttpClient` for transport.
    """

    DEFAULT_MINOR_VERSION = 73

    def __init__(
        self,
        *,
        realm_id: str,
        http_client: Optional[QboHttpClient] = None,
        minor_version: int = DEFAULT_MINOR_VERSION,
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

    def __enter__(self) -> "QboVendorCreditClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_vendor_credit(self, vendor_credit_id: str) -> QboVendorCredit:
        """Get a single VendorCredit by ID."""
        data = self._http_client.get(
            f"vendorcredit/{vendor_credit_id}",
            operation_name="qbo.vendorcredit.get",
        )
        return QboVendorCreditResponse(**data).vendor_credit

    def query_vendor_credits(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboVendorCredit]:
        """Query VendorCredits with optional date filters."""
        where_clauses: List[str] = []

        if last_updated_time:
            formatted_time = _format_datetime_for_qbo_query(last_updated_time, logger=logger)
            where_clauses.append(f"MetaData.LastUpdatedTime > '{formatted_time}'")

        if start_date:
            where_clauses.append(f"TxnDate >= '{start_date}'")

        if end_date:
            where_clauses.append(f"TxnDate <= '{end_date}'")

        query = "SELECT * FROM VendorCredit"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += f" STARTPOSITION {start_position} MAXRESULTS {max_results}"

        data = self._http_client.get(
            "query",
            params={"query": query},
            operation_name="qbo.vendorcredit.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        vendor_credits_data = query_response.get("VendorCredit", [])
        if not vendor_credits_data:
            return []
        if isinstance(vendor_credits_data, dict):
            return [QboVendorCredit(**vendor_credits_data)]
        return [QboVendorCredit(**vc) for vc in vendor_credits_data]

    def query_all_vendor_credits(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[QboVendorCredit]:
        """Query all VendorCredits, handling pagination."""
        all_vendor_credits: List[QboVendorCredit] = []
        start_position = 1
        max_results = 1000

        while True:
            batch = self.query_vendor_credits(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                start_position=start_position,
                max_results=max_results,
            )
            if not batch:
                break
            all_vendor_credits.extend(batch)
            if len(batch) < max_results:
                break
            start_position += max_results

        return all_vendor_credits

    def query_all_vendor_credit_ids(self) -> List[str]:
        """
        Page the complete set of live QBO VendorCredit ids for this realm.

        STRICT — raises on any anomaly rather than returning a short list, so a
        caller diffing local mappings against this set can never flag LIVE
        records as deleted off a truncated fetch. Do NOT substitute
        `query_all_vendor_credits` for this: it swallows a missing QueryResponse as an
        empty page and ends pagination early. See base/paging.py.
        """
        return page_all_record_ids(
            self._http_client,
            entity="VendorCredit",
            operation_name="qbo.vendorcredit.query_ids",
        )

    def query_vendor_credits_by_ids(self, ids: List[str]) -> List[QboVendorCredit]:
        """Fetch full VendorCredit records by QBO Id (batched, strict QueryResponse guards)."""
        if not ids:
            return []
        all_vendor_credits: List[QboVendorCredit] = []
        for offset in range(0, len(ids), _BY_IDS_BATCH_SIZE):
            batch = ids[offset : offset + _BY_IDS_BATCH_SIZE]
            numeric_ids: List[str] = []
            for raw_id in batch:
                norm = normalize_qbo_id(raw_id)
                if not norm or not norm.isdigit():
                    logger.warning(
                        "Skipping non-numeric QBO VendorCredit id for by-ids query: %r",
                        raw_id,
                    )
                    continue
                numeric_ids.append(norm)
            if not numeric_ids:
                continue
            in_clause = ",".join(f"'{i}'" for i in numeric_ids)
            query_string = (
                f"SELECT * FROM VendorCredit WHERE Id IN ({in_clause}) MAXRESULTS 1000"
            )
            where = f"entity=VendorCredit ids={len(numeric_ids)}"
            data = self._http_client.get(
                "query",
                params={"query": query_string},
                operation_name="qbo.vendorcredit.query_by_ids",
            )
            all_vendor_credits.extend(
                _strict_parse_vendor_credit_query_response(data, where=where)
            )
        return all_vendor_credits

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_vendor_credit(
        self,
        vendor_credit: QboVendorCreditCreate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboVendorCredit:
        """
        Create a new VendorCredit in QBO.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.
        """
        data = self._http_client.post(
            "vendorcredit",
            json=vendor_credit.model_dump(by_alias=True, exclude_none=True),
            idempotency_key=idempotency_key,
            operation_name="qbo.vendorcredit.create",
        )
        return QboVendorCreditResponse(**data).vendor_credit

    def update_vendor_credit(
        self,
        vendor_credit: QboVendorCreditUpdate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboVendorCredit:
        """Update an existing VendorCredit in QBO."""
        data = self._http_client.post(
            "vendorcredit",
            json=vendor_credit.model_dump(by_alias=True, exclude_none=True),
            idempotency_key=idempotency_key,
            operation_name="qbo.vendorcredit.update",
        )
        return QboVendorCreditResponse(**data).vendor_credit

    def delete_vendor_credit(
        self,
        vendor_credit_id: str,
        sync_token: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboVendorCredit:
        """Delete a VendorCredit from QBO (soft delete)."""
        data = self._http_client.post(
            "vendorcredit",
            params={"operation": "delete"},
            json={
                "Id": vendor_credit_id,
                "SyncToken": sync_token,
            },
            idempotency_key=idempotency_key,
            operation_name="qbo.vendorcredit.delete",
        )
        return QboVendorCreditResponse(**data).vendor_credit
