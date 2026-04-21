# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports


class QboError(Exception):
    """
    Base exception for QuickBooks Online API errors.

    All fields are optional so that existing call sites constructing
    `QboError(message, code=..., detail=...)` continue to work. New fields
    (http_status, request_method, request_path, correlation_id, retry_after_seconds)
    are populated by the shared QboHttpClient when raising typed errors.
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


class QboTransportError(QboError):
    """
    Raised for transport-level failures: DNS resolution, TLS handshake,
    connection refused, connection reset. Always safe to retry with backoff.
    """

    is_retryable = True


class QboTimeoutError(QboError):
    """
    Raised when a QBO request exceeds its configured connect or read timeout.
    Safe to retry with backoff; idempotency keys protect writes against
    accidental duplicate effect if the original request actually reached QBO.
    """

    is_retryable = True


class QboRateLimitError(QboError):
    """
    Raised when QBO returns HTTP 429. The retry layer should honor the
    `retry_after_seconds` value (captured from the Retry-After header)
    before retrying.
    """

    is_retryable = True


class QboServerError(QboError):
    """
    Raised for generic 5xx responses. Usually safe to retry; persistent
    failures should surface to the caller after the retry budget is exhausted.
    """

    is_retryable = True


class QboServiceUnavailableError(QboServerError):
    """
    Raised specifically for HTTP 503. QBO often pairs this with a Retry-After
    header indicating when to retry.
    """


# ---------------------------------------------------------------------------
# Client errors — NOT retryable by default; retry won't fix the input.
# ---------------------------------------------------------------------------


class QboClientError(QboError):
    """
    Base for 4xx-class errors. Retry will not help; the caller must fix
    the request or surface the problem to a human.
    """

    is_retryable = False


class QboAuthError(QboClientError):
    """
    Raised when authentication with the QBO API fails (401/403). The
    shared client's 401 handler will attempt a token refresh and retry
    once before surfacing this to the caller.
    """


class QboValidationError(QboClientError):
    """
    Raised when QBO rejects the request body (HTTP 400) with validation
    details in the response fault codes.
    """


class QboConflictError(QboClientError):
    """
    Raised for HTTP 409 and SyncToken-mismatch conditions. Typically
    indicates the caller's view of the record is stale; the conflict
    handler (Phase 4) decides whether to merge or flag.
    """


class QboSyncTokenMismatchError(QboConflictError):
    """
    Raised specifically for QBO's Stale Object / SyncToken mismatch
    rejection (fault code 5010). Indicates our cached SyncToken is older
    than QBO's current value — someone else updated the record between
    our read and our push.

    Unlike the generic QboConflictError, this is `is_retryable=True` —
    the outbox worker recovers automatically by re-pulling fresh QBO
    state (which refreshes the local SyncToken cache) and retrying the
    push once.
    """

    is_retryable = True


class QboNotFoundError(QboClientError):
    """
    Raised when the requested QBO resource does not exist (HTTP 404).
    """


class QboDuplicateError(QboClientError):
    """
    Raised when QBO rejects a create because a uniqueness constraint
    would be violated (e.g., duplicate DocNumber for a vendor). Caller
    decides whether to recover (look up existing and link) or surface.
    """


class QboWriteRefusedError(QboClientError):
    """
    Raised by the shared client when a write (POST/PUT) is attempted
    while the `ALLOW_QBO_WRITES` environment guard is not set to "true".

    This is a local-dev safety mechanism, not a server response — the
    request is refused before it ever leaves the process. Production
    must explicitly set ALLOW_QBO_WRITES=true in App Service Application
    Settings; local dev runs with writes refused by default so new
    developers can't accidentally push to QBO.
    """


# ---------------------------------------------------------------------------
# Catch-all.
# ---------------------------------------------------------------------------


class QboUnexpectedError(QboError):
    """
    Raised for unclassified errors that don't map to any specific case:
    unexpected status codes, malformed responses, etc. Worth flagging for
    investigation rather than retrying blindly.
    """
