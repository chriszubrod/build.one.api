# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports


class MsGraphError(Exception):
    """
    Base exception for Microsoft Graph API errors.

    All fields are optional so existing call sites can catch on the base
    class while the shared MsGraphClient populates the richer metadata
    (http_status, request_method, request_path, correlation_id,
    retry_after_seconds) when raising typed subclasses.
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
    ):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.request_method = request_method
        self.request_path = request_path
        self.correlation_id = correlation_id
        self.retry_after_seconds = retry_after_seconds


# ---------------------------------------------------------------------------
# Transient errors — retryable by default.
# ---------------------------------------------------------------------------


class MsTransportError(MsGraphError):
    """
    Raised for transport-level failures: DNS resolution, TLS handshake,
    connection refused, connection reset. Always safe to retry with backoff.
    """

    is_retryable = True


class MsTimeoutError(MsGraphError):
    """
    Raised when a Graph request exceeds its configured connect or read timeout.
    Safe to retry with backoff; `x-ms-client-request-id` idempotency protects
    writes against duplicate effect if the original request actually reached MS.
    """

    is_retryable = True


class MsRateLimitError(MsGraphError):
    """
    Raised when Graph returns HTTP 429. The retry layer should honor the
    `retry_after_seconds` value (captured from the Retry-After header)
    before retrying.
    """

    is_retryable = True


class MsServerError(MsGraphError):
    """
    Raised for generic 5xx responses. Usually safe to retry; persistent
    failures should surface to the caller after the retry budget is exhausted.
    """

    is_retryable = True


class MsServiceUnavailableError(MsServerError):
    """
    Raised specifically for HTTP 503. Graph often pairs this with a Retry-After
    header indicating when to retry.
    """


# ---------------------------------------------------------------------------
# Client errors — NOT retryable by default; retry won't fix the input.
# ---------------------------------------------------------------------------


class MsClientError(MsGraphError):
    """
    Base for 4xx-class errors. Retry will not help; the caller must fix
    the request or surface the problem to a human.
    """

    is_retryable = False


class MsAuthError(MsClientError):
    """
    Raised when authentication with Graph fails (401/403). The shared
    client's 401 handler will attempt a token refresh and retry once
    before surfacing this to the caller.
    """


class MsValidationError(MsClientError):
    """
    Raised when Graph rejects the request body (HTTP 400) with validation
    details in the response error payload.
    """


class MsConflictError(MsClientError):
    """
    Raised for HTTP 409 (e.g., SharePoint filename collisions when the
    conflict behavior is not "replace", Graph edit conflicts).
    """


class MsNotFoundError(MsClientError):
    """
    Raised when the requested Graph resource does not exist (HTTP 404).
    """


class MsWriteRefusedError(MsClientError):
    """
    Raised by the shared client when a write (POST/PUT/PATCH/DELETE) is
    attempted while the `ALLOW_MS_WRITES` environment guard is not set
    to "true".

    This is a local-dev safety mechanism, not a server response — the
    request is refused before it ever leaves the process. Production
    must explicitly set ALLOW_MS_WRITES=true in App Service Application
    Settings; local dev runs with writes refused by default so new
    developers can't accidentally push to production SharePoint / Excel /
    Mail.
    """


# ---------------------------------------------------------------------------
# Catch-all.
# ---------------------------------------------------------------------------


class MsUnexpectedError(MsGraphError):
    """
    Raised for unclassified errors that don't map to any specific case:
    unexpected status codes, malformed responses, etc. Worth flagging for
    investigation rather than retrying blindly.
    """
