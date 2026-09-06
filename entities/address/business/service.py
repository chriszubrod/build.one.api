# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.address.business.model import Address, Country
from entities.address.persistence.repo import AddressRepository
from shared.database import DatabaseConstraintError
from shared.db_constraints import FK_REFERENCE_MESSAGE, FK_REFERENCE_VIOLATION


class AddressService:
    """
    Service for Address entity business operations.
    """

    def __init__(self, repo: Optional[AddressRepository] = None):
        """Initialize the AddressService."""
        self.repo = repo or AddressRepository()

    def create(self, *, street_one: str, street_two: Optional[str] = None, city: str, state: str, zip: str) -> Address:
        """
        Create a new address.
        """
        # Country is always United States
        country = Country.UNITED_STATES
        return self.repo.create(street_one=street_one, street_two=street_two, city=city, state=state, zip=zip, country=country)

    def read_all(self) -> list[Address]:
        """
        Read all addresses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Address]:
        """
        Read an address by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Address]:
        """
        Read an address by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_street_one_and_city(self, street_one: str, city: str) -> Optional[Address]:
        """
        Read an address by street one and city.
        """
        return self.repo.read_by_street_one_and_city(street_one=street_one, city=city)

    def read_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None) -> Optional[Address]:
        """
        Read an address directly by its dbo-native QBO identity (U-277) — the
        Phase-4 repoint seam, bypassing the qbo.PhysicalAddress staging table.
        """
        return self.repo.read_by_qbo_identity(qbo_id, realm_id)

    def read_deleted_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None) -> Optional[Address]:
        """U-370 C1 guard passthrough — see AddressRepository.read_deleted_by_qbo_identity."""
        return self.repo.read_deleted_by_qbo_identity(qbo_id, realm_id)

    def set_qbo_identity(
        self,
        *,
        id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str] = None,
    ) -> None:
        """Stamp dbo-native QBO identity. Bare passthrough.

        Connectors still call ``.repo.set_qbo_identity`` (same as Bill /
        Customer / Project). This exists so HTTP/service callers do not
        have to reach through the repository.
        """
        self.repo.set_qbo_identity(id=id, qbo_id=qbo_id, realm_id=realm_id)

    def update_by_public_id(self, public_id: str, address) -> Optional[Address]:
        """
        Update an address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        existing.row_version = address.row_version
        existing.street_one = address.street_one
        existing.street_two = address.street_two
        existing.city = address.city
        existing.state = address.state
        existing.zip = address.zip
        # Country is always United States
        existing.country = Country.UNITED_STATES
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Address]:
        """Soft-delete an unused address. Linked rows stay 422 (A4 / C1)."""
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        deleted = self.repo.delete_by_id(existing.id)
        if deleted is None:
            raise DatabaseConstraintError(FK_REFERENCE_VIOLATION, FK_REFERENCE_MESSAGE)
        return deleted
