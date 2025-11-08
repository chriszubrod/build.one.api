# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports


class QboError(Exception):
    """
    Base exception for QuickBooks API errors.
    """

    def __init__(self, message: str, *, code: Optional[str] = None, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


class QboAuthError(QboError):
    """
    Raised when authentication with the QuickBooks API fails.
    """


class QboValidationError(QboError):
    """
    Raised when QuickBooks reports validation failures.
    """


class QboRateLimitError(QboError):
    """
    Raised when QuickBooks rate limits the request.
    """


class QboConflictError(QboError):
    """
    Raised when a conflict occurs, such as outdated sync tokens.
    """


class QboNotFoundError(QboError):
    """
    Raised when a requested QuickBooks resource cannot be found.
    """
