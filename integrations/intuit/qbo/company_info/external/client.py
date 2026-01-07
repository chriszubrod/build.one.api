# Python Standard Library Imports
import logging
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

    def get_company_info(self, company_id: Optional[str] = None) -> QboCompanyInfo:
        """
        Retrieve company information from QuickBooks using the query endpoint.
        
        Args:
            company_id: Optional company ID. If not provided, fetches the default company info.
        
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

