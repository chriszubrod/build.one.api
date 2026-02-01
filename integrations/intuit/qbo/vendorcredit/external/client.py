# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.vendorcredit.external.schemas import (
    QboVendorCredit,
    QboVendorCreditCreate,
    QboVendorCreditUpdate,
    QboVendorCreditResponse,
)
from integrations.intuit.qbo.base.errors import (
    QboAuthError,
    QboNotFoundError,
    QboRateLimitError,
    QboValidationError,
    QboError,
)

logger = logging.getLogger(__name__)


class QboVendorCreditClient:
    """
    Client for QBO VendorCredit API operations.
    """
    
    DEFAULT_BASE_URL = "https://quickbooks.api.intuit.com"
    DEFAULT_MINOR_VERSION = "73"
    DEFAULT_TIMEOUT = 30.0
    
    def __init__(
        self,
        access_token: str,
        realm_id: str,
        base_url: str = DEFAULT_BASE_URL,
        minor_version: str = DEFAULT_MINOR_VERSION,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[httpx.Client] = None,
    ):
        """Initialize the QBO VendorCredit client."""
        self.access_token = access_token
        self.realm_id = realm_id
        self.base_url = base_url
        self.minor_version = minor_version
        self.timeout = timeout
        self._session = session
        self._owns_session = session is None
    
    def __enter__(self):
        if self._session is None:
            self._session = httpx.Client(timeout=self.timeout)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._owns_session and self._session:
            self._session.close()
            self._session = None
        return False
    
    def _build_path(self, path: str) -> str:
        """Build the full API path."""
        return f"/v3/company/{self.realm_id}{path}"
    
    def _get_headers(self) -> Dict[str, str]:
        """Get default headers for API requests."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    
    def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Handle API response and translate errors."""
        if response.status_code == 200 or response.status_code == 201:
            return response.json()
        elif response.status_code == 401:
            raise QboAuthError("Authentication failed")
        elif response.status_code == 404:
            raise QboNotFoundError("Resource not found")
        elif response.status_code == 429:
            raise QboRateLimitError("Rate limit exceeded")
        elif response.status_code == 400:
            error_detail = response.text
            raise QboValidationError(f"Validation error: {error_detail}")
        else:
            raise QboError(f"API error {response.status_code}: {response.text}")
    
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make an API request."""
        if self._session is None:
            raise RuntimeError("Client must be used as context manager")
        
        url = f"{self.base_url}{self._build_path(path)}"
        
        # Add minor version to params
        if params is None:
            params = {}
        params["minorversion"] = self.minor_version
        
        response = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            headers=self._get_headers(),
        )
        
        return self._handle_response(response)
    
    @staticmethod
    def _format_datetime_for_qbo_query(dt_input) -> str:
        """Format datetime for QBO query WHERE clause."""
        if isinstance(dt_input, str):
            # Try to parse ISO format
            try:
                dt = datetime.fromisoformat(dt_input.replace('Z', '+00:00'))
            except ValueError:
                # Try simpler format
                dt = datetime.strptime(dt_input[:19], '%Y-%m-%d %H:%M:%S')
        elif isinstance(dt_input, datetime):
            dt = dt_input
        else:
            raise ValueError(f"Unsupported datetime type: {type(dt_input)}")
        
        return dt.strftime('%Y-%m-%dT%H:%M:%S')
    
    def get_vendor_credit(self, vendor_credit_id: str) -> QboVendorCredit:
        """Get a single VendorCredit by ID."""
        data = self._request("GET", f"/vendorcredit/{vendor_credit_id}")
        return QboVendorCreditResponse(**data).vendor_credit
    
    def query_vendor_credits(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 100,
    ) -> List[QboVendorCredit]:
        """Query VendorCredits with optional filters."""
        where_clauses = []
        
        if last_updated_time:
            formatted_time = self._format_datetime_for_qbo_query(last_updated_time)
            where_clauses.append(f"MetaData.LastUpdatedTime > '{formatted_time}'")
        
        if start_date:
            where_clauses.append(f"TxnDate >= '{start_date}'")
        
        if end_date:
            where_clauses.append(f"TxnDate <= '{end_date}'")
        
        query = "SELECT * FROM VendorCredit"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += f" STARTPOSITION {start_position} MAXRESULTS {max_results}"
        
        data = self._request("GET", "/query", params={"query": query})
        
        query_response = data.get("QueryResponse", {})
        vendor_credits_data = query_response.get("VendorCredit", [])
        
        return [QboVendorCredit(**vc) for vc in vendor_credits_data]
    
    def query_all_vendor_credits(
        self,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[QboVendorCredit]:
        """Query all VendorCredits with pagination handling."""
        all_vendor_credits = []
        start_position = 1
        max_results = 100
        
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
    
    def create_vendor_credit(self, vendor_credit: QboVendorCreditCreate) -> QboVendorCredit:
        """Create a new VendorCredit in QBO."""
        data = self._request(
            "POST",
            "/vendorcredit",
            json=vendor_credit.model_dump(by_alias=True, exclude_none=True),
        )
        return QboVendorCreditResponse(**data).vendor_credit
    
    def update_vendor_credit(self, vendor_credit: QboVendorCreditUpdate) -> QboVendorCredit:
        """Update an existing VendorCredit in QBO."""
        data = self._request(
            "POST",
            "/vendorcredit",
            json=vendor_credit.model_dump(by_alias=True, exclude_none=True),
        )
        return QboVendorCreditResponse(**data).vendor_credit
    
    def delete_vendor_credit(self, vendor_credit_id: str, sync_token: str) -> QboVendorCredit:
        """Delete a VendorCredit from QBO (soft delete)."""
        data = self._request(
            "POST",
            "/vendorcredit",
            params={"operation": "delete"},
            json={
                "Id": vendor_credit_id,
                "SyncToken": sync_token,
            },
        )
        return QboVendorCreditResponse(**data).vendor_credit
