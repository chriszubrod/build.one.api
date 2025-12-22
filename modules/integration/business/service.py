# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.integration.business.model import Integration
from modules.integration.persistence.repo import IntegrationRepository


class IntegrationService:
    """
    Service for Integration entity business operations.
    """

    def __init__(self, repo: Optional[IntegrationRepository] = None):
        """Initialize the IntegrationService."""
        self.repo = repo or IntegrationRepository()

    def create(self, *, name: str, status: str, endpoint: str) -> Integration:
        """
        Create a new integration.
        """
        return self.repo.create(name=name, status=status, endpoint=endpoint)

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
            existing.status = integration.status
            existing.endpoint = integration.endpoint
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Integration]:
        """
        Delete a integration by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
