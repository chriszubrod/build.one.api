# Python Standard Library Imports
import asyncio
import functools
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar, Union

logger = logging.getLogger(__name__)

# Error codes for classification
ERROR_RATE_LIMIT = "rate_limit"
ERROR_NETWORK = "network"
ERROR_TIMEOUT = "timeout"
ERROR_AUTH = "auth"
ERROR_VALIDATION = "validation"
ERROR_NOT_FOUND = "not_found"
ERROR_SERVER = "server"
ERROR_UNKNOWN = "unknown"

# Default retryable error codes
DEFAULT_RETRYABLE_CODES = (ERROR_RATE_LIMIT, ERROR_NETWORK, ERROR_TIMEOUT, ERROR_SERVER)


@dataclass
class CapabilityResult:
    """
    Standard result wrapper for capability operations.
    
    Includes retry-friendly metadata for error handling.
    """
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None  # One of ERROR_* constants
    retryable: bool = False
    retry_after_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, data: Any, **metadata) -> "CapabilityResult":
        """Create a successful result."""
        return cls(success=True, data=data, metadata=metadata)
    
    @classmethod
    def fail(
        cls,
        error: str,
        error_code: Optional[str] = None,
        retryable: bool = False,
        retry_after_seconds: Optional[int] = None,
        **metadata,
    ) -> "CapabilityResult":
        """Create a failed result with retry metadata."""
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            metadata=metadata,
        )


F = TypeVar("F", bound=Callable[..., CapabilityResult])


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_codes: Tuple[str, ...] = DEFAULT_RETRYABLE_CODES,
) -> Callable[[F], F]:
    """
    Decorator for retrying operations that return CapabilityResult.
    
    Args:
        max_attempts: Maximum number of attempts (including initial)
        base_delay: Base delay in seconds for exponential backoff
        max_delay: Maximum delay between retries
        retryable_codes: Error codes that are eligible for retry
        
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> CapabilityResult:
            last_result: Optional[CapabilityResult] = None
            
            for attempt in range(max_attempts):
                result = func(*args, **kwargs)
                
                # Success - return immediately
                if result.success:
                    return result
                
                last_result = result
                
                # Check if we should retry
                should_retry = (
                    result.retryable
                    and result.error_code in retryable_codes
                    and attempt < max_attempts - 1
                )
                
                if not should_retry:
                    break
                
                # Calculate delay
                if result.retry_after_seconds:
                    delay = min(result.retry_after_seconds, max_delay)
                else:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                
                logger.warning(
                    f"Retry {attempt + 1}/{max_attempts - 1} for {func.__name__} "
                    f"after {delay:.1f}s (error: {result.error_code})"
                )
                
                time.sleep(delay)
            
            return last_result or CapabilityResult.fail("No result", error_code=ERROR_UNKNOWN)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> CapabilityResult:
            last_result: Optional[CapabilityResult] = None
            
            for attempt in range(max_attempts):
                result = await func(*args, **kwargs)
                
                if result.success:
                    return result
                
                last_result = result
                
                should_retry = (
                    result.retryable
                    and result.error_code in retryable_codes
                    and attempt < max_attempts - 1
                )
                
                if not should_retry:
                    break
                
                if result.retry_after_seconds:
                    delay = min(result.retry_after_seconds, max_delay)
                else:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                
                logger.warning(
                    f"Retry {attempt + 1}/{max_attempts - 1} for {func.__name__} "
                    f"after {delay:.1f}s (error: {result.error_code})"
                )
                
                await asyncio.sleep(delay)
            
            return last_result or CapabilityResult.fail("No result", error_code=ERROR_UNKNOWN)
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore
    
    return decorator


class Capability(ABC):
    """
    Base class for all capabilities.
    
    Capabilities wrap external services and provide a consistent
    interface for agents to use.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this capability (e.g., 'llm', 'email')."""
        pass
    
    def _log_call(self, method: str, **kwargs) -> None:
        """Log a capability method call for debugging."""
        # Filter out large values for logging
        safe_kwargs = {
            k: (v if len(str(v)) < 200 else f"<{len(str(v))} chars>")
            for k, v in kwargs.items()
        }
        logger.debug(f"Capability {self.name}.{method} called with: {safe_kwargs}")
    
    def _log_result(self, method: str, result: CapabilityResult) -> None:
        """Log a capability result for debugging."""
        if result.success:
            logger.debug(f"Capability {self.name}.{method} succeeded")
        else:
            logger.warning(f"Capability {self.name}.{method} failed: {result.error}")
    
    def _classify_error(self, e: Exception) -> Tuple[str, bool, Optional[int]]:
        """
        Classify an exception into error code, retryable flag, and retry delay.
        
        Args:
            e: The exception to classify
            
        Returns:
            Tuple of (error_code, retryable, retry_after_seconds)
        """
        error_type = type(e).__name__
        error_msg = str(e).lower()
        
        # Network/connection errors - retryable
        if any(name in error_type for name in ("Connection", "Connect", "Network", "DNS")):
            return ERROR_NETWORK, True, None
        
        # Timeout errors - retryable
        if "Timeout" in error_type or "timeout" in error_msg:
            return ERROR_TIMEOUT, True, None
        
        # Rate limit errors - retryable with delay
        if "RateLimit" in error_type or "rate limit" in error_msg or "429" in error_msg:
            # Try to extract retry-after from error message
            retry_after = self._extract_retry_after(e)
            return ERROR_RATE_LIMIT, True, retry_after
        
        # Authentication errors - not retryable
        if any(name in error_type for name in ("Auth", "Unauthorized", "Forbidden")):
            return ERROR_AUTH, False, None
        if any(code in error_msg for code in ("401", "403", "unauthorized", "forbidden")):
            return ERROR_AUTH, False, None
        
        # Validation errors - not retryable
        if any(name in error_type for name in ("Validation", "ValueError", "TypeError")):
            return ERROR_VALIDATION, False, None
        
        # Not found - not retryable
        if "NotFound" in error_type or "404" in error_msg:
            return ERROR_NOT_FOUND, False, None
        
        # Server errors (5xx) - retryable
        if any(code in error_msg for code in ("500", "502", "503", "504")):
            return ERROR_SERVER, True, None
        
        # Default - unknown, not retryable
        return ERROR_UNKNOWN, False, None
    
    def _extract_retry_after(self, e: Exception) -> Optional[int]:
        """Extract retry-after seconds from an exception if available."""
        # Check for retry_after attribute
        if hasattr(e, "retry_after"):
            try:
                return int(e.retry_after)
            except (ValueError, TypeError):
                pass
        
        # Try to parse from error message
        error_msg = str(e)
        import re
        match = re.search(r"retry.?after[:\s]+(\d+)", error_msg, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        # Default retry delay for rate limits
        return 60
    
    def _handle_error(self, e: Exception, method: str) -> CapabilityResult:
        """
        Handle an exception and return a properly classified CapabilityResult.
        
        Args:
            e: The exception that occurred
            method: The method name for logging
            
        Returns:
            CapabilityResult with error classification
        """
        error_code, retryable, retry_after = self._classify_error(e)
        
        logger.exception(f"Error in {self.name}.{method}")
        
        return CapabilityResult.fail(
            error=str(e),
            error_code=error_code,
            retryable=retryable,
            retry_after_seconds=retry_after,
        )
