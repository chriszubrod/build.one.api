# Python Standard Library Imports
import email.utils
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

# Third-party Imports
import httpx

# Local Imports
from integrations.box.base.correlation import ensure_correlation_id
from integrations.box.base.errors import (
    BoxAuthError,
    BoxClientError,
    BoxConflictError,
    BoxLockedError,
    BoxNotFoundError,
    BoxPermissionError,
    BoxPreconditionError,
    BoxRateLimitError,
    BoxServerError,
    BoxServiceUnavailableError,
    BoxTimeoutError,
    BoxTransportError,
    BoxUnexpectedError,
    BoxValidationError,
    BoxWriteRefusedError,
)
from integrations.box.base.logger import get_box_logger
from integrations.box.base.retry import RetryPolicy, execute_with_retry


logger = get_box_logger(__name__)


BOX_API_BASE = "https://api.box.com/2.0"
BOX_OAUTH_TOKEN_URL = "https://api.box.com/oauth2/token"
BOX_UPLOAD_BASE = "https://upload.box.com/api/2.0"

DEFAULT_USER_AGENT = "buildone-box-client/1.0"


# Tiered timeouts. Per-call `timeout_tier` selects A/B/C. Values mirror the
# MS client for now; Box-specific tuning is a later phase.
_TIMEOUT_TIERS: Dict[str, httpx.Timeout] = {
    "A": httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    "B": httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0),
    "C": httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0),
}

# Multipart parts for uploads: (field_name, (filename, payload, content_type)).
_MultipartParts = List[Tuple[str, Tuple[Optional[str], Union[str, bytes], Optional[str]]]]


def _writes_allowed() -> bool:
    """
    Default-deny local-dev safety gate.

    Returns True only when `ALLOW_BOX_WRITES` is explicitly set to `"true"`
    (case-insensitive). Any other value — including unset — returns False.
    Production App Service sets this flag in Application Settings; local
    dev environments are refused by default so a fresh checkout cannot
    accidentally push to real Box folders.
    """
    return os.getenv("ALLOW_BOX_WRITES", "").strip().lower() == "true"


def writes_allowed() -> bool:
    """Public read of the ALLOW_BOX_WRITES gate (for status endpoints)."""
    return _writes_allowed()


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """
    Parse a Retry-After header value into seconds.

    Box may return either an integer-seconds form (`"60"`) or an
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


class BoxHttpClient:
    """
    Shared HTTP client for Box API calls.

    Owns: HTTP mechanics, auth injection (lazy CCG token mint + 401-remint-retry),
    retry with backoff+jitter + Retry-After honoring, structured logging with
    correlation ID, tiered timeouts (A=fast / B=medium / C=upload-download),
    and the multipart upload shape Box requires.

    Box has no Graph-style client-request-id dedup header — no idempotency
    header is injected; idempotency keys are a Phase 2 outbox concern.

    Entity clients should compose this class rather than construct their own
    httpx.Client.
    """

    def __init__(
        self,
        *,
        api_base: Optional[str] = None,
        upload_base: Optional[str] = None,
        auth_service: Optional[Any] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_base = (api_base or BOX_API_BASE).rstrip("/")
        self.upload_base = (upload_base or BOX_UPLOAD_BASE).rstrip("/")

        # Lazy import: auth.business.service imports from base at module load.
        # Importing at module load time would couple base/ -> auth/ at load,
        # risking circular deps when other base/ modules grow.
        if auth_service is None:
            from integrations.box.auth.business.service import BoxAuthService
            auth_service = BoxAuthService()
        self.auth_service = auth_service

        # A pool-level timeout is set on construction; per-call tier overrides
        # the read/write/connect components via the `timeout_tier` argument.
        self._http_client = http_client or httpx.Client(timeout=_TIMEOUT_TIERS["A"])
        self._owns_http_client = http_client is None

    def __enter__(self) -> "BoxHttpClient":
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
            base_url=self.api_base,
            path=path,
            params=params,
            json_body=None,
            files=None,
            extra_headers=extra_headers,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_reads(),
            operation_name=operation_name,
        )

    def post(
        self,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("POST", path, operation_name)
        return self._execute(
            method="POST",
            base_url=self.api_base,
            path=path,
            params=params,
            json_body=json_body,
            files=None,
            extra_headers=extra_headers,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def put(
        self,
        path: str,
        *,
        json_body: Optional[Any] = None,
        if_match: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("PUT", path, operation_name)
        return self._execute(
            method="PUT",
            base_url=self.api_base,
            path=path,
            params=params,
            json_body=json_body,
            files=None,
            extra_headers=self._merge_if_match(extra_headers, if_match),
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def delete(
        self,
        path: str,
        *,
        if_match: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout_tier: str = "A",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._enforce_write_gate("DELETE", path, operation_name)
        return self._execute(
            method="DELETE",
            base_url=self.api_base,
            path=path,
            params=params,
            json_body=None,
            files=None,
            extra_headers=self._merge_if_match(extra_headers, if_match),
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def download_file(
        self,
        file_id: Union[str, int],
        *,
        timeout_tier: str = "C",
        operation_name: Optional[str] = None,
    ) -> bytes:
        """
        GET `/files/{id}/content` and return the raw file bytes.

        Box responds 302 to a pre-signed dl.boxcloud.com URL; redirects are
        followed (httpx strips Authorization on the cross-origin hop, which
        the CDN expects). A 202 + Retry-After means the file is not yet
        available for download — raised as BoxServiceUnavailableError so the
        retry layer waits the indicated time. Downloads are never write-gated.
        Defaults to tier C because downloads may be large.
        """
        return self._execute_raw(
            method="GET",
            path=f"files/{file_id}/content",
            params=None,
            extra_headers=None,
            timeout_tier=timeout_tier,
            operation_name=operation_name,
        )

    def upload_file(
        self,
        folder_id: Union[str, int],
        filename: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
        timeout_tier: str = "C",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload a new file into a folder via `POST {UPLOAD}/files/content`.

        Returns Box's parsed response (a collection whose `entries[0]` is the
        new file). A name collision in the folder raises BoxConflictError
        with the existing file id under `context_info.conflicts` — callers
        recover by switching to `upload_file_version`.
        """
        path = "files/content"
        self._enforce_write_gate("POST", path, operation_name)
        attributes = {"name": filename, "parent": {"id": str(folder_id)}}
        return self._execute(
            method="POST",
            base_url=self.upload_base,
            path=path,
            params=None,
            json_body=None,
            files=self._build_upload_parts(attributes, filename, content, content_type),
            extra_headers=None,
            timeout_tier=timeout_tier,
            policy=RetryPolicy.for_writes(),
            operation_name=operation_name,
        )

    def upload_file_version(
        self,
        file_id: Union[str, int],
        filename: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
        if_match: Optional[str] = None,
        timeout_tier: str = "C",
        operation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload a new version of an existing file via
        `POST {UPLOAD}/files/{id}/content`.

        `if_match` (the file's current etag) makes the write conditional —
        a stale etag raises BoxPreconditionError (412), which the Phase 2
        outbox handler resolves by refetching the file and re-applying.
        """
        path = f"files/{file_id}/content"
        self._enforce_write_gate("POST", path, operation_name)
        attributes = {"name": filename}
        return self._execute(
            method="POST",
            base_url=self.upload_base,
            path=path,
            params=None,
            json_body=None,
            files=self._build_upload_parts(attributes, filename, content, content_type),
            extra_headers=self._merge_if_match(None, if_match),
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
        Raise BoxWriteRefusedError unless ALLOW_BOX_WRITES=true is set.

        This runs before any network activity — the request is fully
        aborted on the local side. A structured log event is emitted
        so operators can see what was refused (useful both in prod if
        the flag is accidentally unset and in local dev to diagnose
        "why is my write failing"). GETs, downloads, and the CCG token
        mint are never gated — the mint is auth, not content.
        """
        if _writes_allowed():
            return

        correlation_id = ensure_correlation_id()
        logger.warning(
            "box.http.request.blocked",
            extra={
                "event_name": "box.http.request.blocked",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": path,
                "outcome": "write_refused",
                "reason": "ALLOW_BOX_WRITES_not_true",
            },
        )
        raise BoxWriteRefusedError(
            f"Box write refused: ALLOW_BOX_WRITES is not 'true' for {method} {path}",
            request_method=method,
            request_path=path,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _merge_if_match(
        extra_headers: Optional[Dict[str, str]],
        if_match: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """Merge an If-Match etag into the extra headers, if supplied."""
        if not if_match:
            return extra_headers
        merged = dict(extra_headers or {})
        merged["If-Match"] = if_match
        return merged

    @staticmethod
    def _build_upload_parts(
        attributes: Dict[str, Any],
        filename: str,
        content: bytes,
        content_type: str,
    ) -> _MultipartParts:
        """
        Build the multipart parts for a Box upload.

        Ordering is load-bearing: Box hard-400s with
        `metadata_after_file_contents` if the `attributes` JSON part does
        not precede the `file` part. httpx preserves list order, so the
        parts are returned as an ordered list, never a dict.
        """
        return [
            ("attributes", (None, json.dumps(attributes), None)),
            ("file", (filename, content, content_type)),
        ]

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
        parsed JSON. Used by `download_file` for binary downloads.
        """
        correlation_id = ensure_correlation_id()

        if timeout_tier not in _TIMEOUT_TIERS:
            raise ValueError(f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')")
        timeout = _TIMEOUT_TIERS[timeout_tier]

        url = f"{self.api_base}/{path.lstrip('/')}"
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
        """Single round-trip for raw-bytes GET, including 401-remint-retry-once."""
        access_token = self.auth_service.ensure_valid_token()

        start = time.monotonic()
        logger.info(
            "box.http.request.started",
            extra={
                "event_name": "box.http.request.started",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
            },
        )

        try:
            # Raw-bytes downloads (/content) return 302 with a pre-signed
            # dl.boxcloud.com URL. Follow the redirect; httpx strips
            # Authorization on cross-origin redirects so the CDN accepts the
            # request without our bearer token.
            response = self._send_http(
                method=method,
                url=url,
                access_token=access_token,
                params=params,
                json_body=None,
                files=None,
                extra_headers=extra_headers,
                timeout=timeout,
                follow_redirects=True,
            )

            if response.status_code == 401:
                refreshed = self.auth_service.ensure_valid_token(force_refresh=True)
                response = self._send_http(
                    method=method,
                    url=url,
                    access_token=refreshed,
                    params=params,
                    json_body=None,
                    files=None,
                    extra_headers=extra_headers,
                    timeout=timeout,
                    follow_redirects=True,
                )
        except httpx.TimeoutException as error:
            raise BoxTimeoutError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            raise BoxTransportError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        duration_ms = (time.monotonic() - start) * 1000

        # 202 + Retry-After = "not yet ready for download" (e.g., the file was
        # just uploaded). Raised as a retryable 503-class error so the retry
        # layer waits out the indicated interval — must run before the generic
        # 2xx success branch, which 202 would otherwise satisfy.
        if response.status_code == 202:
            retry_after_seconds = _parse_retry_after(
                response.headers.get("Retry-After") or response.headers.get("retry-after")
            )
            logger.info(
                "box.http.request.not_ready",
                extra={
                    "event_name": "box.http.request.not_ready",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "http_status": 202,
                    "duration_ms": duration_ms,
                    "retry_after_seconds": retry_after_seconds,
                },
            )
            raise BoxServiceUnavailableError(
                "Box file content not yet available for download (HTTP 202)",
                http_status=202,
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
                retry_after_seconds=retry_after_seconds,
            )

        if 200 <= response.status_code < 300:
            logger.info(
                "box.http.request.completed",
                extra={
                    "event_name": "box.http.request.completed",
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
        raise BoxUnexpectedError("unreachable")

    def _execute(
        self,
        *,
        method: str,
        base_url: str,
        path: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        files: Optional[_MultipartParts],
        timeout_tier: str,
        policy: RetryPolicy,
        operation_name: Optional[str],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        correlation_id = ensure_correlation_id()

        if timeout_tier not in _TIMEOUT_TIERS:
            raise ValueError(f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')")
        timeout = _TIMEOUT_TIERS[timeout_tier]

        url = f"{base_url}/{path.lstrip('/')}"
        op_name = operation_name or f"{method} {path}"

        def _do_once() -> Dict[str, Any]:
            return self._send_once(
                method=method,
                url=url,
                request_path=path,
                params=params,
                json_body=json_body,
                files=files,
                extra_headers=extra_headers,
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
        files: Optional[_MultipartParts],
        extra_headers: Optional[Dict[str, str]],
        timeout: httpx.Timeout,
        correlation_id: str,
        operation_name: str,
    ) -> Dict[str, Any]:
        """
        Single round-trip including 401-remint-retry-once. Raises a typed
        BoxError on failure, returns parsed JSON on success. Wrapped
        by the retry loop in `_execute` for 429/5xx handling.
        """
        access_token = self.auth_service.ensure_valid_token()

        start = time.monotonic()
        logger.info(
            "box.http.request.started",
            extra={
                "event_name": "box.http.request.started",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
            },
        )

        try:
            response = self._send_http(
                method=method,
                url=url,
                access_token=access_token,
                params=params,
                json_body=json_body,
                files=files,
                extra_headers=extra_headers,
                timeout=timeout,
            )

            # 401-remint-retry-once: a single-shot recovery that is intentionally
            # distinct from the retry layer (the retry layer handles 429/5xx).
            # CCG re-mint is cheap and rotation-free, so a force_refresh here
            # is always safe.
            if response.status_code == 401:
                logger.info(
                    "box.auth.token.remint.started",
                    extra={
                        "event_name": "box.auth.token.remint.started",
                        "operation_name": operation_name,
                        "reason": "401_on_request",
                    },
                )
                refreshed = self.auth_service.ensure_valid_token(force_refresh=True)
                logger.info(
                    "box.auth.token.remint.completed",
                    extra={
                        "event_name": "box.auth.token.remint.completed",
                    },
                )
                response = self._send_http(
                    method=method,
                    url=url,
                    access_token=refreshed,
                    params=params,
                    json_body=json_body,
                    files=files,
                    extra_headers=extra_headers,
                    timeout=timeout,
                )

        except httpx.TimeoutException as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "box.http.request.failed",
                extra={
                    "event_name": "box.http.request.failed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "timeout",
                },
            )
            raise BoxTimeoutError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error
        except httpx.TransportError as error:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "box.http.request.failed",
                extra={
                    "event_name": "box.http.request.failed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "duration_ms": duration_ms,
                    "outcome": "transport",
                },
            )
            raise BoxTransportError(
                str(error),
                request_method=method,
                request_path=request_path,
                correlation_id=correlation_id,
            ) from error

        duration_ms = (time.monotonic() - start) * 1000

        if 200 <= response.status_code < 300:
            logger.info(
                "box.http.request.completed",
                extra={
                    "event_name": "box.http.request.completed",
                    "operation_name": operation_name,
                    "http_method": method,
                    "request_path": request_path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                    "outcome": "success",
                    "metric_name": "box.http.request.duration",
                    "metric_type": "histogram",
                    "metric_value": duration_ms,
                },
            )
            # A 202 carrying Retry-After is Box's async not-ready signal
            # (zip downloads, async operations) — surface it as retryable,
            # mirroring the download path, instead of returning an empty
            # dict indistinguishable from success.
            if response.status_code == 202:
                not_ready_retry_after = _parse_retry_after(
                    response.headers.get("Retry-After")
                    or response.headers.get("retry-after")
                )
                if not_ready_retry_after is not None:
                    raise BoxServiceUnavailableError(
                        "Box returned HTTP 202 with Retry-After (operation not ready)",
                        http_status=202,
                        request_method=method,
                        request_path=request_path,
                        correlation_id=correlation_id,
                        retry_after_seconds=not_ready_retry_after,
                    )
                return {}
            # DELETE returns 204 with empty body.
            if response.status_code == 204 or not response.text:
                return {}
            try:
                return response.json()
            except Exception:
                # Defensive: a 2xx with a non-JSON body shouldn't crash the
                # caller; binary content goes through download_file instead.
                return {}

        self._raise_for_status(
            response=response,
            method=method,
            request_path=request_path,
            correlation_id=correlation_id,
            operation_name=operation_name,
            duration_ms=duration_ms,
        )
        raise BoxUnexpectedError("unreachable")

    def _send_http(
        self,
        *,
        method: str,
        url: str,
        access_token: str,
        params: Optional[Dict[str, Any]],
        json_body: Optional[Any],
        files: Optional[_MultipartParts],
        extra_headers: Optional[Dict[str, str]],
        timeout: httpx.Timeout,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Pure HTTP send with auth header injection."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        # Multipart uploads: never set Content-Type manually — httpx must own
        # the multipart boundary parameter.
        if extra_headers:
            headers.update(extra_headers)

        return self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body if files is None else None,
            files=files,
            timeout=timeout,
            follow_redirects=follow_redirects,
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
        """Map HTTP error response to typed BoxError."""
        status = response.status_code

        # Box error body shape: {"type": "error", "status": ..., "code": "...",
        # "message": "...", "context_info": {...}, "request_id": "..."} —
        # parsed defensively because the body may not be JSON at all (e.g.,
        # proxy-generated 502 pages).
        error_code: Optional[str] = None
        error_message: Optional[str] = None
        context_info: Optional[Dict[str, Any]] = None
        box_request_id: Optional[str] = None
        try:
            body = response.json()
            if isinstance(body, dict):
                error_code = body.get("code")
                error_message = body.get("message")
                raw_context = body.get("context_info")
                if isinstance(raw_context, dict):
                    context_info = raw_context
                box_request_id = body.get("request_id")
        except Exception:
            pass

        message = error_message or f"Box API returned HTTP {status}"
        detail = response.text[:500] if response.text else None

        retry_after_seconds = _parse_retry_after(
            response.headers.get("Retry-After") or response.headers.get("retry-after")
        )

        common: Dict[str, Any] = {
            "code": error_code,
            "detail": detail,
            "http_status": status,
            "request_method": method,
            "request_path": request_path,
            "correlation_id": correlation_id,
            "context_info": context_info,
        }

        logger.warning(
            "box.http.request.failed",
            extra={
                "event_name": "box.http.request.failed",
                "operation_name": operation_name,
                "http_method": method,
                "request_path": request_path,
                "http_status": status,
                "duration_ms": duration_ms,
                "outcome": "http_error",
                "box_error_code": error_code,
                "box_request_id": box_request_id,
            },
        )

        if status == 400:
            raise BoxValidationError(message, **common)
        if status == 401:
            raise BoxAuthError(message, **common)
        if status == 403:
            # 403 splits two ways: a locked file (code like `item_locked` /
            # `access_denied_item_locked`) is transient and retryable; a plain
            # permission denial is not.
            # Match on the structured error code (item_locked /
            # access_denied_item_locked / file_locked). Free-text message
            # sniffing is only a fallback when the body carried no code at
            # all — otherwise a permission message that merely mentions a
            # lock would burn retries on a non-retryable denial.
            code_lower = (error_code or "").lower()
            message_lower = (error_message or "").lower()
            if "lock" in code_lower or (not error_code and "locked" in message_lower):
                raise BoxLockedError(message, **common)
            raise BoxPermissionError(message, **common)
        if status == 404:
            raise BoxNotFoundError(message, **common)
        if status == 409:
            raise BoxConflictError(message, **common)
        if status == 412:
            raise BoxPreconditionError(message, **common)
        if status == 429:
            raise BoxRateLimitError(message, retry_after_seconds=retry_after_seconds, **common)
        if status == 503:
            raise BoxServiceUnavailableError(message, retry_after_seconds=retry_after_seconds, **common)
        if 400 <= status < 500:
            raise BoxClientError(message, **common)
        if 500 <= status < 600:
            raise BoxServerError(message, **common)
        raise BoxUnexpectedError(message, **common)
