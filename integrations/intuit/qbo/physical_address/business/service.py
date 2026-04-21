# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from integrations.intuit.qbo.physical_address.persistence.repo import QboPhysicalAddressRepository
from integrations.intuit.qbo.physical_address.external.client import QboPhysicalAddressClient

logger = logging.getLogger(__name__)


class QboPhysicalAddressService:
    """
    Service for QboPhysicalAddress entity business operations.
    """

    def __init__(self, repo: Optional[QboPhysicalAddressRepository] = None):
        """Initialize the QboPhysicalAddressService."""
        self.repo = repo or QboPhysicalAddressRepository()

    def create(
        self,
        *,
        qbo_id: Optional[str],
        line1: Optional[str],
        line2: Optional[str],
        city: Optional[str],
        country: Optional[str],
        country_sub_division_code: Optional[str],
        postal_code: Optional[str],
    ) -> QboPhysicalAddress:
        """
        Create a new QboPhysicalAddress.
        """
        return self.repo.create(
            qbo_id=qbo_id,
            line1=line1,
            line2=line2,
            city=city,
            country=country,
            country_sub_division_code=country_sub_division_code,
            postal_code=postal_code,
        )

    def read_all(self) -> list[QboPhysicalAddress]:
        """
        Read all QboPhysicalAddresses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[QboPhysicalAddress]:
        """
        Read a QboPhysicalAddress by ID.
        """
        return self.repo.read_by_id(id=id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboPhysicalAddress]:
        """
        Read a QboPhysicalAddress by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id=qbo_id)

    def update_by_id(
        self,
        id: int,
        row_version: bytes,
        qbo_id: Optional[str],
        line1: Optional[str],
        line2: Optional[str],
        city: Optional[str],
        country: Optional[str],
        country_sub_division_code: Optional[str],
        postal_code: Optional[str],
    ) -> Optional[QboPhysicalAddress]:
        """
        Update a QboPhysicalAddress by ID.
        """
        existing = self.read_by_id(id=id)
        if existing:
            return self.repo.update_by_id(
                id=id,
                row_version=row_version,
                qbo_id=qbo_id,
                line1=line1,
                line2=line2,
                city=city,
                country=country,
                country_sub_division_code=country_sub_division_code,
                postal_code=postal_code,
            )
        return None

    def delete_by_id(self, id: int) -> Optional[QboPhysicalAddress]:
        """
        Delete a QboPhysicalAddress by ID.
        """
        existing = self.read_by_id(id=id)
        if existing:
            return self.repo.delete_by_id(id=id)
        return None

    def sync_from_qbo(
        self,
        *,
        realm_id: str,
        qbo_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> QboPhysicalAddress:
        """
        Fetch a QBO physical address from QBO CompanyInfo API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.

        Args:
            realm_id: QBO company realm ID
            qbo_id: Optional QBO ID to use for the address record
                    (defaults to realm_id if not provided)
            access_token: Deprecated — kept for backward compatibility with the
                          public API route. QboHttpClient resolves and refreshes
                          the token lazily via the auth service, so this value
                          is ignored.

        Returns:
            QboPhysicalAddress: The synced address record
        """
        with QboPhysicalAddressClient(realm_id=realm_id) as client:
            qbo_address = client.get_physical_address(qbo_id=qbo_id)
            
            if not qbo_address:
                raise ValueError("No PhysicalAddress found in CompanyInfo response")
            
            # Use realm_id as the ID if not provided
            record_id = qbo_id or realm_id
            
            # Check if record already exists
            existing = self.read_by_qbo_id(qbo_id=record_id)
            
            if existing:
                # Update existing record
                logger.info(f"Updating existing QBO physical address with QBO ID: {record_id}")
                return self.repo.update_by_id(
                    id=existing.id,
                    row_version=existing.row_version,
                    qbo_id=record_id,
                    line1=qbo_address.line1,
                    line2=qbo_address.line2,
                    city=qbo_address.city,
                    country=qbo_address.country,
                    country_sub_division_code=qbo_address.country_sub_division_code,
                    postal_code=qbo_address.postal_code,
                )
            else:
                # Create new record
                logger.info(f"Creating new QBO physical address with ID: {record_id}")
                return self.repo.create(
                    qbo_id=record_id,
                    line1=qbo_address.line1,
                    line2=qbo_address.line2,
                    city=qbo_address.city,
                    country=qbo_address.country,
                    country_sub_division_code=qbo_address.country_sub_division_code,
                    postal_code=qbo_address.postal_code,
                )

