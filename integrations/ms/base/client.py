# Python Standard Library Imports
import email.utils
import logging
import os
import time
from typing import Any, Dict, Optional, Union

# Third-party Imports
import httpx

# Local Imports
from integrations.ms.base.correlation import ensure_correlation_id, get_idempotency_key
from integrations.ms.base.errors import (
    MsAuthError,
    MsClientError,
    MsConflictError,
    MsNotFoundError,
    MsRateLimitError,
    MsServerError,
    MsServiceUnavailableError,
    MsTimeoutError,
    MsTransportError,
    MsUnexpectedError,
    MsValidationError,
    MsWriteRefusedError,
)
from integrations.ms.base.idempotency import resolve_idempotency_key
from integrations.ms.base.logger import get_ms_logger
from integrations.ms.base.retry import RetryPolicy, execute_with_retry


logger = get_ms_logger(__name__)


DEFAULT_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_USER_AGENT = "buildone-ms-client/1.0"


# Tiered timeouts. Per-call `timeout_tier` selects A/B/C. See the MS
# integration plan (Round 0 decisions) for rationale.
_TIMEOUT_TIERS: Dict[str, httpx.Timeout] = {
    "A": httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    "B": httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0),
    "C": httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0),
}


def _writes_allowed() -> bool:
    """
    Default-deny local-dev safety gate.

    Returns True only when `ALLOW_MS_WRITES` is explicitly set to `"true"`
    (case-insensitive). Any other value — including unset — returns False.
    Production App Service sets this flag in Application Settings; local
    dev environments are refused by default so a fresh checkout cannot
    accidentally push to real SharePoint / Excel / Mail.
    """
    return os.getenv("ALLOW_MS_WRITES", "").strip().lower() == "true"


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """
    Parse a Retry-After header value into seconds.

    Graph may return either an integer-seconds form (`"60"`) or an
    HTTP-date form (`"Wed, 21 Oct 2015 07:28:00 GMT"`). We handle both;
    malformed values fall through to policy-computed backoff.
    """
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


class MsGraphClient:
    """
    Shared HTTP client for Microsoft Graph API calls.

    Owns: HTTP mechanics, auth injection (lazy token fetch + 401-refresh-retry),
    retry with backoff+jitter + Retry-After honoring, idempotency key injection
    for writes via `x-ms-client-request-id`, structured logging with correlation
    ID, tiered timeouts (A=fast / B=workbook / C=upload).

    Entity clients should compose this class rather than construct their own
    httpx.Client. See `integrations/ms/<entity>/external/client.py` for
    per-entity usage.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        auth_service: Optional[Any] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

        # Lazy import: auth.business.service transitively imports from base.
        # Importing at module load time would couple base/ -> auth/ at load,
        # risking circular deps when other base/ modules grow.
        if auth_service is None:
            from integrations.ms.auth.business.service import MsAuthService
            auth_service = MsAuthService()
        self.auth_service = auth_service

        # A pool-level timeout is set on construction; per-call tier overrides
        # the read/write/connect components via the `timeout_tier` argument.
        self._http_client = http_client or httpx.Client(timeout=_TIMEOUT_TIERS["A"])
        self._owns_http_client = http_client is None

    def __enter__(self) -> "MsGraphClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    # ------------------------------------------------------------------ #
    # Public verb methods
    # ------------------------------------------------------------------ #

    def get(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._execute(
            method="GET",
            path=path,
            params=params,
            json_body=None,
            content=None,
            content_type=None,
            extra_headers=extra_headers,
            idempotency_key=None,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_reads(),
            operation_name=operation_name,
        )

    def post(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("POST", path, operation_name)
        return self._execute(
            method="POST",
            path=path,
            params=params,
            json_body=json,
            content=None,
            content_type=None,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def patch(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("PATCH", path, operation_name)
        return self._execute(
            method="PATCH",
            path=path,
            params=params,
            json_body=json,
            content=None,
            content_type=None,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def put(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("PUT", path, operation_name)
        return self._execute(
            method="PUT",
            path=path,
            params=params,
            json_body=json,
            content=None,
            content_type=None,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def delete(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("DELETE", path, operation_name)
        return self._execute(
            method="DELETE",
            path=path,
            params=params,
            json_body=None,
            content=None,
            content_type=None,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def get_bytes(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_tier: str = "C",
        operation_name: Optional[str] = None,
    ) -> bytes:
        """
        GET a Graph endpoint and return the raw response body as bytes.
        Used for binary downloads (drive item content, mail attachments).
        Defaults to tier C because downloads may be large.
        """
        return self._execute_raw(
            method="GET",
            path=path,
            params=params,
            extra_headers=extra_headers,
            timeout_tier=timeout_tier,
            operation_name=operation_name,
        )

    def upload(
        self,
        path: str,
        *,
        content: bytes,
        content_type: str = "application/octet-stream",
        method: str = "PUT",
        extra_headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout_tier: str = "C",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Raw-bytes upload (simple PUT for small files, chunk PUT for upload
        sessions). Bypasses JSON body handling; caller supplies raw bytes
        and sets Content-Type / Content-Range in `extra_headers`.

        Defaults to tier C (120s read) which covers slow-uplink uploads of
        a full 5MB upload-session chunk.
        """
        if method.upper() not in ("PUT", "POST"):
            raise ValueError(f"upload() supports PUT or POST, not {method}")
        self._enforce_write_gate(method.upper(), path, operation_name)
        return self._execute(
            method=method.upper(),
            path=path,
            params=params,
            json_body=None,
            content=content,
            content_type=content_type,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def _enforce_write_gate(
        self,
        method: str,
        path: str,
        operation_name: Optional[str],
    ) -> None:
        """
        Raise MsWriteRefusedError unless ALLOW_MS_WRITES=true is set.

        This runs before any network activity — the request is fully
        aborted on the local side. A structured log event is emitted
        so operators can see what was refused (useful both in prod if
        the flag is accidentally unset and in local dev to diagnose
        "why is my write failing").
        """
        if _writes_allowed():
            return

        correlation_id = ensure_correlation_id()
        logger.warning(
            "ms.http.request.blocked",
            extra={
                "event_name": "ms.http.request.blocked",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": path,
                "outcome": "write_refused",
                "reason": "ALLOW_MS_WRITES_not_true",
            },
        )
        raise MsWriteRefusedError(
            f"MS write refused: ALLOW_MS_WRITES is not 'true' for {method} {path}",
            request_method=method,
            request_path=path,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _execute_raw(
        self,
        *,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]],
        extra_headers: Optional[Dict[str, str]],
        timeout_tier: str,
        operation_name: Optional[str],
    ) -> bytes:
        """
        Retry-wrapped execution that returns raw response bytes instead of
        parsed JSON. Used by `get_bytes` for binary downloads.
        """
        correlation_id = ensure_correlation_id()

        if timeout_tier not in _TIMEOUT_TIERS:
            raise ValueError(f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')")
        timeout = _TIMEOUT_TIERS[timeout_tier]

        url = f"{self.base_url}/{path.lstrip('/')}"
        op_name = operation_name or f"{method} {path}"

        def _do_once() -> bytes:
            return self._send_once_raw(
                method=method,
                url=url,
                request_path=path,
                params=params,
                extra_headers=extra_headers,
                timeout=timeout,
                correlation_id=correlation_id,
                operation_name=op_name,
            )

        return execute_with_retry(
            _do_once,
            RetryPolicy.for_reads(),
            log=logger,
            operation_name=op_name,
            correlation_id=correlation_id,
        )

    def _send_once_raw(
        self,
        *,
        method: str,
        url: str,
        request_path: str,
        params: Optional[Dict[str, Any]],
        extra_headers: Optional[Dict[str, str]],
        timeout: httpx.Timeout,
        correlation_id: str,
        operation_name: str,
    ) -> bytes:
        """Single round-trip for raw-bytes GET, including 401-refresh-retry-once."""
        auth = self.auth_service.ensure_valid_token()
        if not auth or not auth.access_token:
            raise MsAuthError(
                "No valid MS auth token available",
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            )

        start = time.monotonic()
        logger.info(
            "ms.http.request.started",
            extra={
                "event_name": "ms.http.request.started",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
            },
        )

        try:
            response = self._send_http(
                method=method,
                url=url,
                access_token=auth.access_token,
                params=params,
                json_body=None,
                content=None,
                content_type=None,
                extra_headers=extra_headers,
                client_request_id=None,
                timeout=timeout,
            )

            if response.status_code == 401:
                refreshed = self.auth_service.ensure_valid_token(force_refresh=True)
                if not refreshed or not refreshed.access_token:
                    raise MsAuthError(
                        "Token refresh after 401 did not yield a new token",
                        http_status=401,
                        request_method=method,
                        request_path=request_path,
                        correlation_id=correlation_id,
                    )
                response = self._send_http(
                    method=method,
                    url=url,
                    access_token=refreshed.access_token,
                    params=params,
                    json_body=None,
                    content=None,
                    content_type=None,
                    extra_headers=extra_headers,
                    client_request_id=None,
                    timeout=timeout,
                )
        except httpx.TimeoutException as error:
            raise MsTimeoutError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            raise MsTransportError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        duration_ms = (time.monotonic() - start) * 1000

        if 200 <= response.status_code < 300:
            logger.info(
                "ms.http.request.completed",
                extra={
                    "event_name": "ms.http.request.completed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                    "outcome": "success",
                },
            )
            return response.content

        self._raise_for_status(
            response=response,
            method=method,
            request_path=request_path,
            correlation_id=correlation_id,
            operation_name=operation_name,
            duration_ms=duration_ms,
        )
        raise MsUnexpectedError("unreachable")

    def _execute(
        self,
        *,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        content: Optional[bytes],
        content_type: Optional[str],
        idempotency_key: Optional[str],
        timeout_tier: str,
        policy: RetryPolicy,
        operation_name: Optional[str],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        correlation_id = ensure_correlation_id()

        if timeout_tier not in _TIMEOUT_TIERS:
            raise ValueError(f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')")
        timeout = _TIMEOUT_TIERS[timeout_tier]

        # Resolve the idempotency key for writes. Fallback order:
        # explicit caller-supplied → context-var (set by outbox worker) → fresh UUID.
        client_request_id: Optional[str] = None
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            key = idempotency_key or get_idempotency_key()
            client_request_id = resolve_idempotency_key(key)

        url = f"{self.base_url}/{path.lstrip('/')}"
        op_name = operation_name or f"{method} {path}"

        def _do_once() -> Dict[str, Any]:
            return self._send_once(
                method=method,
                url=url,
                request_path=path,
                params=params,
                json_body=json_body,
                content=content,
                content_type=content_type,
                extra_headers=extra_headers,
                client_request_id=client_request_id,
                timeout=timeout,
                correlation_id=correlation_id,
                operation_name=op_name,
            )

        return execute_with_retry(
            _do_once,
            policy,
            log=logger,
            operation_name=op_name,
            correlation_id=correlation_id,
        )

    def _send_once(
        self,
        *,
        method: str,
        url: str,
        request_path: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        content: Optional[bytes],
        content_type: Optional[str],
        extra_headers: Optional[Dict[str, str]],
        client_request_id: Optional[str],
        timeout: httpx.Timeout,
        correlation_id: str,
        operation_name: str,
    ) -> Dict[str, Any]:
        """
        Single round-trip including 401-refresh-retry-once. Raises a typed
        MsGraphError on failure, returns parsed JSON on success. Wrapped
        by the retry loop in `_execute` for 429/5xx handling.
        """
        auth = self.auth_service.ensure_valid_token()
        if not auth or not auth.access_token:
            raise MsAuthError(
                "No valid MS auth token available",
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            )

        start = time.monotonic()
        logger.info(
            "ms.http.request.started",
            extra={
                "event_name": "ms.http.request.started",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
            },
        )

        try:
            response = self._send_http(
                method=method,
                url=url,
                access_token=auth.access_token,
                params=params,
                json_body=json_body,
                content=content,
                content_type=content_type,
                extra_headers=extra_headers,
                client_request_id=client_request_id,
                timeout=timeout,
            )

            # 401-refresh-retry-once: a single-shot recovery that is intentionally
            # distinct from the retry layer (the retry layer handles 429/5xx).
            if response.status_code == 401:
                logger.info(
                    "ms.auth.token.refresh.started",
                    extra={
                        "event_name": "ms.auth.token.refresh.started",
                        "operation_name": operation_name,
                        "reason": "401_on_request",
                    },
                )
                refreshed = self.auth_service.ensure_valid_token(force_refresh=True)
                if not refreshed or not refreshed.access_token:
                    logger.error(
                        "ms.auth.token.refresh.failed",
                        extra={
                            "event_name": "ms.auth.token.refresh.failed",
                        },
                    )
                    raise MsAuthError(
                        "Token refresh after 401 did not yield a new token",
                        http_status=401,
                        request_method=method,
                        request_path=request_path,
                        correlation_id=correlation_id,
                    )
                logger.info(
                    "ms.auth.token.refresh.completed",
                    extra={
                        "event_name": "ms.auth.token.refresh.completed",
                    },
                )
                response = self._send_http(
                    method=method,
                    url=url,
                    access_token=refreshed.access_token,
                    params=params,
                    json_body=json_body,
                    content=content,
                    content_type=content_type,
                    extra_headers=extra_headers,
                    client_request_id=client_request_id,
                    timeout=timeout,
                )

        except httpx.TimeoutException as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "ms.http.request.failed",
                extra={
                    "event_name": "ms.http.request.failed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "timeout",
                },
            )
            raise MsTimeoutError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "ms.http.request.failed",
                extra={
                    "event_name": "ms.http.request.failed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "transport",
                },
            )
            raise MsTransportError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        duration_ms = (time.monotonic() - start) * 1000

        if 200 <= response.status_code < 300:
            logger.info(
                "ms.http.request.completed",
                extra={
                    "event_name": "ms.http.request.completed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                    "outcome": "success",
                    "metric_name": "ms.http.request.duration",
                    "metric_type": "histogram",
                    "metric_value": duration_ms,
                },
            )
            # 202 Accepted (e.g., sendMail) returns empty body; also DELETE often 204.
            if response.status_code in (202, 204) or not response.text:
                return {}
            try:
                return response.json()
            except Exception:
                # Uploads and binary endpoints may return non-JSON on success;
                # callers that need raw bytes should use a different method.
                return {}

        self._raise_for_status(
            response=response,
            method=method,
            request_path=request_path,
            correlation_id=correlation_id,
            operation_name=operation_name,
            duration_ms=duration_ms,
        )
        raise MsUnexpectedError("unreachable")

    def _send_http(
        self,
        *,
        method: str,
        url: str,
        access_token: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        content: Optional[bytes],
        content_type: Optional[str],
        extra_headers: Optional[Dict[str, str]],
        client_request_id: Optional[str],
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        """Pure HTTP send with auth + idempotency header injection."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if content is not None and content_type:
            headers["Content-Type"] = content_type
        if client_request_id:
            headers["x-ms-client-request-id"] = client_request_id
        if extra_headers:
            headers.update(extra_headers)

        return self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body if content is None else None,
            content=content,
            timeout=timeout,
        )

    def _raise_for_status(
        self,
        *,
        response: httpx.Response,
        method: str,
        request_path: str,
        correlation_id: str,
        operation_name: str,
        duration_ms: float,
    ) -> None:
        """Map HTTP error response to typed MsGraphError."""
        status = response.status_code

        error_code: Optional[str] = None
        error_message: Optional[str] = None
        error_detail: Optional[str] = None
        try:
            body = response.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                error_code = err.get("code")
                error_message = err.get("message")
                inner = err.get("innerError") or err.get("innererror")
                if isinstance(inner, dict):
                    error_detail = inner.get("message") or str(inner)
        except Exception:
            pass

        message = error_message or f"MS Graph API returned HTTP {status}"
        detail = error_detail or (response.text[:500] if response.text else None)

        retry_after_seconds = _parse_retry_after(
            response.headers.get("Retry-After") or response.headers.get("retry-after")
        )

        common: Dict[str, Any] = {
            "code": error_code,
            "detail": detail,
            "http_status": status,
            "request_method": method,
            "request_path": request_path,
        }

        logger.warning(
            "ms.http.request.failed",
            extra={
                "event_name": "ms.http.request.failed",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
                "http_status": status,
                "duration_ms": duration_ms,
                "outcome": "http_error",
                "ms_error_code": error_code,
            },
        )

        if status == 400:
            raise MsValidationError(message, **common)
        if status in (401, 403):
            raise MsAuthError(message, **common)
        if status == 404:
            raise MsNotFoundError(message, **common)
        if status == 409:
            raise MsConflictError(message, **common)
        if status == 429:
            raise MsRateLimitError(message, retry_after_seconds=retry_after_seconds, **common)
        if status == 503:
            raise MsServiceUnavailableError(message, retry_after_seconds=retry_after_seconds, **common)
        if 400 <= status < 500:
            raise MsClientError(message, **common)
        if 500 <= status < 600:
            raise MsServerError(message, **common)
        raise MsUnexpectedError(message, **common)
