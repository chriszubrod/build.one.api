# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.sync.business.model import Sync
from integrations.sync.persistence.repo import SyncRepository


class SyncService:
    """
    Service for Sync entity business operations.
    """

    def __init__(self, repo: Optional[SyncRepository] = None):
        """Initialize the SyncService."""
        self.repo = repo or SyncRepository()

    def create(self, *, provider: Optional[str], env: Optional[str], entity: Optional[str], last_sync_datetime: Optional[str]) -> Sync:
        """
        Create a new sync record.
        """
        return self.repo.create(provider=provider, env=env, entity=entity, last_sync_datetime=last_sync_datetime)

    def read_all(self) -> list[Sync]:
        """
        Read all sync records.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Sync]:
        """
        Read a sync record by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Sync]:
        """
        Read a sync record by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_provider(self, provider: str) -> Optional[Sync]:
        """
        Read a sync record by provider.
        """
        return self.repo.read_by_provider(provider)

    def update_by_public_id(self, public_id: str, sync) -> Optional[Sync]:
        """
        Update a sync record by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = sync.row_version
            existing.provider = sync.provider
            existing.env = sync.env
            existing.entity = sync.entity
            existing.last_sync_datetime = sync.last_sync_datetime
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str) -> Optional[Sync]:
        """
        Delete a sync record by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
