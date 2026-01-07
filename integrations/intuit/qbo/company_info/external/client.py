# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Any, Dict, Optional

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo,
    QboCompanyInfoResponse,
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


class QboCompanyInfoClient:
    """
    Lightweight client for interacting with QBO CompanyInfo endpoints.
    """

    def __init__(
        self,
        *,
        access_token: str,
        realm_id: str,
        base_url: str = "https://quickbooks.api.intuit.com",
        minor_version: Optional[int] = 65,
        timeout: float = 10.0,
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
                "User-Agent": "build.one-qbo-company-info-client/1.0",
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

    def get_company_info(self, company_id: Optional[str] = None, last_updated_time: Optional[str] = None) -> QboCompanyInfo:
        """
        Retrieve company information from QuickBooks using the query endpoint.
        
        Args:
            company_id: Optional company ID. If not provided, fetches the default company info.
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                CompanyInfo where Metadata.LastUpdatedTime > last_updated_time.
        
        Returns:
            QboCompanyInfo: The company information
        """
        if company_id:
            path = f"/companyinfo/{company_id}"
            data = self._request("GET", path)
            # Handle direct CompanyInfo response format
            return QboCompanyInfoResponse(**data).company_info
        else:
            # Use the query endpoint: /query?query=SELECT * FROM CompanyInfo
            path = "/query"
            if last_updated_time:
                # Format datetime for QBO API query
                formatted_time = _format_datetime_for_qbo_query(last_updated_time)
                query_string = f"SELECT * FROM CompanyInfo WHERE Metadata.LastUpdatedTime > '{formatted_time}'"
                logger.debug(f"Querying CompanyInfo with WHERE clause: Metadata.LastUpdatedTime > '{formatted_time}'")
            else:
                query_string = "SELECT * FROM CompanyInfo"
            data = self._request("GET", path, params={"query": query_string})

            # Handle query response format - extract CompanyInfo from QueryResponse
            if "QueryResponse" in data:
                query_response = data["QueryResponse"]
                company_info_list = query_response.get("CompanyInfo", [])
                if company_info_list:
                    # Return the first CompanyInfo from the query result
                    return QboCompanyInfo(**company_info_list[0])
                else:
                    raise ValueError("No CompanyInfo found in query response")
            else:
                # Fallback to direct response format
                return QboCompanyInfoResponse(**data).company_info

    def update_company_info(self, company_info: QboCompanyInfo) -> QboCompanyInfo:
        """
        Update company information in QuickBooks Online.
        
        Note: QBO CompanyInfo API has limited update capabilities. Only certain fields
        may be updatable. This method attempts to update CompanyInfo but may fail
        if the API doesn't support updates for the requested fields.
        
        Args:
            company_info: QboCompanyInfo object with updated fields
        
        Returns:
            QboCompanyInfo: The updated company information
        
        Raises:
            QboValidationError: If the update is not supported or validation fails
            QboError: For other API errors
        """
        if not company_info.id:
            raise QboValidationError("CompanyInfo ID is required for update")
        
        # Build the update payload
        # Note: QBO API typically requires SyncToken for updates
        payload = {
            "CompanyInfo": company_info.model_dump(exclude_none=True, by_alias=True)
        }
        
        # Use POST method to update CompanyInfo
        # Path format: /companyinfo/{company_id}
        path = f"/companyinfo/{company_info.id}"
        
        try:
            data = self._request("POST", path, json=payload)
            # Handle response format
            if "CompanyInfo" in data:
                return QboCompanyInfo(**data["CompanyInfo"])
            elif "QueryResponse" in data:
                query_response = data["QueryResponse"]
                company_info_list = query_response.get("CompanyInfo", [])
                if company_info_list:
                    return QboCompanyInfo(**company_info_list[0])
            return QboCompanyInfoResponse(**data).company_info
        except QboValidationError as e:
            # Log limitation if update is not supported
            logger.warning(
                f"CompanyInfo update may not be supported or validation failed: {e}. "
                "QBO CompanyInfo API has limited update capabilities."
            )
            raise
        except Exception as e:
            logger.error(f"Error updating CompanyInfo: {e}")
            raise QboError(f"Failed to update CompanyInfo: {str(e)}")

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

