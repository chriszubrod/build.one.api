# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.item.external.schemas import (
    QboItem,
    QboItemResponse,
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
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM Item WHERE Metadata.LastUpdatedTime > '{formatted}' "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
            logger.debug(
                f"Querying Items with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = (
                f"SELECT * FROM Item STARTPOSITION {start_position} MAXRESULTS {max_results}"
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
