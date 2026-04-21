# Python Standard Library Imports
import logging
from datetime import datetime
from typing import List, Optional

# Local Imports
from integrations.intuit.qbo.base.client import QboHttpClient
from integrations.intuit.qbo.vendor.external.schemas import (
    QboVendor,
    QboVendorCreate,
    QboVendorResponse,
    QboVendorUpdate,
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


class QboVendorClient:
    """
    Client for QBO Vendor endpoints. Composes `QboHttpClient` for transport.
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

    def __enter__(self) -> "QboVendorClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def create_vendor(self, vendor: QboVendorCreate, *, idempotency_key: Optional[str] = None) -> QboVendor:
        """
        Create a vendor in QuickBooks.

        The `idempotency_key` is a stable QBO `?requestid=` value. Pass a
        caller-supplied key from the outbox row so retries deduplicate on
        QBO's side. When omitted, the shared client auto-generates a fresh
        UUID per call.
        """
        payload = {"Vendor": vendor.dict(by_alias=True, exclude_none=True)}
        data = self._http_client.post(
            "vendor",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.vendor.create",
        )
        return QboVendorResponse(**data).vendor

    def update_vendor(self, vendor: QboVendorUpdate, *, idempotency_key: Optional[str] = None) -> QboVendor:
        """
        Update a vendor in QuickBooks.

        Note: QBO expects vendor fields at root level for updates, not wrapped in {"Vendor": ...}.
        """
        payload = vendor.dict(by_alias=True, exclude_none=True)
        data = self._http_client.post(
            "vendor",
            json=payload,
            idempotency_key=idempotency_key,
            operation_name="qbo.vendor.update",
        )
        return QboVendorResponse(**data).vendor

    def delete_vendor(
        self,
        vendor_id: str,
        *,
        sync_token: str,
        idempotency_key: Optional[str] = None,
    ) -> QboVendor:
        """Delete (or deactivate) a vendor in QuickBooks."""
        payload = {
            "Vendor": {
                "Id": vendor_id,
                "SyncToken": sync_token,
            },
        }
        data = self._http_client.post(
            "vendor",
            json=payload,
            params={"operation": "delete"},
            idempotency_key=idempotency_key,
            operation_name="qbo.vendor.delete",
        )
        return QboVendorResponse(**data).vendor

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #

    def get_vendor(self, vendor_id: str) -> QboVendor:
        """Retrieve a single vendor by ID from QuickBooks."""
        data = self._http_client.get(
            f"vendor/{vendor_id}",
            operation_name="qbo.vendor.get",
        )
        return QboVendorResponse(**data).vendor

    def query_vendors(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboVendor]:
        """Query vendors from QuickBooks using the query endpoint."""
        if last_updated_time:
            formatted = _format_datetime_for_qbo_query(last_updated_time)
            query_string = (
                f"SELECT * FROM Vendor WHERE Metadata.LastUpdatedTime > '{formatted}' "
                f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )
            logger.debug(
                f"Querying Vendors with WHERE clause: Metadata.LastUpdatedTime > '{formatted}'"
            )
        else:
            query_string = (
                f"SELECT * FROM Vendor STARTPOSITION {start_position} MAXRESULTS {max_results}"
            )

        data = self._http_client.get(
            "query",
            params={"query": query_string},
            operation_name="qbo.vendor.query",
        )

        query_response = data.get("QueryResponse") if isinstance(data, dict) else None
        if not query_response:
            return []

        vendors_data = query_response.get("Vendor", [])
        if not vendors_data:
            return []
        if isinstance(vendors_data, dict):
            return [QboVendor(**vendors_data)]
        return [QboVendor(**vendor) for vendor in vendors_data]

    def query_all_vendors(self, last_updated_time: Optional[str] = None) -> List[QboVendor]:
        """Query all vendors from QuickBooks, handling pagination."""
        all_vendors: List[QboVendor] = []
        start_position = 1
        max_results = 1000

        while True:
            vendors = self.query_vendors(
                last_updated_time=last_updated_time,
                start_position=start_position,
                max_results=max_results,
            )
            if not vendors:
                break
            all_vendors.extend(vendors)
            if len(vendors) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_vendors)} vendors from QBO")
        return all_vendors
