# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.address.business.model import Address, Country
from modules.address.persistence.repo import AddressRepository


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

    def read_by_id(self, id: str) -> Optional[Address]:
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

    def update_by_public_id(self, public_id: str, address) -> Optional[Address]:
        """
        Update an address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
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
        """
        Delete an address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
