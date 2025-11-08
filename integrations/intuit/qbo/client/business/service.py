# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.client.business.model import QboClient
from integrations.intuit.qbo.client.persistence.repo import QboClientRepository


class QboClientService:
    """
    Service for QboClient entity business operations.
    """

    def __init__(self, repo: Optional[QboClientRepository] = None):
        """Initialize the QboClientService."""
        self.repo = repo or QboClientRepository()

    def create(self, *, client_id: str, client_secret: str) -> QboClient:
        """
        Create a new QboClient.
        """
        return self.repo.create(
            client_id=client_id,
            client_secret=client_secret,
        )

    def read_all(self) -> list[QboClient]:
        """
        Read all QboClients.
        """
        return self.repo.read_all()

    def read_by_client_id(self, client_id: str) -> Optional[QboClient]:
        """
        Read a QboClient by client ID.
        """
        return self.repo.read_by_client_id(client_id)

    def update_by_client_id(self, client_id: str, client_secret: str) -> Optional[QboClient]:
        """
        Update a QboClient by client ID.
        """
        existing = self.read_by_client_id(client_id)
        if existing:
            existing.client_id = client_id
            existing.client_secret = client_secret
            return self.repo.update_by_client_id(client_id, client_secret)
        return None

    def delete_by_client_id(self, client_id: str) -> Optional[QboClient]:
        """
        Delete a QboClient by client ID.
        """
        return self.repo.delete_by_client_id(client_id)
