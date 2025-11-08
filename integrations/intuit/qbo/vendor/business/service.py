# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository


class QboVendorService:
    """
    Service for QboVendor entity business operations.
    """

    def __init__(self, repo: Optional[QboVendorRepository] = None):
        """Initialize the QboVendorService."""
        self.repo = repo or QboVendorRepository()

    def create(self, *, id: Optional[str], sync_token: Optional[str], display_name: Optional[str], vendor_1099: Optional[int], company_name: Optional[str], tax_identifier: Optional[str], print_on_check_name: Optional[str], bill_addr_id: Optional[str]) -> QboVendor:
        """
        Create a new QboVendor.
        """
        return self.repo.create(
            id=id,
            sync_token=sync_token,
            display_name=display_name,
            vendor_1099=vendor_1099,
            company_name=company_name,
            tax_identifier=tax_identifier,
            print_on_check_name=print_on_check_name,
            bill_addr_id=bill_addr_id,
        )

    def read_all(self) -> list[QboVendor]:
        """
        Read all QboVendors.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_sync_token(self, sync_token: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by sync token.
        """
        return self.repo.read_by_sync_token(sync_token)

    def read_by_display_name(self, display_name: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by display name.
        """
        return self.repo.read_by_display_name(display_name)

    def read_by_company_name(self, company_name: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by company name.
        """
        return self.repo.read_by_company_name(company_name)
    
    def read_by_tax_identifier(self, tax_identifier: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by tax identifier.
        """
        return self.repo.read_by_tax_identifier(tax_identifier)

    def update_by_id(self, id: str, sync_token: str, display_name: str, vendor_1099: int, company_name: str, tax_identifier: str, print_on_check_name: str, bill_addr_id: str) -> Optional[QboVendor]:
        """
        Update a QboVendor by public ID.
        """
        existing = self.read_by_id(id=id)
        if existing:
            existing.sync_token = sync_token
            existing.display_name = display_name
            existing.vendor_1099 = vendor_1099
            existing.company_name = company_name
            existing.tax_identifier = tax_identifier
            existing.print_on_check_name = print_on_check_name
            existing.bill_addr_id = bill_addr_id
            return self.repo.update_by_id(id=id, sync_token=sync_token, display_name=display_name, vendor_1099=vendor_1099, company_name=company_name, tax_identifier=tax_identifier, print_on_check_name=print_on_check_name, bill_addr_id=bill_addr_id)
        return None

    def delete_by_id(self, id: str) -> Optional[QboVendor]:
        """
        Delete a QboVendor by ID.
        """
        return self.repo.delete_by_id(id=id)
