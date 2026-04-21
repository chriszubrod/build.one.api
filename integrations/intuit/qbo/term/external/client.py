# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.term.external.schemas import (
    QboTerm,
    QboTermCreate,
    QboTermResponse,
    QboTermUpdate,
)

logger = logging.getLogger(__name__)


def _format_datetime_for_qbo_query(datetime_input) -> Optional[str]:
    """
    Format datetime string or datetime object for QBO query WHERE clause.
    QBO expects ISO 8601 format with timezone offset: 'YYYY-MM-DDTHH:MM:SS-HH:MM'

    Args:
        datetime_input: ISO format datetime string (may end with Z or +00:00)
                        or datetime.datetime object.

    Returns:
        Formatted datetime string for QBO query, or None/original if input
        is falsy.
    """
    if not datetime_input:
        return None if datetime_input is None else str(datetime_input)

    # Convert datetime object to ISO string if needed
    if isinstance(datetime_input, datetime):
        datetime_str = datetime_input.isoformat()
    else:
        datetime_str = str(datetime_input)

    # Remove Z suffix if present
    dt_str = datetime_str.rstrip("Z")

    # If ends with +00:00, remove it (we'll add timezone later if needed)
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


class QboTermClient:
    """
    Client for QBO Term endpoints.

    Composes `QboHttpClient` for transport: auth injection, retry with
    backoff, idempotency key injection on writes, structured logging,
    metrics emission, and typed error mapping all happen in the shared
    layer. This class focuses on entity-specific payload shape and
    response parsing.
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

    def __enter__(self) -> "QboTermClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_term(self, term: QboTermCreate, *, idempotency_key: Optional[str] = None) -> QboTerm:
        """
        Create a term in QuickBooks.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.
        """
        payload = {"Term": term.dict(by_alias=True, exclude_none=True)}
        data = self._http_client.post(
            "term",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.term.create",
        )
        return QboTermResponse(**data).term

    def update_term(self, term: QboTermUpdate, *, idempotency_key: Optional[str] = None) -> QboTerm:
        """Update a term in QuickBooks."""
        payload = {"Term": term.dict(by_alias=True, exclude_none=True)}
        data = self._http_client.post(
            "term",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.term.update",
        )
        return QboTermResponse(**data).term

    def delete_term(
        self,
        term_id: str,
        *,
        sync_token: str,
        idempotency_key: Optional[str] = None,
    ) -> QboTerm:
        """Delete (or deactivate) a term in QuickBooks."""
        payload = {
            "Term": {
                "Id": term_id,
                "SyncToken": sync_token,
            },
        }
        data = self._http_client.post(
            "term",
            json=payload,
            params={"operation": "delete"},
            idempotency_key=idempotency_key,
            operation_name="qbo.term.delete",
        )
        return QboTermResponse(**data).term

    def get_term(self, term_id: str) -> QboTerm:
        """Retrieve a single term by ID from QuickBooks."""
        data = self._http_client.get(f"term/{term_id}", operation_name="qbo.term.get")
        return QboTermResponse(**data).term

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #

    def query_terms(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboTerm]:
        """
        Query terms from QuickBooks using the query endpoint.

        Args:
            last_updated_time: Optional ISO datetime. If provided, only returns
                terms where Metadata.LastUpdatedTime > last_updated_time.
            start_position: 1-based pagination offset.
            max_results: Max rows per page (QBO caps at 1000).
        """
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM Term WHERE Metadata.LastUpdatedTime > '{formatted}' "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
            logger.debug(
                f"Querying Terms with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = (
                f"SELECT * FROM Term STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.term.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        terms_data = query_response.get("Term", [])
        if not terms_data:
            return []
        if isinstance(terms_data, dict):
            return [QboTerm(**terms_data)]
        return [QboTerm(**term) for term in terms_data]

    def query_all_terms(self, last_updated_time: Optional[str] = None) -> List[QboTerm]:
        """
        Query all terms from QuickBooks, handling pagination.
        """
        all_terms: List[QboTerm] = []
        start_position = 1
        max_results = 1000

        while True:
            terms = self.query_terms(
                last_updated_time=last_updated_time,
                start_position=start_position,
                max_results=max_results,
            )
            if not terms:
                break
            all_terms.extend(terms)
            if len(terms) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_terms)} terms from QBO")
        return all_terms
