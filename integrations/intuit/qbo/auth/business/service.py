# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.auth.business.model import QboAuth
from integrations.intuit.qbo.auth.persistence.repo import QboAuthRepository


class QboAuthService:
    """
    Service for QboAuth entity business operations.
    """

    def __init__(self, repo: Optional[QboAuthRepository] = None):
        """Initialize the QboAuthService."""
        self.repo = repo or QboAuthRepository()

    def create(self, *, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> QboAuth:
        """
        Create a new QboAuth.
        """
        return self.repo.create(
            code=code,
            realm_id=realm_id,
            state=state,
            token_type=token_type,
            id_token=id_token,
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            x_refresh_token_expires_in=x_refresh_token_expires_in,
        )

    def read_all(self) -> list[QboAuth]:
        """
        Read all QboAuths.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Read a QboAuth by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def update_by_realm_id(self, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> Optional[QboAuth]:
        """
        Update a QboAuth by realm ID.
        """
        existing = self.read_by_realm_id(realm_id)
        if existing:
            existing.code = code
            existing.realm_id = realm_id
            existing.state = state
            existing.token_type = token_type
            existing.id_token = id_token
            existing.access_token = access_token
            existing.expires_in = expires_in
            existing.refresh_token = refresh_token
            existing.x_refresh_token_expires_in = x_refresh_token_expires_in
            return self.repo.update_by_realm_id(existing)
        return None

    def delete_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Delete a QboAuth by realm ID.
        """
        return self.repo.delete_by_realm_id(realm_id)
