# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.purchase.external.schemas import (
    QboPurchase,
    QboPurchaseCreate,
    QboPurchaseResponse,
    QboPurchaseUpdate,
)

logger = logging.getLogger(__name__)


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


class QboPurchaseClient:
    """
    Client for QBO Purchase endpoints. Composes `QboHttpClient` for transport.
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

    def __enter__(self) -> "QboPurchaseClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_purchase(
        self,
        purchase: QboPurchaseCreate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboPurchase:
        """
        Create a purchase in QuickBooks.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.
        """
        payload = purchase.model_dump(by_alias=True, exclude_none=True, mode="json")
        data = self._http_client.post(
            "purchase",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.purchase.create",
        )
        return QboPurchaseResponse(**data).purchase

    def update_purchase(
        self,
        purchase: QboPurchaseUpdate,
        *,
        idempotency_key: Optional[str] = None,
    ) -> QboPurchase:
        """Update a purchase in QuickBooks."""
        payload = purchase.model_dump(by_alias=True, exclude_none=True, mode="json")
        data = self._http_client.post(
            "purchase",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.purchase.update",
        )
        return QboPurchaseResponse(**data).purchase

    def delete_purchase(
        self,
        purchase_id: str,
        *,
        sync_token: str,
        idempotency_key: Optional[str] = None,
    ) -> QboPurchase:
        """Delete a purchase in QuickBooks."""
        payload = {
            "Id": purchase_id,
            "SyncToken": sync_token,
        }
        data = self._http_client.post(
            "purchase",
            params={"operation": "delete"},
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.purchase.delete",
        )
        return QboPurchaseResponse(**data).purchase

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_purchase(self, purchase_id: str) -> QboPurchase:
        """Retrieve a single purchase by ID from QuickBooks."""
        data = self._http_client.get(
            f"purchase/{purchase_id}",
            operation_name="qbo.purchase.get",
        )
        return QboPurchaseResponse(**data).purchase

    def query_purchases(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboPurchase]:
        """Query purchases from QuickBooks with optional date filters."""
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

        if where_clauses:
            where_clause = " AND ".join(where_clauses)
            query_string = (
                f"SELECT * FROM Purchase WHERE {where_clause} "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
        else:
            query_string = (
                f"SELECT * FROM Purchase STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        logger.debug(f"QBO Query: {query_string}")
        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.purchase.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        purchases_data = query_response.get("Purchase", [])
        if not purchases_data:
            return []
        if isinstance(purchases_data, dict):
            return [QboPurchase(**purchases_data)]
        return [QboPurchase(**purchase) for purchase in purchases_data]

    def query_all_purchases(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[QboPurchase]:
        """Query all purchases from QuickBooks, handling pagination."""
        all_purchases: List[QboPurchase] = []
        start_position = 1
        max_results = 1000

        while True:
            purchases = self.query_purchases(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                start_position=start_position,
                max_results=max_results,
            )
            if not purchases:
                break
            all_purchases.extend(purchases)
            if len(purchases) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_purchases)} purchases from QBO")
        return all_purchases
