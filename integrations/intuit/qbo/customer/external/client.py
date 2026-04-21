# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.customer.external.schemas import (
    QboCustomer,
    QboCustomerResponse,
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


class QboCustomerClient:
    """
    Client for QBO Customer endpoints. Composes `QboHttpClient` for transport.
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

    def __enter__(self) -> "QboCustomerClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_customer(self, customer_id: str) -> QboCustomer:
        """Retrieve a single customer by ID from QuickBooks."""
        data = self._http_client.get(
            f"customer/{customer_id}",
            operation_name="qbo.customer.get",
        )
        return QboCustomerResponse(**data).customer

    def query_customers(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboCustomer]:
        """Query customers from QuickBooks using the query endpoint."""
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM Customer WHERE Metadata.LastUpdatedTime > '{formatted}' "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
            logger.debug(
                f"Querying Customers with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = (
                f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.customer.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        customers_data = query_response.get("Customer", [])
        if not customers_data:
            return []
        if isinstance(customers_data, dict):
            return [QboCustomer(**customers_data)]
        return [QboCustomer(**customer) for customer in customers_data]

    def query_all_customers(self, last_updated_time: Optional[str] = None) -> List[QboCustomer]:
        """Query all customers from QuickBooks, handling pagination."""
        all_customers: List[QboCustomer] = []
        start_position = 1
        max_results = 1000

        while True:
            customers = self.query_customers(
                last_updated_time=last_updated_time,
                start_position=start_position,
                max_results=max_results,
            )
            if not customers:
                break
            all_customers.extend(customers)
            if len(customers) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_customers)} customers from QBO")
        return all_customers
