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

    def create(self, *, app: str, client_id: str, client_secret: str) -> QboClient:
        """
        Create a new QboClient.
        """
        return self.repo.create(
            app=app,
            client_id=client_id,
            client_secret=client_secret,
        )

    def read_all(self) -> list[QboClient]:
        """
        Read all QboClients.
        """
        return self.repo.read_all()

    def read_by_app(self, app: str) -> Optional[QboClient]:
        """
        Read a QboClient by app.
        """
        return self.repo.read_by_app(app)

    def update_by_app(self, app: str, client_id: str, client_secret: str) -> Optional[QboClient]:
        """
        Update a QboClient by app.
        """
        existing = self.read_by_app(app)
        if existing:
            existing.app = app
            existing.client_id = client_id
            existing.client_secret = client_secret
            return self.repo.update_by_app(app, client_id, client_secret)
        return None

    def delete_by_app(self, app: str) -> Optional[QboClient]:
        """
        Delete a QboClient by app.
        """
        existing = self.read_by_app(app)
        if existing:
            existing.app = app
            return self.repo.delete_by_app(app)
        return None
