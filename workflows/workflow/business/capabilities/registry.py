# Python Standard Library Imports
import logging
from typing import Optional, TYPE_CHECKING

# Local Imports
from workflows.workflow.business.capabilities.base import Capability

if TYPE_CHECKING:
    from workflows.workflow.business.capabilities.llm import LlmCapabilities
    from workflows.workflow.business.capabilities.document import DocumentCapabilities
    from workflows.workflow.business.capabilities.email import EmailCapabilities
    from workflows.workflow.business.capabilities.entity import EntityCapabilities
    from workflows.workflow.business.capabilities.sharepoint import SharePointCapabilities
    from workflows.workflow.business.capabilities.storage import StorageCapabilities
    from workflows.workflow.business.capabilities.sync import SyncCapabilities

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """
    Central registry for all agent capabilities.
    
    Provides dependency injection and lazy initialization
    of capability instances.
    """
    
    def __init__(self):
        self._capabilities: dict[str, Capability] = {}
        self._initialized = False
    
    def register(self, capability: Capability) -> None:
        """Register a capability instance."""
        self._capabilities[capability.name] = capability
        logger.info(f"Registered capability: {capability.name}")
    
    def get(self, name: str) -> Optional[Capability]:
        """Get a capability by name."""
        return self._capabilities.get(name)
    
    def initialize_all(self) -> None:
        """Initialize all standard capabilities."""
        if self._initialized:
            return
        
        # Import here to avoid circular imports
        from workflows.workflow.business.capabilities.llm import LlmCapabilities
        from workflows.workflow.business.capabilities.document import DocumentCapabilities
        from workflows.workflow.business.capabilities.email import EmailCapabilities
        from workflows.workflow.business.capabilities.entity import EntityCapabilities
        from workflows.workflow.business.capabilities.sharepoint import SharePointCapabilities
        from workflows.workflow.business.capabilities.storage import StorageCapabilities
        from workflows.workflow.business.capabilities.sync import SyncCapabilities
        
        self.register(LlmCapabilities())
        self.register(DocumentCapabilities())
        self.register(EmailCapabilities())
        self.register(EntityCapabilities())
        self.register(SharePointCapabilities())
        self.register(StorageCapabilities())
        self.register(SyncCapabilities())
        
        self._initialized = True
        logger.info("All capabilities initialized")
    
    @property
    def llm(self) -> "LlmCapabilities":
        """Get LLM capabilities."""
        return self._capabilities.get("llm")
    
    @property
    def document(self) -> "DocumentCapabilities":
        """Get document capabilities."""
        return self._capabilities.get("document")
    
    @property
    def email(self) -> "EmailCapabilities":
        """Get email capabilities."""
        return self._capabilities.get("email")
    
    @property
    def entity(self) -> "EntityCapabilities":
        """Get entity capabilities."""
        return self._capabilities.get("entity")
    
    @property
    def sharepoint(self) -> "SharePointCapabilities":
        """Get SharePoint capabilities."""
        return self._capabilities.get("sharepoint")
    
    @property
    def storage(self) -> "StorageCapabilities":
        """Get storage capabilities."""
        return self._capabilities.get("storage")
    
    @property
    def sync(self) -> "SyncCapabilities":
        """Get sync capabilities."""
        return self._capabilities.get("sync")


# Global registry instance
_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    """Get the global capability registry instance."""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _registry.initialize_all()
    return _registry
