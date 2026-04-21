# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.account.external.schemas import (
    QboAccount,
    QboAccountResponse,
)

logger = logging.getLogger(__name__)


def _format_datetime_for_qbo_query(datetime_input) -> Optional[str]:
    """
    Format a datetime (string or datetime object) for a QBO query WHERE clause.
    QBO expects ISO 8601 with a timezone offset: 'YYYY-MM-DDTHH:MM:SS+HH:MM'.
    """
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


class QboAccountClient:
    """
    Client for QBO Account endpoints.

    Composes `QboHttpClient` for transport: auth, retry, idempotency,
    structured logging, metrics, and typed error mapping all happen in
    the shared layer. This class focuses on entity-specific payload
    shape and response parsing.
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

    def __enter__(self) -> "QboAccountClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_account(self, account_id: str) -> QboAccount:
        """Retrieve a single account by ID from QuickBooks."""
        data = self._http_client.get(
            f"account/{account_id}",
            operation_name="qbo.account.get",
        )
        return QboAccountResponse(**data).account

    def query_accounts(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboAccount]:
        """
        Query accounts from QuickBooks using the query endpoint.

        Args:
            last_updated_time: Optional ISO datetime. If provided, only returns
                accounts where Metadata.LastUpdatedTime > last_updated_time.
            start_position: 1-based pagination offset.
            max_results: Max rows per page (QBO caps at 1000).
        """
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM Account WHERE Metadata.LastUpdatedTime > '{formatted}' "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
            logger.debug(
                f"Querying Accounts with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = (
                f"SELECT * FROM Account STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.account.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        accounts_data = query_response.get("Account", [])
        if not accounts_data:
            return []
        if isinstance(accounts_data, dict):
            return [QboAccount(**accounts_data)]
        return [QboAccount(**account) for account in accounts_data]

    def query_all_accounts(self, last_updated_time: Optional[str] = None) -> List[QboAccount]:
        """Query all accounts from QuickBooks, handling pagination."""
        all_accounts: List[QboAccount] = []
        start_position = 1
        max_results = 1000

        while True:
            accounts = self.query_accounts(
                last_updated_time=last_updated_time,
                start_position=start_position,
                max_results=max_results,
            )
            if not accounts:
                break
            all_accounts.extend(accounts)
            if len(accounts) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_accounts)} accounts from QBO")
        return all_accounts
