# Python Standard Library Imports
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CapabilityResult:
    """
    Standard result wrapper for capability operations.
    """
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def ok(cls, data: Any, **metadata) -> "CapabilityResult":
        """Create a successful result."""
        return cls(success=True, data=data, metadata=metadata)
    
    @classmethod
    def fail(cls, error: str, **metadata) -> "CapabilityResult":
        """Create a failed result."""
        return cls(success=False, error=error, metadata=metadata)


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
