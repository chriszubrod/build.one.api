# Python Standard Library Imports
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, NoReturn, Optional, Tuple, Union

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.base.correlation import ensure_correlation_id, get_idempotency_key
from integrations.intuit.qbo.base.errors import (
    AuthFailureKind,
    QboAuthError,
    QboAuthTransientError,
    QboConflictError,
    QboDuplicateError,
    QboMalformedResponseError,
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
from integrations.intuit.qbo.base.budget import QboApiBudget, get_qbo_api_budget
from integrations.intuit.qbo.base.idempotency import resolve_idempotency_key
from integrations.intuit.qbo.base.retry import RetryPolicy, execute_with_retry
from shared.env_flags import env_flag_enabled


logger = logging.getLogger(__name__)


def _format_datetime_for_qbo_query(datetime_input, *, logger: Optional[logging.Logger] = None) -> Optional[str]:
    """
    Format a datetime for a QBO query WHERE clause (ISO 8601 with +HH:MM offset).

    `logger` lets each of the 9 entity clients that call this log their fallback
    warning under their OWN module logger (matching pre-extraction behavior)
    instead of this shared module's — pass the caller's `logger` explicitly.
    """
    log = logger if logger is not None else logging.getLogger(__name__)
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
        log.warning(
            f"Failed to format datetime '{datetime_str}' for QBO query: {error}. Using as-is."
        )
        return datetime_str


DEFAULT_PROD_BASE_URL = "https://quickbooks.api.intuit.com/v3/company"
DEFAULT_SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com/v3/company"
DEFAULT_USER_AGENT = "buildone-qbo-client/1.0"

# Tiered timeouts. Per-call `timeout_tier` selects A/B/C.
_TIMEOUT_TIERS: Dict[str, httpx.Timeout] = {
    "A": httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    "B": httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0),
    "C": httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0),
}

# Multipart parts: (field_name, (filename, payload, content_type)).
_MultipartParts = List[Tuple[str, Tuple[Optional[str], Union[str, bytes], Optional[str]]]]


def writes_allowed() -> bool:
    """
    Default-deny local-dev safety gate.

    Returns True only when `ALLOW_QBO_WRITES` is explicitly set to `"true"`
    (case-insensitive). Any other value — including unset — returns False.
    Production App Service sets this flag in Application Settings; local
    dev environments are refused by default so a fresh checkout cannot
    accidentally push to real QBO.
    """
    return env_flag_enabled("ALLOW_QBO_WRITES")


def _recode_writes_allowed() -> bool:
    """
    Default-deny feature gate for the expense-coding cockpit's QBO recode
    (U-005 Phase F). Mirrors `writes_allowed()` and is AND-ed with it.

    Returns True only when `ALLOW_EXPENSE_RECODE_WRITES` is explicitly set to
    `"true"` (case-insensitive). Any other value — including unset or a
    malformed string — returns False, so the cockpit ships writes-off and
    go-live is a deliberate, reversible flip of this one flag.
    """
    return env_flag_enabled("ALLOW_EXPENSE_RECODE_WRITES")


def recode_write_gate_reason() -> Optional[str]:
    """
    Public read of the expense-recode write gates (for confirm + status
    endpoints — U-058): why the recode write path is disabled, or None when
    enabled. Checks the global ALLOW_QBO_WRITES gate first, then the
    ALLOW_EXPENSE_RECODE_WRITES feature gate.
    """
    if not writes_allowed():
        return "qbo_writes_disabled"
    if not _recode_writes_allowed():
        return "recode_writes_disabled"
    return None


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
        api_budget: Optional[QboApiBudget] = None,
    ):
        self.realm_id = realm_id
        self._api_budget = api_budget or get_qbo_api_budget()
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

    def _resolve_auth(self, force_refresh: bool) -> tuple[Any, AuthFailureKind]:
        """
        Resolve a valid QboAuth plus the classification of any failure.

        Deliberately NO fallback to the unclassified `ensure_valid_token`: the
        only failure kind such a fallback could report is PERMANENT, which is
        precisely the attempt-1 dead-letter this unit exists to remove — a
        silent revert to it would be invisible. Every auth service reaching
        this seam is a `QboAuthService`; anything else should fail loudly.
        """
        return self.auth_service.ensure_valid_token_classified(
            realm_id=self.realm_id,
            force_refresh=force_refresh,
        )

    def _raise_auth_unavailable(
        self,
        *,
        failure_kind: AuthFailureKind,
        reason: str,
        request_method: str,
        request_path: str,
        correlation_id: str,
        http_status: Optional[int] = None,
    ) -> NoReturn:
        kwargs: Dict[str, Any] = {
            "request_method": request_method,
            "request_path": request_path,
            "correlation_id": correlation_id,
        }
        if http_status is not None:
            kwargs["http_status"] = http_status
        detail, error_class = (
            ("transient refresh failure", QboAuthTransientError)
            if failure_kind == AuthFailureKind.TRANSIENT
            else ("permanent — re-authorization required", QboAuthError)
        )
        raise error_class(f"{reason} ({detail})", **kwargs)

    # ------------------------------------------------------------------ #
    # Public verb methods
    # ------------------------------------------------------------------ #

    def get(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._execute(
            method="GET",
            path=path,
            params=params,
            json_body=None,
            files=None,
            idempotency_key=None,
            policy=RetryPolicy.for_reads(timeout_tier=timeout_tier),
            timeout_tier=timeout_tier,
            include_requestid=False,
            operation_name=operation_name,
        )

    def post(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
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
            files=None,
            idempotency_key=idempotency_key,
            policy=RetryPolicy.for_writes(timeout_tier=timeout_tier),
            timeout_tier=timeout_tier,
            include_requestid=True,
            operation_name=operation_name,
        )

    def post_multipart(
        self,
        path: str,
        *,
        files: _MultipartParts,
        params: Optional[Dict[str, Any]] = None,
        timeout_tier: str = "C",
        include_requestid: bool = False,
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST multipart/form-data (e.g. /upload). Defaults to tier C and
        omits requestid — Intuit's /upload endpoint is not documented to
        honour requestid; flipping include_requestid on is booked behind a
        live probe.

        Uses for_uploads_single() (max_attempts=1): retrying a create without an
        idempotency token duplicates Attachables when attempt 1 already committed.
        There is no retry above this layer either — a failed upload is recorded as a
        durable qbo.ReconciliationIssue by the caller (U-234), not retried.
        401-refresh-resend stays in _send_once.
        """
        self._enforce_write_gate("POST", path, operation_name)
        return self._execute(
            method="POST",
            path=path,
            params=params,
            json_body=None,
            files=files,
            idempotency_key=None,
            policy=RetryPolicy.for_uploads_single(),
            timeout_tier=timeout_tier,
            include_requestid=include_requestid,
            operation_name=operation_name,
        )

    def put(
        self,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
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
            files=None,
            idempotency_key=idempotency_key,
            policy=RetryPolicy.for_writes(timeout_tier=timeout_tier),
            timeout_tier=timeout_tier,
            include_requestid=True,
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
        if writes_allowed():
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
        files: Optional[_MultipartParts],
        idempotency_key: Optional[str],
        policy: RetryPolicy,
        timeout_tier: str,
        include_requestid: bool,
        operation_name: Optional[str],
    ) -> Dict[str, Any]:
        correlation_id = ensure_correlation_id()

        # For get/post/put, an invalid timeout_tier already raised inside the
        # RetryPolicy.for_reads/for_writes(timeout_tier=...) call the caller made
        # to build `policy` before calling this method (retry.py's _budget_for_tier
        # validates the same tier set) -- this check is a backstop for those three,
        # and the ONLY validator for post_multipart, whose for_uploads_single()
        # policy doesn't take a tier.
        if timeout_tier not in _TIMEOUT_TIERS:
            raise ValueError(f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')")
        timeout = _TIMEOUT_TIERS[timeout_tier]

        effective_params: Dict[str, Any] = dict(params or {})
        if method in ("POST", "PUT") and include_requestid:
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
                files=files,
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
        params: Dict[str, Any],
        json_body: Optional[Any],
        files: Optional[_MultipartParts],
        timeout: httpx.Timeout,
        correlation_id: str,
        operation_name: str,
    ) -> Dict[str, Any]:
        """
        Single round-trip including 401-refresh-retry-once. Raises a typed
        QboError on failure, returns parsed JSON on success. Wrapped by the
        retry loop in `_execute` for 429/5xx handling.
        """
        auth, failure_kind = self._resolve_auth(force_refresh=False)
        if not auth or not auth.access_token:
            self._raise_auth_unavailable(
                failure_kind=failure_kind,
                reason="No valid QBO auth token available",
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
            response = self._send_http(
                method, url, auth.access_token, params, json_body, files, timeout
            )

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
                refreshed, refresh_failure_kind = self._resolve_auth(force_refresh=True)
                if not refreshed or not refreshed.access_token:
                    logger.error(
                        "qbo.auth.token.refresh.failed",
                        extra={
                            "event_name": "qbo.auth.token.refresh.failed",
                            "correlation_id": correlation_id,
                            "realm_id": self.realm_id,
                            "failure_kind": refresh_failure_kind.value,
                        },
                    )
                    self._raise_auth_unavailable(
                        failure_kind=refresh_failure_kind,
                        reason="Token refresh after 401 did not yield a new token",
                        request_method=method,
                        request_path=request_path,
                        correlation_id=correlation_id,
                        http_status=401,
                    )
                logger.info(
                    "qbo.auth.token.refresh.completed",
                    extra={
                        "event_name": "qbo.auth.token.refresh.completed",
                        "correlation_id": correlation_id,
                        "realm_id": self.realm_id,
                    },
                )
                response = self._send_http(
                    method, url, refreshed.access_token, params, json_body, files, timeout
                )

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
            # U-212: an empty/unparseable 2xx body raises (retryable) rather
            # than returning {} — see base/delete_reconcile.py for why.
            if response.text:
                try:
                    return response.json()
                except Exception:
                    pass
            raise QboMalformedResponseError(
                f"QBO returned HTTP {response.status_code} with an "
                f"{'empty' if not response.text else 'unparseable'} body",
                detail=response.text[:200] or None,
                http_status=response.status_code,
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            )

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
        files: Optional[_MultipartParts],
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        """Pure HTTP send with auth header injection. Isolated so the 401-retry can reuse it."""
        # U-211 meter + breaker: every real HTTP round-trip to the QBO API —
        # including the 401-retry resend and each retry-loop attempt — passes
        # through here. Increments first, then refuses (QboBudgetExceededError)
        # over the block threshold — the monthly CorePlus cap is a hard block
        # at Intuit's side; refusing locally preserves the remaining headroom.
        self._api_budget.record_call_or_raise(self.realm_id, method=method, path=url)
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
        }
        # Guard on files is None (not json_body) so the nine JSON clients stay
        # byte-identical on the wire. Multipart: never set Content-Type — httpx
        # must own the boundary parameter.
        if files is None:
            headers["Content-Type"] = "application/json"
        return self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body if files is None else None,
            files=files,
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
            # QBO fault code 610 = Object Not Found. Intuit signals a GET on a
            # HARD-DELETED transaction as HTTP 400/610, NOT 404 — mapping it
            # here is what lets the reconcile void detectors and the pull-side
            # delete-confirm actually see real deletions (U-212/U-213; the
            # 2026-08-07 audit found deletions landing in generic errors).
            if fault_code == "610":
                raise QboNotFoundError(message, **common)
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
