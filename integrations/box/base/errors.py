# Python Standard Library Imports
from typing import Any, Dict, Optional

# Third-party Imports

# Local Imports


class BoxError(Exception):
    """
    Base exception for Box API errors.

    All fields are optional so existing call sites can catch on the base
    class while the shared BoxHttpClient populates the richer metadata
    (http_status, code, request_method, request_path, correlation_id,
    retry_after_seconds, context_info) when raising typed subclasses.

    `code` is the Box error body's `code` field (e.g., `item_name_in_use`,
    `item_locked`). `context_info` is Box's structured detail object —
    notably, 409 conflict responses put the conflicting item under
    `context_info.conflicts`.
    """

    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        detail: Optional[str] = None,
        http_status: Optional[int] = None,
        request_method: Optional[str] = None,
        request_path: Optional[str] = None,
        correlation_id: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
        context_info: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.request_method = request_method
        self.request_path = request_path
        self.correlation_id = correlation_id
        self.retry_after_seconds = retry_after_seconds
        self.context_info = context_info


# ---------------------------------------------------------------------------
# Transient errors — retryable by default.
# ---------------------------------------------------------------------------


class BoxTransportError(BoxError):
    """
    Raised for transport-level failures: DNS resolution, TLS handshake,
    connection refused, connection reset. Always safe to retry with backoff.
    """

    is_retryable = True


class BoxTimeoutError(BoxError):
    """
    Raised when a Box request exceeds its configured connect or read timeout.
    Safe to retry with backoff. Box has no client-request-id dedup header, so
    a write that timed out after reaching Box may have taken effect — the
    Phase 2 outbox handlers are responsible for detecting duplicates (e.g.,
    via the 409 conflict's `context_info.conflicts`).
    """

    is_retryable = True


class BoxRateLimitError(BoxError):
    """
    Raised when Box returns HTTP 429. The retry layer should honor the
    `retry_after_seconds` value (captured from the Retry-After header)
    before retrying.
    """

    is_retryable = True


class BoxServerError(BoxError):
    """
    Raised for generic 5xx responses. Usually safe to retry; persistent
    failures should surface to the caller after the retry budget is exhausted.
    """

    is_retryable = True


class BoxServiceUnavailableError(BoxServerError):
    """
    Raised for HTTP 503, and for the download-not-ready case where Box
    returns 202 + Retry-After on `GET /files/{id}/content`. Both carry a
    Retry-After header indicating when to retry.
    """


# ---------------------------------------------------------------------------
# Client errors — NOT retryable by default; retry won't fix the input.
# ---------------------------------------------------------------------------


class BoxClientError(BoxError):
    """
    Base for 4xx-class errors. Retry will not help; the caller must fix
    the request or surface the problem to a human.
    """

    is_retryable = False


class BoxAuthError(BoxClientError):
    """
    Raised when authentication with Box fails: an HTTP 401 on an API call,
    or a CCG token mint rejected by the token endpoint (400/401 — a config
    or credential problem). The shared client's 401 handler will force a
    fresh mint and retry once before surfacing this to the caller.
    """


class BoxValidationError(BoxClientError):
    """
    Raised when Box rejects the request body (HTTP 400) with validation
    details in the response error payload (e.g., `bad_request`,
    `metadata_after_file_contents` on malformed multipart uploads).
    """


class BoxPermissionError(BoxClientError):
    """
    Raised for a plain HTTP 403 — the authenticated service account lacks
    access to the item or the app's scopes don't cover the operation.
    403s whose error code indicates a file lock raise BoxLockedError instead.
    """


class BoxNotFoundError(BoxClientError):
    """
    Raised when the requested Box resource does not exist (HTTP 404).
    """


class BoxConflictError(BoxClientError):
    """
    Raised for HTTP 409 (e.g., `item_name_in_use` when uploading a file
    whose name already exists in the folder). Always carries `context_info` —
    Box puts the conflicting item under `context_info.conflicts`, which is
    how callers recover the existing file id to upload a new version instead.
    """


class BoxPreconditionError(BoxClientError):
    """
    Raised for HTTP 412 — the `If-Match` etag is stale because the file
    changed since it was read. Non-retryable at the HTTP retry layer:
    blindly resending the same etag cannot succeed. The Phase 2 outbox
    handler recovers by refetching the file (fresh etag) and re-applying
    the write.
    """


class BoxLockedError(BoxClientError):
    """
    Raised for the HTTP 403 variant whose error code indicates a file lock
    (e.g., `item_locked`, `access_denied_item_locked`) — another user or
    process holds an explicit lock on the file. Safe to retry with backoff —
    the lock is transient and clears when the holder releases or it expires.
    """

    is_retryable = True


class BoxWriteRefusedError(BoxClientError):
    """
    Raised by the shared client when a write (POST/PUT/DELETE/upload) is
    attempted while the `ALLOW_BOX_WRITES` environment guard is not set
    to "true".

    This is a local-dev safety mechanism, not a server response — the
    request is refused before it ever leaves the process. Production
    must explicitly set ALLOW_BOX_WRITES=true in App Service Application
    Settings; local dev runs with writes refused by default so new
    developers can't accidentally push to real Box folders. The CCG token
    mint is deliberately NOT gated — it is auth, not content.
    """


# ---------------------------------------------------------------------------
# Catch-all.
# ---------------------------------------------------------------------------


class BoxUnexpectedError(BoxError):
    """
    Raised for unclassified errors that don't map to any specific case:
    unexpected status codes, malformed responses, etc. Worth flagging for
    investigation rather than retrying blindly.
    """
