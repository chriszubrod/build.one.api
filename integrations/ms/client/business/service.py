# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.ms.client.business.model import MsClient
from integrations.ms.client.persistence.repo import MsClientRepository


class MsClientService:
    """
    Service for MsClient entity business operations.
    """

    def __init__(self, repo: Optional[MsClientRepository] = None):
        """Initialize the MsClientService."""
        self.repo = repo or MsClientRepository()

    def create(self, *, app: str, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str) -> MsClient:
        """
        Create a new MsClient.
        """
        return self.repo.create(
            app=app,
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id,
            redirect_uri=redirect_uri,
        )

    def read_all(self) -> list[MsClient]:
        """
        Read all MsClients.
        """
        return self.repo.read_all()

    def read_by_app(self, app: str) -> Optional[MsClient]:
        """
        Read a MsClient by app.
        """
        return self.repo.read_by_app(app)

    def update_by_app(self, app: str, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str) -> Optional[MsClient]:
        """
        Update a MsClient by app.
        """
        existing = self.read_by_app(app)
        if existing:
            existing.app = app
            existing.client_id = client_id
            existing.client_secret = client_secret
            existing.tenant_id = tenant_id
            existing.redirect_uri = redirect_uri
            return self.repo.update_by_app(app, client_id, client_secret, tenant_id, redirect_uri)
        return None

    def delete_by_app(self, app: str) -> Optional[MsClient]:
        """
        Delete a MsClient by app.
        """
        existing = self.read_by_app(app)
        if existing:
            existing.app = app
            return self.repo.delete_by_app(app)
        return None
