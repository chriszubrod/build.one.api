# Python Standard Library Imports
import logging
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient, _format_datetime_for_qbo_query
from integrations.intuit.qbo.item.external.schemas import (
    QboItem,
    QboItemResponse,
)

logger = logging.getLogger(__name__)


class QboItemClient:
    """
    Client for QBO Item endpoints. Composes `QboHttpClient` for transport.
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

    def __enter__(self) -> "QboItemClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_item(self, item_id: str) -> QboItem:
        """Retrieve a single item by ID from QuickBooks."""
        data = self._http_client.get(f"item/{item_id}", operation_name="qbo.item.get")
        return QboItemResponse(**data).item

    def query_items(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboItem]:
        """Query items from QuickBooks using the query endpoint."""
        where_clauses: List[str] = []
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time, logger=logger)
            where_clauses.append(f"Metadata.LastUpdatedTime > '{formatted}'")
            logger.debug(
                f"Querying Items with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        # QBO's query API hides inactive records by default, so a deactivation (and every
        # merge, which deactivates the merged-from record) would otherwise never reach us (U-219).
        where_clauses.append("Active IN (true, false)")
        query_string = (
            f"SELECT * FROM Item WHERE {' AND '.join(where_clauses)} "
            f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
        )

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.item.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        items_data = query_response.get("Item", [])
        if not items_data:
            return []
        if isinstance(items_data, dict):
            return [QboItem(**items_data)]
        return [QboItem(**item) for item in items_data]

    def query_all_items(self, last_updated_time: Optional[str] = None) -> List[QboItem]:
        """Query all items from QuickBooks, handling pagination."""
        all_items: List[QboItem] = []
        start_position = 1
        max_results = 1000

        while True:
            items = self.query_items(
                last_updated_time=last_updated_time,
                start_position=start_position,
                max_results=max_results,
            )
            if not items:
                break
            all_items.extend(items)
            if len(items) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_items)} items from QBO")
        return all_items
