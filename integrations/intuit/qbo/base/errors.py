# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from shared.database import is_transient_error

_CHAIN_WALK_MAX = 5

# SQLSTATEs and message substrings that shared.database.is_transient_error does not yet
# cover (e.g. HYT00 / "Query timeout expired"). A wrong "not retryable" verdict at the
# QBO watermark seam permanently skips the record until someone edits it in QBO again.
# Consolidate these vocabularies with shared.database in a follow-up unit (TODO.md).
_EXTRA_TRANSIENT_SQLSTATES = frozenset({"HYT00", "HYT01"})  # query timeout / connection timeout
_EXTRA_TRANSIENT_MESSAGES = (
    "query timeout expired",
    "timeout expired",
    "login timeout expired",
)


def _matches_extra_transient(error: BaseException) -> bool:
    """True when pyodbc timeout failures missed by shared.database.is_transient_error."""
    if hasattr(error, "args") and len(error.args) >= 1 and error.args[0] is not None:
        sqlstate = str(error.args[0]).upper()
        if sqlstate in _EXTRA_TRANSIENT_SQLSTATES:
            return True

    error_str = str(error).lower()
    for msg in _EXTRA_TRANSIENT_MESSAGES:
        if msg in error_str:
            return True

    return False


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


class QboMalformedResponseError(QboServerError):
    """
    Raised when QBO returns a 2xx whose body is empty or unparseable —
    a transient server-side anomaly, retryable via QboServerError (U-212;
    rationale in base/delete_reconcile.py).
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


class QboBudgetExceededError(QboClientError):
    """
    Raised by the shared client's per-call budget breaker (U-211) when the
    month-to-date QBO API call count has crossed the block threshold
    (default 95% of the Intuit CorePlus monthly hard cap).

    Like QboWriteRefusedError, this is a local refusal — the request never
    leaves the process. `is_retryable=False` so the in-process retry loop
    does not spin against a monthly ceiling; the outbox worker special-cases
    this error to park rows until the cap resets on the 1st (it must never
    dead-letter them).
    """

    def __init__(
        self,
        message: str,
        *,
        month_key: Optional[str] = None,
        call_count: Optional[int] = None,
        budget: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.month_key = month_key
        self.call_count = call_count
        self.budget = budget


# ---------------------------------------------------------------------------
# Catch-all.
# ---------------------------------------------------------------------------


class QboUnexpectedError(QboError):
    """
    Raised for unclassified errors that don't map to any specific case:
    unexpected status codes, malformed responses, etc. Worth flagging for
    investigation rather than retrying blindly.
    """


def is_retryable_error(error: BaseException | None) -> bool:
    """
    True when any link in the exception chain is retryable for watermark hold decisions.

    Unions two vocabularies: (1) QBO typed errors expose ``is_retryable`` on
    ``QboError`` subclasses (transport, timeout, rate limit, 5xx, SyncToken mismatch);
    (2) ``shared.database.is_transient_error`` matches pyodbc/SQL Server transient
    failures by SQLSTATE and message substring. Projection loops can raise either —
    e.g. ``QboTimeoutError`` from Attachable fan-out inside the same try as dbo writes,
    or ``raise ValueError(...) from db_err`` when a connector wraps a deadlock.
    """
    if error is None:
        return False

    seen: set[int] = set()
    stack: list[BaseException] = [error]

    while stack and len(seen) < _CHAIN_WALK_MAX:
        current = stack.pop()
        oid = id(current)
        if oid in seen:
            continue
        seen.add(oid)

        if (
            getattr(current, "is_retryable", False)
            or is_transient_error(current)
            or _matches_extra_transient(current)
        ):
            return True

        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)

    return False
