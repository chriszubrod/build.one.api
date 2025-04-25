"""
Module for business responses.
"""

# python standard library imports
from datetime import datetime
from typing import Any, Optional


class BusinessResponse:
    """
    This is a template for creating a new BusinessResponse.
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
        """
        Function to initialize the BusinessResponse object.
        """
        self.data = data
        self.message = message
        self.status_code = status_code
        self.success = success
        self.timestamp = timestamp


    def to_dict(self):
        """
        Converts the BusinessResponse object to a dictionary.
        """
        return {
            "data": self.data,
            "message": self.message,
            "status_code": self.status_code,
            "success": self.success,
            "timestamp": self.timestamp.isoformat()
        }
