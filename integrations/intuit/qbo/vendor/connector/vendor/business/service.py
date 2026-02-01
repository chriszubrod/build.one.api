# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.connector.vendor.business.model import VendorVendor
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from services.vendor.business.service import VendorService
from services.vendor.business.model import Vendor
from services.vendor_address.business.service import VendorAddressService

logger = logging.getLogger(__name__)

# Address type ID for billing (typically ID 1)
ADDRESS_TYPE_BILLING = 1


class VendorVendorConnector:
    """
    Connector service for synchronization between QboVendor and Vendor modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[VendorVendorRepository] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_address_service: Optional[VendorAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
    ):
        """Initialize the VendorVendorConnector."""
        self.mapping_repo = mapping_repo or VendorVendorRepository()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_address_service = vendor_address_service or VendorAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()

    def sync_from_qbo_vendor(self, qbo_vendor: QboVendor) -> Vendor:
        """
        Sync data from QboVendor to Vendor module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Vendor accordingly
        
        Args:
            qbo_vendor: QboVendor record
        
        Returns:
            Vendor: The synced Vendor record
        """
        # Map QBO Vendor fields to Vendor module fields
        vendor_name = qbo_vendor.display_name
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        
        if mapping:
            # Found existing mapping - update the Vendor
            vendor = self.vendor_service.read_by_id(mapping.vendor_id)
            if vendor:
                logger.info(f"Updating existing Vendor {vendor.id} from QboVendor {qbo_vendor.id}")
                vendor.name = vendor_name
                vendor = self.vendor_service.repo.update_by_id(vendor)
                
                # Sync addresses for existing vendor
                self._sync_addresses(qbo_vendor, vendor.id)
                
                return vendor
            else:
                # Mapping exists but Vendor not found - recreate Vendor
                logger.warning(f"Mapping exists but Vendor {mapping.vendor_id} not found. Creating new Vendor.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Vendor
        logger.info(f"Creating new Vendor from QboVendor {qbo_vendor.id}: name={vendor_name}")
        vendor = self.vendor_service.create(
            name=vendor_name,
            abbreviation=None,
            is_draft=False
        )
        
        # Create mapping
        vendor_id = int(vendor.id) if isinstance(vendor.id, str) else vendor.id
        try:
            mapping = self.create_mapping(vendor_id=vendor_id, qbo_vendor_id=qbo_vendor.id)
            logger.info(f"Created mapping: Vendor {vendor_id} <-> QboVendor {qbo_vendor.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Sync addresses for new vendor
        self._sync_addresses(qbo_vendor, vendor_id)
        
        return vendor

    def _sync_addresses(self, qbo_vendor: QboVendor, vendor_id: int) -> None:
        """
        Sync billing address from QboVendor to VendorAddress/Address.
        
        Args:
            qbo_vendor: QboVendor with bill_addr_id
            vendor_id: Database ID of the Vendor
        """
        # Sync billing address
        if qbo_vendor.bill_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_vendor.bill_addr_id)
                address_id = int(address.id) if isinstance(address.id, str) else address.id
                self._ensure_vendor_address(vendor_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Vendor {vendor_id}")
            except Exception as e:
                logger.error(f"Failed to sync billing address for Vendor {vendor_id}: {e}")

    def _ensure_vendor_address(self, vendor_id: int, address_id: int, address_type_id: int) -> None:
        """
        Ensure a VendorAddress record exists linking Vendor to Address.
        Creates if not exists, updates if exists with different address.
        
        Args:
            vendor_id: Database ID of the Vendor
            address_id: Database ID of the Address
            address_type_id: Type of address (billing)
        """
        # Check for existing VendorAddress by vendor_id
        # Note: read_by_vendor_id may return a single VendorAddress or None
        existing_address = self.vendor_address_service.read_by_vendor_id(str(vendor_id))
        
        # Also check by vendor_id and address_type_id by reading all and filtering
        # This handles the case where a vendor might have multiple addresses
        all_vendor_addresses = self.vendor_address_service.read_all()
        existing = None
        for va in all_vendor_addresses:
            va_vendor_id = int(va.vendor_id) if isinstance(va.vendor_id, str) else va.vendor_id
            va_address_type_id = int(va.address_type_id) if isinstance(va.address_type_id, str) else va.address_type_id
            if va_vendor_id == vendor_id and va_address_type_id == address_type_id:
                existing = va
                break
        
        if existing:
            existing_address_id = int(existing.address_id) if isinstance(existing.address_id, str) else existing.address_id
            if existing_address_id != address_id:
                # Update with new address
                existing.address_id = str(address_id)
                self.vendor_address_service.repo.update_by_id(existing)
                logger.debug(f"Updated VendorAddress {existing.id} with new address {address_id}")
        else:
            # Create new VendorAddress
            self.vendor_address_service.create(
                vendor_id=str(vendor_id),
                address_id=str(address_id),
                address_type_id=str(address_type_id)
            )
            logger.debug(f"Created VendorAddress for Vendor {vendor_id}, Address {address_id}, Type {address_type_id}")

    def create_mapping(self, vendor_id: int, qbo_vendor_id: int) -> VendorVendor:
        """
        Create a mapping between Vendor and QboVendor.
        
        Args:
            vendor_id: Database ID of Vendor record
            qbo_vendor_id: Database ID of QboVendor record
        
        Returns:
            VendorVendor: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_vendor = self.mapping_repo.read_by_vendor_id(vendor_id)
        if existing_by_vendor:
            raise ValueError(
                f"Vendor {vendor_id} is already mapped to QboVendor {existing_by_vendor.qbo_vendor_id}"
            )
        
        existing_by_qbo_vendor = self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor_id)
        if existing_by_qbo_vendor:
            raise ValueError(
                f"QboVendor {qbo_vendor_id} is already mapped to Vendor {existing_by_qbo_vendor.vendor_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(vendor_id=vendor_id, qbo_vendor_id=qbo_vendor_id)

    def get_mapping_by_vendor_id(self, vendor_id: int) -> Optional[VendorVendor]:
        """
        Get mapping by Vendor ID.
        """
        return self.mapping_repo.read_by_vendor_id(vendor_id)

    def get_mapping_by_qbo_vendor_id(self, qbo_vendor_id: int) -> Optional[VendorVendor]:
        """
        Get mapping by QboVendor ID.
        """
        return self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor_id)
