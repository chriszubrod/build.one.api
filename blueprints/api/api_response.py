from typing import Any, Optional
from datetime import datetime


class ApiResponse:
    """
    This is a template for creating a new API response.
    """
    data: Optional[Any] = None
    message: Optional[str] = None
    status_code: Optional[int] = None
    success: Optional[bool] = None
    timestamp: Optional[datetime] = None

    def __init__(
            self,
            data: Optional[Any] = None,
            message: Optional[str] = None,
            status_code: Optional[int] = None,
            success: Optional[bool] = None,
            timestamp: Optional[datetime] = None
        ):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.success = success
        self.timestamp = timestamp


    def to_dict(self):
        """
        Converts the ApiResponse object to a dictionary.
        """
        return {
            "message": self.message,
            "status_code": self.status_code,
            "data": self.data,
            "success": self.success,
            "timestamp": self.timestamp.isoformat()
        }
