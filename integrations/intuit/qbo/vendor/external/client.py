# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.vendor.external.schemas import (
    QboVendor,
    QboVendorCreate,
    QboVendorQueryResponse,
    QboVendorResponse,
    QboVendorUpdate,
)
from integrations.intuit.qbo.base.errors import (
    QboError,
    QboAuthError,
    QboValidationError,
    QboRateLimitError,
    QboConflictError,
    QboNotFoundError,
)

logger = logging.getLogger(__name__)


def _format_datetime_for_qbo_query(datetime_input) -> str:
    """
    Format datetime string or datetime object for QBO query WHERE clause.
    QBO expects ISO 8601 format with timezone offset: 'YYYY-MM-DDTHH:MM:SS-HH:MM'
    
    Args:
        datetime_input: ISO format datetime string (may end with Z or +00:00) or datetime.datetime object
    
    Returns:
        str: Formatted datetime string for QBO query
    """
    if not datetime_input:
        return None if datetime_input is None else str(datetime_input)
    
    # Convert datetime object to ISO string if needed
    if isinstance(datetime_input, datetime):
        datetime_str = datetime_input.isoformat()
    else:
        datetime_str = str(datetime_input)
    
    # Remove Z suffix if present
    dt_str = datetime_str.rstrip('Z')
    
    # If ends with +00:00, remove it (we'll add timezone later if needed)
    if dt_str.endswith('+00:00'):
        dt_str = dt_str[:-6]
    
    # Try to parse and format
    try:
        # Parse the datetime
        if 'T' in dt_str:
            # Has time component
            if '.' in dt_str:
                # Has milliseconds, remove them
                dt_str = dt_str.split('.')[0]
            # Ensure we have seconds
            if dt_str.count(':') == 1:
                dt_str += ':00'
        else:
            # Date only, add time
            dt_str += 'T00:00:00'
        
        # QBO queries work best with timezone offset format
        # Use UTC offset format: +00:00 (standard UTC representation)
        return f"{dt_str}+00:00"
    except Exception as e:
        logger.warning(f"Failed to format datetime '{datetime_str}' for QBO query: {e}. Using as-is.")
        return datetime_str


class QboVendorClient:
    """
    Lightweight client for interacting with Qbo Vendor endpoints.
    """

    def __init__(
        self,
        *,
        access_token: str,
        realm_id: str,
        base_url: str = "https://quickbooks.api.intuit.com",
        minor_version: Optional[int] = 65,
        timeout: float = 30.0,
        session: Optional[httpx.Client] = None,
    ):
        self.access_token = access_token
        self.realm_id = realm_id
        self.minor_version = minor_version
        self._owns_client = session is None
        self._client = session or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._client.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "build.one-qbo-vendor-client/1.0",
            }
        )

    def close(self):
        """
        Close the underlying HTTP client if owned by this instance.
        """
        if self._owns_client and self._client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def create_vendor(self, vendor: QboVendorCreate) -> QboVendor:
        """
        Create a vendor in QuickBooks.
        """
        payload = {"Vendor": vendor.dict(by_alias=True, exclude_none=True)}
        data = self._request("POST", "/vendor", json=payload)
        return QboVendorResponse(**data).vendor

    def update_vendor(self, vendor: QboVendorUpdate) -> QboVendor:
        """
        Update a vendor in QuickBooks.
        
        Note: QBO expects vendor fields at root level, not wrapped in {"Vendor": ...}
        """
        payload = vendor.dict(by_alias=True, exclude_none=True)
        data = self._request("POST", "/vendor", json=payload)
        return QboVendorResponse(**data).vendor

    def delete_vendor(self, vendor_id: str, *, sync_token: str) -> QboVendor:
        """
        Delete (or deactivate) a vendor in QuickBooks.
        """
        payload = {
            "Vendor": {
                "Id": vendor_id,
                "SyncToken": sync_token,
            },
        }
        data = self._request("POST", "/vendor", params={"operation": "delete"}, json=payload)
        return QboVendorResponse(**data).vendor


    def get_vendor(self, vendor_id: str) -> QboVendor:
        """
        Retrieve a single vendor by ID from QuickBooks.
        
        Args:
            vendor_id: QBO Vendor ID
        
        Returns:
            QboVendor: The vendor information
        """
        path = f"/vendor/{vendor_id}"
        data = self._request("GET", path)
        return QboVendorResponse(**data).vendor

    def query_vendors(
        self,
        last_updated_time: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboVendor]:
        """
        Query vendors from QuickBooks using the query endpoint.
        
        Args:
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Vendors where Metadata.LastUpdatedTime > last_updated_time.
            start_position: Starting position for pagination (1-based)
            max_results: Maximum number of results to return (max 1000)
        
        Returns:
            List[QboVendor]: List of vendors matching the query
        """
        path = "/query"
        
        # Build query string
        if last_updated_time:
            formatted_time = _format_datetime_for_qbo_query(last_updated_time)
            query_string = f"SELECT * FROM Vendor WHERE Metadata.LastUpdatedTime > '{formatted_time}' STARTPOSITION {start_position} MAXRESULTS {max_results}"
            logger.debug(f"Querying Vendors with WHERE clause: Metadata.LastUpdatedTime > '{formatted_time}'")
        else:
            query_string = f"SELECT * FROM Vendor STARTPOSITION {start_position} MAXRESULTS {max_results}"
        
        data = self._request("GET", path, params={"query": query_string})
        
        # Handle query response format
        if "QueryResponse" in data:
            query_response = data["QueryResponse"]
            vendors_data = query_response.get("Vendor", [])
            if not vendors_data:
                return []
            if isinstance(vendors_data, dict):
                # Single vendor returned as dict
                return [QboVendor(**vendors_data)]
            # Multiple vendors returned as list
            return [QboVendor(**vendor) for vendor in vendors_data]
        
        return []

    def query_all_vendors(self, last_updated_time: Optional[str] = None) -> List[QboVendor]:
        """
        Query all vendors from QuickBooks, handling pagination.
        
        Args:
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Vendors where Metadata.LastUpdatedTime > last_updated_time.
        
        Returns:
            List[QboVendor]: List of all vendors matching the query
        """
        all_vendors = []
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
            
            # If we got fewer vendors than requested, we're done
            if len(vendors) < max_results:
                break
            
            start_position += max_results
        
        logger.info(f"Retrieved {len(all_vendors)} vendors from QBO")
        return all_vendors

    def query_vendors_legacy(self, query: str) -> QboVendorQueryResponse:
        """
        Execute a SQL-like query against QuickBooks vendors (legacy method).
        """
        response_data = self._request(
            "POST",
            "/query",
            content=query.encode("utf-8"),
            headers={"Content-Type": "application/text"},
        )
        query_response = response_data.get("QueryResponse", {})
        return QboVendorQueryResponse(**query_response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Issue an HTTP request against the QuickBooks API.
        """
        url_path = self._build_path(path)
        query_params = dict(params or {})
        if self.minor_version is not None and "minorversion" not in query_params:
            query_params["minorversion"] = self.minor_version

        logger.debug(
            "QuickBooks request",
            extra={
                "method": method,
                "url": url_path,
                "params": query_params,
                "has_payload": bool(json or content),
            },
        )

        response = self._client.request(
            method=method,
            url=url_path,
            params=query_params or None,
            json=json,
            content=content,
            headers=headers,
        )

        return self._handle_response(response)

    def _build_path(self, path: str) -> str:
        """
        Construct the QuickBooks API path for the configured realm.
        """
        clean_path = path if path.startswith("/") else f"/{path}"
        return f"/v3/company/{self.realm_id}{clean_path}"

    def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """
        Validate a QuickBooks API response and translate errors.
        """
        if 200 <= response.status_code < 300:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                logger.error("QuickBooks response did not contain valid JSON")
                raise QboError("Qbo response did not contain valid JSON")

        self._raise_for_status(response)
        return {}

    def _raise_for_status(self, response: httpx.Response) -> None:
        """
        Raise an application-specific exception for an HTTP error response.
        """
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        message, code, detail = self._extract_error_details(payload, response.text)
        status = response.status_code

        if status in (400, 422):
            raise QboValidationError(message, code=code, detail=detail)
        if status == 401:
            raise QboAuthError(message, code=code, detail=detail)
        if status == 404:
            raise QboNotFoundError(message, code=code, detail=detail)
        if status == 409:
            raise QboConflictError(message, code=code, detail=detail)
        if status == 429:
            raise QboRateLimitError(message, code=code, detail=detail)

        raise QboError(message, code=code, detail=detail)

    @staticmethod
    def _extract_error_details(payload: Dict[str, Any], fallback_text: str) -> tuple[str, Optional[str], Optional[str]]:
        """
        Extract the most relevant error messaging from a QuickBooks error response.
        """
        fault = payload.get("Fault", {})
        errors = fault.get("Error")

        if isinstance(errors, list) and errors:
            error = errors[0]
            message = error.get("Message") or error.get("Detail") or fallback_text
            code = error.get("code")
            detail = error.get("Detail")
            return message or fallback_text or "QuickBooks request failed", code, detail

        message = fault.get("type") if isinstance(fault, dict) else None
        return message or fallback_text or "QuickBooks request failed", None, None
