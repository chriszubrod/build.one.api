# Python Standard Library Imports
from typing import Optional, Dict, Any

# Third-party Imports

# Local Imports
from modules.integration.business.model import Integration, IntegrationStatus
from modules.integration.business.handlers import IntegrationHandlerFactory
from modules.integration.persistence.repo import IntegrationRepository


class IntegrationService:
    """
    Service for Integration entity business operations.
    """

    def __init__(self, repo: Optional[IntegrationRepository] = None):
        """Initialize the IntegrationService."""
        self.repo = repo or IntegrationRepository()

    def create(self, *, name: str, status: Optional[IntegrationStatus] = None) -> Integration:
        """
        Create a new integration.
        """
        # Default to DISCONNECTED if not provided
        if status is None:
            status = IntegrationStatus.DISCONNECTED
        # Convert enum to string value for database storage
        status_value = status.value if isinstance(status, IntegrationStatus) else status
        return self.repo.create(name=name, status=status_value)

    def read_all(self) -> list[Integration]:
        """
        Read all integrations.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Integration]:
        """
        Read a integration by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Integration]:
        """
        Read a integration by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Integration]:
        """
        Read a integration by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(self, public_id: str, integration) -> Optional[Integration]:
        """
        Update a integration by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = integration.row_version
            existing.name = integration.name
            # Handle enum conversion - if it's already an enum, use it; if it's a string, convert it
            if isinstance(integration.status, IntegrationStatus):
                existing.status = integration.status
            elif isinstance(integration.status, str):
                try:
                    existing.status = IntegrationStatus(integration.status)
                except ValueError:
                    # If invalid status string, keep existing status
                    pass
            else:
                existing.status = integration.status
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Integration]:
        """
        Delete a integration by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None

    def connect(self, public_id: str) -> Dict[str, Any]:
        """
        Connect an integration by routing to the appropriate handler.
        """
        integration = self.read_by_public_id(public_id)
        if not integration:
            return {
                "success": False,
                "redirect_url": None,
                "message": f"Integration with public_id {public_id} not found"
            }
        
        handler = IntegrationHandlerFactory.get_handler(integration)
        if not handler:
            return {
                "success": False,
                "redirect_url": None,
                "message": f"No handler found for integration type: {integration.name}"
            }
        
        return handler.connect(integration)
    
    def disconnect(self, public_id: str) -> Dict[str, Any]:
        """
        Disconnect an integration by routing to the appropriate handler.
        """
        integration = self.read_by_public_id(public_id)
        if not integration:
            return {
                "success": False,
                "message": f"Integration with public_id {public_id} not found"
            }
        
        handler = IntegrationHandlerFactory.get_handler(integration)
        if not handler:
            return {
                "success": False,
                "message": f"No handler found for integration type: {integration.name}"
            }
        
        return handler.disconnect(integration)
