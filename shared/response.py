"""
Module for persistence responses.
"""
from datetime import datetime
from typing import Any, Dict, Optional
import json


class ApiResponse:
    """
    This is a template for creating a new ApiResponse.
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
        Function to initialize the ApiResponse object.
        """
        self.data = data
        self.message = message
        self.status_code = status_code
        self.success = success
        self.timestamp = timestamp


    def to_dict(self):
        """
        Converts the ApiResponse object to a dictionary.
        """
        return {
            "data": self.data,
            "message": self.message,
            "status_code": self.status_code,
            "success": self.success,
            "timestamp": self.timestamp.isoformat()
        }


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



class PersistenceResponse:
    """
    This is a template for creating a new PersistenceResponse.
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
        Function to initialize the PersistenceResponse object.
        """
        self.data = data
        self.message = message
        self.status_code = status_code
        self.success = success
        self.timestamp = timestamp

    def to_dict(self):
        """
        Converts the PersistenceResponse object to a dictionary.
        """
        return {
            "data": self.data,
            "message": self.message,
            "status_code": self.status_code,
            "success": self.success,
            "timestamp": self.timestamp.isoformat()
        }















class SuccessResponse(PersistenceResponse):
    """Response for successful operations"""
    def __init__(self, message: str, data: Any = None, status_code: int = 200):
        super().__init__(message, status_code)
        self.data = data

    def to_dict(self):
        response = super().to_dict()
        if self.data is not None:
            response["data"] = self.data
        return response


class DatabaseError(Exception):
    """Raised when a database operation fails."""
    def __init__(self, message: str, sqlstate: str = None):
        self.sqlstate = sqlstate
        super().__init__(message)

class ValidationError(Exception):
    """Raised when data validation fails."""
    pass

class NotFoundError(Exception):
    """Raised when a requested resource is not found."""
    pass

class IntegrityError(DatabaseError):
    """Raised when database integrity is violated."""
    pass

class TransactionError(DatabaseError):
    """Raised when transaction operations fail."""
    pass

class TimeoutError(DatabaseError):
    """Raised when database operations timeout."""
    pass

class SyntaxError(DatabaseError):
    """Raised when SQL syntax is invalid."""
    pass

def exception_handler(error) -> Dict[str, Any]:
    """
    Maps database errors to appropriate exceptions.
    
    Args:
        error: The database error object

    Raises:
        Various exceptions based on SQL state
    """
    if hasattr(error, 'args') and len(error.args) >= 2:
        sqlstate = error.args[0]
        description = error.args[1]
    else:
        sqlstate = "Unknown"
        description = str(error)

    # Map SQL states to exceptions
    error_mapping = {
        "40002": (TransactionError, "Transaction rollback"),
        "22***": (ValidationError, "Data exception"),
        "23***": (IntegrityError, "Integrity constraint violation"),
        "24***": (TransactionError, "Invalid cursor state"),
        "25***": (TransactionError, "Invalid transaction state"),
        "42***": (SyntaxError, "Syntax error or access rule violation"),
        "HYT00": (TimeoutError, "Timeout expired"),
        "HYT01": (TimeoutError, "Connection timeout expired")
    }

    if sqlstate == "0A000":
        return json.dumps({
            "sqlstate": sqlstate,
            "description": description
        })

    # Get exception class and message
    exception_class, default_message = error_mapping.get(
        sqlstate,
        (DatabaseError, description)
    )

    # Raise appropriate exception
    raise exception_class(
        default_message,
        sqlstate=sqlstate
    )
