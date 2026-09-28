# Python Standard Library Imports
import email.utils
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Third-party Imports
import httpx

# Local Imports
from integrations.ramp.base.correlation import ensure_correlation_id
from integrations.ramp.base.errors import (
    RampAuthError,
    RampClientError,
    RampNotFoundError,
    RampPermissionError,
    RampRateLimitError,
    RampServerError,
    RampTimeoutError,
    RampTransportError,
    RampUnexpectedError,
    RampUntrustedRedirectError,
    RampValidationError,
)
from integrations.ramp.base.logger import get_ramp_logger
from integrations.ramp.base.retry import RetryPolicy, execute_with_retry


logger = get_ramp_logger(__name__)

DEFAULT_USER_AGENT = "buildone-ramp-client/1.0"

# Ramp returns 504 when a request exceeds 60s.
_TIMEOUT_TIERS: Dict[str, httpx.Timeout] = {
    "A": httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    "B": httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0),
}


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    if not header_value:
        return None
    stripped = header_value.strip()
    try:
        return float(stripped)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
        if parsed is not None:
            delta = parsed.timestamp() - time.time()
            return max(0.0, delta)
    except (TypeError, ValueError):
        pass
    return None


class RampHttpClient:
    """Read-only HTTP client for Ramp developer API (GET only)."""

    def __init__(
        self,
        *,
        api_base: str,
        auth_service: Any,
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.auth_service = auth_service
        self._http_client = http_client or httpx.Client(timeout=_TIMEOUT_TIERS["A"])
        self._owns_http_client = http_client is None

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def get(
        self,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> Dict[str, Any]:
        policy = retry_policy or RetryPolicy.for_reads()
        correlation_id = ensure_correlation_id()
        url = self._resolve_url(path_or_url)
        request_path = path_or_url if path_or_url.startswith("/") else urlparse(path_or_url).path
        op_name = operation_name or f"GET {request_path}"

        def _do_once() -> Dict[str, Any]:
            return self._send_once(
                url=url,
                params=params,
                timeout=_TIMEOUT_TIERS[timeout_tier],
                correlation_id=correlation_id,
                request_path=request_path,
                operation_name=op_name,
            )

        return execute_with_retry(
            _do_once,
            policy,
            log=logger,
            operation_name=op_name,
            correlation_id=correlation_id,
        )

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            parsed = urlparse(path_or_url)
            base_parsed = urlparse(self.api_base)
            if parsed.scheme.lower() != base_parsed.scheme.lower():
                raise RampUntrustedRedirectError(
                    "Ramp pagination link scheme does not match configured api_base",
                    request_path=parsed.path or path_or_url,
                )
            if parsed.netloc.lower() != base_parsed.netloc.lower():
                raise RampUntrustedRedirectError(
                    "Ramp pagination link host does not match configured api_base",
                    request_path=parsed.path or path_or_url,
                )
            return path_or_url
        return f"{self.api_base}/{path_or_url.lstrip('/')}"

    def _send_once(
        self,
        *,
        url: str,
        params: Optional[Dict[str, Any]],
        timeout: httpx.Timeout,
        correlation_id: str,
        request_path: str,
        operation_name: str,
    ) -> Dict[str, Any]:
        access_token = self.auth_service.ensure_valid_token()

        try:
            response = self._http_client.get(
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": DEFAULT_USER_AGENT,
                },
                timeout=timeout,
            )
        except httpx.TimeoutException as error:
            raise RampTimeoutError(
                str(error),
                request_method="GET",
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            raise RampTransportError(
                str(error),
                request_method="GET",
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        if 200 <= response.status_code < 300:
            if not response.text:
                return {}
            try:
                body = response.json()
            except json.JSONDecodeError as error:
                raise RampUnexpectedError(
                    "Ramp returned non-JSON success body",
                    http_status=response.status_code,
                    request_method="GET",
                    request_path=request_path,
                    correlation_id=correlation_id,
                ) from error
            if not isinstance(body, dict):
                raise RampUnexpectedError(
                    "Ramp success body was not a JSON object",
                    http_status=response.status_code,
                    request_method="GET",
                    request_path=request_path,
                    correlation_id=correlation_id,
                )
            return body

        self._raise_for_status(
            response=response,
            method="GET",
            request_path=request_path,
            correlation_id=correlation_id,
        )
        raise RampUnexpectedError("unreachable")

    def _raise_for_status(
        self,
        *,
        response: httpx.Response,
        method: str,
        request_path: str,
        correlation_id: str,
    ) -> None:
        status = response.status_code
        message = f"Ramp API returned HTTP {status}"
        detail = response.text[:500] if response.text else None
        retry_after_seconds = _parse_retry_after(
            response.headers.get("Retry-After") or response.headers.get("retry-after")
        )
        common = {
            "detail": detail,
            "http_status": status,
            "request_method": method,
            "request_path": request_path,
            "correlation_id": correlation_id,
        }

        if status == 400:
            raise RampValidationError(message, **common)
        if status == 401:
            raise RampAuthError(message, **common)
        if status == 403:
            raise RampPermissionError(message, **common)
        if status == 404:
            raise RampNotFoundError(message, **common)
        if status == 429:
            raise RampRateLimitError(message, retry_after_seconds=retry_after_seconds, **common)
        if status == 504:
            raise RampTimeoutError(message, **common)
        if 400 <= status < 500:
            raise RampClientError(message, **common)
        if 500 <= status < 600:
            raise RampServerError(message, retry_after_seconds=retry_after_seconds, **common)
        raise RampUnexpectedError(message, **common)
