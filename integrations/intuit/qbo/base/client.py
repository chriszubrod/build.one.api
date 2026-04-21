# Python Standard Library Imports
import logging
import os
import time
from typing import Any, Dict, Optional, Union

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.base.correlation import ensure_correlation_id, get_idempotency_key
from integrations.intuit.qbo.base.errors import (
    QboAuthError,
    QboConflictError,
    QboDuplicateError,
    QboNotFoundError,
    QboRateLimitError,
    QboServerError,
    QboServiceUnavailableError,
    QboSyncTokenMismatchError,
    QboTimeoutError,
    QboTransportError,
    QboUnexpectedError,
    QboValidationError,
    QboWriteRefusedError,
)
from integrations.intuit.qbo.base.idempotency import resolve_idempotency_key
from integrations.intuit.qbo.base.retry import RetryPolicy, execute_with_retry


logger = logging.getLogger(__name__)


DEFAULT_PROD_BASE_URL = "https://quickbooks.api.intuit.com/v3/company"
DEFAULT_SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company"
DEFAULT_USER_AGENT = "buildone-qbo-client/1.0"


def _writes_allowed() -> bool:
    """
    Default-deny local-dev safety gate.

    Returns True only when `ALLOW_QBO_WRITES` is explicitly set to `"true"`
    (case-insensitive). Any other value — including unset — returns False.
    Production App Service sets this flag in Application Settings; local
    dev environments are refused by default so a fresh checkout cannot
    accidentally push to real QBO.
    """
    return os.getenv("ALLOW_QBO_WRITES", "").strip().lower() == "true"


class QboHttpClient:
    """
    Shared HTTP client for QBO API calls.

    Owns: HTTP mechanics, auth injection (lazy token fetch + 401-refresh-retry),
    retry with backoff+jitter, idempotency key injection for writes, structured
    logging with correlation ID, metrics emission (currently log-based; swaps
    to OpenTelemetry once Application Insights wires up in Phase 2).

    Entity clients should compose this class rather than construct their own
    httpx.Client. See `integrations/intuit/qbo/<entity>/external/client.py`
    for per-entity usage.
    """

    def __init__(
        self,
        realm_id: str,
        *,
        base_url: Optional[str] = None,
        minor_version: Optional[Union[int, str]] = None,
        auth_service: Optional[Any] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.realm_id = realm_id
        self.base_url = (base_url or DEFAULT_PROD_BASE_URL).rstrip("/")
        self.minor_version = str(minor_version) if minor_version is not None else None

        # Lazy import: auth.business.service transitively imports from base.
        # Importing at module load time would couple base/ -> auth/ at load,
        # risking circular deps when other base/ modules grow.
        if auth_service is None:
            from integrations.intuit.qbo.auth.business.service import QboAuthService
            auth_service = QboAuthService()
        self.auth_service = auth_service

        # Chapter 4 timeouts. Connect fast-fails; read handles slow endpoints.
        self._http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
        )
        self._owns_http_client = http_client is None

    def __enter__(self) -> "QboHttpClient":
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
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._execute(
            method="GET",
            path=path,
            params=params,
            json_body=None,
            idempotency_key=None,
            policy=RetryPolicy.for_reads(),
            operation_name=operation_name,
        )

    def post(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("POST", path, operation_name)
        return self._execute(
            method="POST",
            path=path,
            params=params,
            json_body=json,
            idempotency_key=idempotency_key,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def put(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("PUT", path, operation_name)
        return self._execute(
            method="PUT",
            path=path,
            params=params,
            json_body=json,
            idempotency_key=idempotency_key,
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
        Raise QboWriteRefusedError unless ALLOW_QBO_WRITES=true is set.

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
            "qbo.http.request.blocked",
            extra={
                "event_name": "qbo.http.request.blocked",
                "correlation_id": correlation_id,
                "operation_name": operation_name,
                "realm_id": self.realm_id,
                "http_method": method,
                "request_path": path,
                "outcome": "write_refused",
                "reason": "ALLOW_QBO_WRITES_not_true",
            },
        )
        raise QboWriteRefusedError(
            f"QBO write refused: ALLOW_QBO_WRITES is not 'true' for {method} {path}",
            request_method=method,
            request_path=path,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _execute(
        self,
        *,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        idempotency_key: Optional[str],
        policy: RetryPolicy,
        operation_name: Optional[str],
    ) -> Dict[str, Any]:
        correlation_id = ensure_correlation_id()

        effective_params: Dict[str, Any] = dict(params or {})
        if method in ("POST", "PUT"):
            # Fallback order: explicit caller-supplied key → context-var key
            # (set by the outbox worker) → freshly-generated UUID.
            key = idempotency_key or get_idempotency_key()
            effective_params["requestid"] = resolve_idempotency_key(key)
        if self.minor_version and "minorversion" not in effective_params:
            effective_params["minorversion"] = self.minor_version

        url = f"{self.base_url}/{self.realm_id}/{path.lstrip('/')}"
        op_name = operation_name or f"{method} {path}"

        def _do_once() -> Dict[str, Any]:
            return self._send_once(
                method=method,
                url=url,
                request_path=path,
                params=effective_params,
                json_body=json_body,
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
        params: Dict[str, Any],
        json_body: Optional[Any],
        correlation_id: str,
        operation_name: str,
    ) -> Dict[str, Any]:
        """
        Single round-trip including 401-refresh-retry-once. Raises a typed
        QboError on failure, returns parsed JSON on success. Wrapped by the
        retry loop in `_execute` for 429/5xx handling.
        """
        auth = self.auth_service.ensure_valid_token(realm_id=self.realm_id)
        if not auth or not auth.access_token:
            raise QboAuthError(
                "No valid QBO auth token available",
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            )

        start = time.monotonic()
        logger.info(
            "qbo.http.request.started",
            extra={
                "event_name": "qbo.http.request.started",
                "correlation_id": correlation_id,
                "operation_name": operation_name,
                "realm_id": self.realm_id,
                "http_method": method,
                "request_path": request_path,
            },
        )

        try:
            response = self._send_http(method, url, auth.access_token, params, json_body)

            # 401-refresh-retry-once: a single-shot recovery that is intentionally
            # distinct from the retry layer (the retry layer handles 429/5xx).
            if response.status_code == 401:
                logger.info(
                    "qbo.auth.token.refresh.started",
                    extra={
                        "event_name": "qbo.auth.token.refresh.started",
                        "correlation_id": correlation_id,
                        "operation_name": operation_name,
                        "realm_id": self.realm_id,
                        "reason": "401_on_request",
                    },
                )
                refreshed = self.auth_service.ensure_valid_token(
                    realm_id=self.realm_id, force_refresh=True,
                )
                if not refreshed or not refreshed.access_token:
                    logger.error(
                        "qbo.auth.token.refresh.failed",
                        extra={
                            "event_name": "qbo.auth.token.refresh.failed",
                            "correlation_id": correlation_id,
                            "realm_id": self.realm_id,
                        },
                    )
                    raise QboAuthError(
                        "Token refresh after 401 did not yield a new token",
                        http_status=401,
                        request_method=method,
                        request_path=request_path,
                        correlation_id=correlation_id,
                    )
                logger.info(
                    "qbo.auth.token.refresh.completed",
                    extra={
                        "event_name": "qbo.auth.token.refresh.completed",
                        "correlation_id": correlation_id,
                        "realm_id": self.realm_id,
                    },
                )
                response = self._send_http(method, url, refreshed.access_token, params, json_body)

        except httpx.TimeoutException as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "qbo.http.request.failed",
                extra={
                    "event_name": "qbo.http.request.failed",
                    "correlation_id": correlation_id,
                    "operation_name": operation_name,
                    "realm_id": self.realm_id,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "timeout",
                },
            )
            raise QboTimeoutError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "qbo.http.request.failed",
                extra={
                    "event_name": "qbo.http.request.failed",
                    "correlation_id": correlation_id,
                    "operation_name": operation_name,
                    "realm_id": self.realm_id,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "transport",
                },
            )
            raise QboTransportError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        duration_ms = (time.monotonic() - start) * 1000

        if 200 <= response.status_code < 300:
            logger.info(
                "qbo.http.request.completed",
                extra={
                    "event_name": "qbo.http.request.completed",
                    "correlation_id": correlation_id,
                    "operation_name": operation_name,
                    "realm_id": self.realm_id,
                    "http_method": method,
                    "request_path": request_path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                    "outcome": "success",
                    "metric_name": "qbo.http.request.duration",
                    "metric_type": "histogram",
                    "metric_value": duration_ms,
                },
            )
            if not response.text:
                return {}
            try:
                return response.json()
            except Exception:
                return {}

        self._raise_for_status(
            response=response,
            method=method,
            request_path=request_path,
            correlation_id=correlation_id,
            operation_name=operation_name,
            duration_ms=duration_ms,
        )
        # _raise_for_status always raises; this line is unreachable but
        # keeps the static analyzer happy about return type.
        raise QboUnexpectedError("unreachable")

    def _send_http(
        self,
        method: str,
        url: str,
        access_token: str,
        params: Dict[str, Any],
        json_body: Optional[Any],
    ) -> httpx.Response:
        """Pure HTTP send with auth header injection. Isolated so the 401-retry can reuse it."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
        }
        return self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body,
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
        """Map HTTP error response to typed QboError."""
        status = response.status_code

        fault_code: Optional[str] = None
        fault_message: Optional[str] = None
        fault_detail: Optional[str] = None
        try:
            body = response.json()
            fault = body.get("Fault") or body.get("fault") or {}
            errors = fault.get("Error") or fault.get("error") or []
            if isinstance(errors, dict):
                errors = [errors]
            if errors:
                first = errors[0]
                fault_code = first.get("code") or first.get("Code")
                fault_message = first.get("Message") or first.get("message")
                fault_detail = first.get("Detail") or first.get("detail")
        except Exception:
            pass

        message = fault_message or f"QBO API returned HTTP {status}"
        detail = fault_detail or (response.text[:500] if response.text else None)

        retry_after_seconds: Optional[float] = None
        retry_after_header = response.headers.get("Retry-After") or response.headers.get("retry-after")
        if retry_after_header:
            try:
                retry_after_seconds = float(retry_after_header)
            except ValueError:
                # QBO rarely uses the HTTP-date form; if it ever does we fall
                # back to policy-computed backoff rather than parse-date here.
                pass

        common: Dict[str, Any] = {
            "code": fault_code,
            "detail": detail,
            "http_status": status,
            "request_method": method,
            "request_path": request_path,
            "correlation_id": correlation_id,
        }

        logger.warning(
            "qbo.http.request.failed",
            extra={
                "event_name": "qbo.http.request.failed",
                "correlation_id": correlation_id,
                "operation_name": operation_name,
                "realm_id": self.realm_id,
                "http_method": method,
                "request_path": request_path,
                "http_status": status,
                "duration_ms": duration_ms,
                "outcome": "http_error",
                "qbo_fault_code": fault_code,
            },
        )

        if status == 400:
            # QBO fault code 5010 = Stale Object Error (SyncToken mismatch).
            # Surface separately so the outbox worker can recover by pulling
            # fresh state and retrying.
            if fault_code == "5010":
                raise QboSyncTokenMismatchError(message, **common)
            # QBO fault code 6140 = Duplicate Name Exists / Duplicate DocNumber.
            # Surface separately so callers can pursue recovery (lookup+link)
            # rather than treat this as a generic validation failure.
            if fault_code == "6140":
                raise QboDuplicateError(message, **common)
            raise QboValidationError(message, **common)
        if status in (401, 403):
            raise QboAuthError(message, **common)
        if status == 404:
            raise QboNotFoundError(message, **common)
        if status == 409:
            # 409 can also carry SyncToken mismatches depending on endpoint.
            # Detect via fault_code if available; otherwise fall back to
            # generic conflict.
            if fault_code == "5010":
                raise QboSyncTokenMismatchError(message, **common)
            raise QboConflictError(message, **common)
        if status == 429:
            raise QboRateLimitError(message, retry_after_seconds=retry_after_seconds, **common)
        if status == 503:
            raise QboServiceUnavailableError(message, retry_after_seconds=retry_after_seconds, **common)
        if 500 <= status < 600:
            raise QboServerError(message, **common)
        raise QboUnexpectedError(message, **common)
