"""Custom exceptions for integration-related errors."""

class IntuitAPIError(Exception):
    """Raised when Intuit API request fails."""
    def __init__(self, message: str, status_code: int = None):
        self.status_code = status_code
        super().__init__(message)
