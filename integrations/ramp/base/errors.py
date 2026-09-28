# Python Standard Library Imports
from typing import Any, Dict, Optional


class RampError(Exception):
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


class RampTransportError(RampError):
    is_retryable = True


class RampTimeoutError(RampError):
    is_retryable = True


class RampRateLimitError(RampError):
    is_retryable = True


class RampServerError(RampError):
    is_retryable = True


class RampServiceUnavailableError(RampServerError):
    pass


class RampClientError(RampError):
    is_retryable = False


class RampAuthError(RampClientError):
    pass


class RampValidationError(RampClientError):
    pass


class RampPermissionError(RampClientError):
    pass


class RampNotFoundError(RampClientError):
    pass


class RampUnexpectedError(RampError):
    pass


class RampUntrustedRedirectError(RampClientError):
    """Absolute URL in a pagination link does not match the configured API base."""
