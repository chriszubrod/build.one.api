# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.vendor_type_required_coverage.business.model import VendorTypeRequiredCoverage
from entities.vendor_type_required_coverage.persistence.repo import VendorTypeRequiredCoverageRepository
from shared.authz import current_user_id


class VendorTypeRequiredCoverageService:
    """
    Service for VendorTypeRequiredCoverage entity business operations.
    """

    def __init__(self, repo: Optional[VendorTypeRequiredCoverageRepository] = None):
        """Initialize the VendorTypeRequiredCoverageService."""
        self.repo = repo or VendorTypeRequiredCoverageRepository()

    def read_all(self) -> list[VendorTypeRequiredCoverage]:
        """
        Read all vendor type required coverage rows.
        """
        return self.repo.read_all()

    def read_by_vendor_type_id(self, vendor_type_id: int) -> list[VendorTypeRequiredCoverage]:
        """
        Read required coverages for a vendor type.
        """
        return self.repo.read_by_vendor_type_id(vendor_type_id=vendor_type_id)

    def create(
        self,
        *,
        vendor_type_id: int,
        coverage_type: str,
    ) -> VendorTypeRequiredCoverage:
        """
        Create a required coverage rule for a vendor type.
        """
        return self.repo.create(
            vendor_type_id=vendor_type_id,
            coverage_type=coverage_type,
            created_by_user_id=current_user_id.get(),
        )

    def delete_by_id(self, id: int) -> bool:
        """
        Hard-delete a vendor type required coverage row by ID.
        """
        return self.repo.delete_by_id(id=id)

    def delete_by_public_id(self, public_id: str) -> bool:
        """
        Hard-delete a vendor type required coverage row by public ID.
        """
        return self.repo.delete_by_public_id(public_id=public_id)
