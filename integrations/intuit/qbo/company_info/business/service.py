# Python Standard Library Imports
import json
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo
from integrations.intuit.qbo.company_info.persistence.repo import QboCompanyInfoRepository
from integrations.intuit.qbo.company_info.external.client import QboCompanyInfoClient
from integrations.intuit.qbo.company_info.external.schemas import QboCompanyInfo as QboCompanyInfoExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.physical_address.persistence.repo import QboPhysicalAddressRepository

logger = logging.getLogger(__name__)


class QboCompanyInfoService:
    """
    Service for QboCompanyInfo entity business operations.
    """

    def __init__(self, repo: Optional[QboCompanyInfoRepository] = None):
        """Initialize the QboCompanyInfoService."""
        self.repo = repo or QboCompanyInfoRepository()

    def sync_from_qbo(self, realm_id: str) -> QboCompanyInfo:
        """
        Fetch CompanyInfo from QBO API and store locally.
        Extracts PhysicalAddress objects and creates/updates them first.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
        
        Returns:
            QboCompanyInfo: The synced company info record
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        print(f"QBO Auth: {qbo_auth}")
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch CompanyInfo from QBO API
        with QboCompanyInfoClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_company_info: QboCompanyInfoExternalSchema = client.get_company_info()
        
        if not qbo_company_info:
            raise ValueError("No CompanyInfo found in QBO API response")
        
        # Extract and sync PhysicalAddress records
        company_addr_id = None
        legal_addr_id = None
        customer_communication_addr_id = None
        
        # Sync CompanyAddr
        if qbo_company_info.company_addr:
            company_addr_id = self._sync_physical_address(
                qbo_auth.access_token,
                realm_id,
                qbo_company_info.company_addr,
                f"{realm_id}-company"
            )
        
        # Sync LegalAddr
        if qbo_company_info.legal_addr:
            legal_addr_id = self._sync_physical_address(
                qbo_auth.access_token,
                realm_id,
                qbo_company_info.legal_addr,
                f"{realm_id}-legal"
            )
        
        # Sync CustomerCommunicationAddr
        if qbo_company_info.customer_communication_addr:
            customer_communication_addr_id = self._sync_physical_address(
                qbo_auth.access_token,
                realm_id,
                qbo_company_info.customer_communication_addr,
                f"{realm_id}-customer-communication"
            )
        
        # Extract email and web address strings
        email_str = None
        if qbo_company_info.email and hasattr(qbo_company_info.email, 'address'):
            email_str = qbo_company_info.email.address
        
        web_addr_str = None
        if qbo_company_info.web_addr and hasattr(qbo_company_info.web_addr, 'uri'):
            web_addr_str = qbo_company_info.web_addr.uri
        
        # Extract currency ref as JSON string
        currency_ref_str = None
        if qbo_company_info.currency_ref:
            currency_ref_str = json.dumps(qbo_company_info.currency_ref.dict(exclude_none=True))
        
        # Check if CompanyInfo already exists
        existing = self.repo.read_by_realm_id(realm_id=realm_id)
        
        if existing:
            # Update existing record
            logger.info(f"Updating existing QBO company info for realm_id: {realm_id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_company_info.id or existing.qbo_id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_company_info.sync_token or existing.sync_token,
                realm_id=realm_id,
                company_name=qbo_company_info.company_name,
                legal_name=qbo_company_info.legal_name,
                company_addr_id=company_addr_id,
                legal_addr_id=legal_addr_id,
                customer_communication_addr_id=customer_communication_addr_id,
                tax_payer_id=qbo_company_info.tax_payer_id,
                fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
                country=qbo_company_info.country,
                email=email_str,
                web_addr=web_addr_str,
                currency_ref=currency_ref_str,
            )
        else:
            # Create new record
            logger.info(f"Creating new QBO company info for realm_id: {realm_id}")
            return self.repo.create(
                qbo_id=qbo_company_info.id,
                sync_token=qbo_company_info.sync_token,
                realm_id=realm_id,
                company_name=qbo_company_info.company_name,
                legal_name=qbo_company_info.legal_name,
                company_addr_id=company_addr_id,
                legal_addr_id=legal_addr_id,
                customer_communication_addr_id=customer_communication_addr_id,
                tax_payer_id=qbo_company_info.tax_payer_id,
                fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
                country=qbo_company_info.country,
                email=email_str,
                web_addr=web_addr_str,
                currency_ref=currency_ref_str,
            )

    def _sync_physical_address(
        self,
        access_token: str,
        realm_id: str,
        address_ref,
        qbo_id: str
    ) -> Optional[int]:
        """
        Sync a PhysicalAddress record and return its database ID.
        
        Args:
            access_token: QBO OAuth access token
            realm_id: QBO company realm ID
            address_ref: QboPhysicalAddressRef object from CompanyInfo
            qbo_id: QBO ID to use for the address record
        
        Returns:
            int: The PhysicalAddress.Id, or None if address is empty
        """
        if not address_ref or not any([
            address_ref.line1,
            address_ref.line2,
            address_ref.city,
            address_ref.country
        ]):
            return None
        
        # Check if PhysicalAddress already exists
        physical_address_repo = QboPhysicalAddressRepository()
        existing = physical_address_repo.read_by_qbo_id(qbo_id=qbo_id)
        
        if existing:
            # Update existing PhysicalAddress
            logger.debug(f"Updating existing PhysicalAddress with QBO ID: {qbo_id}")
            updated = physical_address_repo.update_by_id(
                id=existing.id,
                row_version=existing.row_version_bytes,
                qbo_id=qbo_id,
                line1=address_ref.line1,
                line2=address_ref.line2,
                city=address_ref.city,
                country=address_ref.country,
                country_sub_division_code=address_ref.country_sub_division_code,
                postal_code=address_ref.postal_code,
            )
            return updated.id if updated else None
        else:
            # Create new PhysicalAddress
            logger.debug(f"Creating new PhysicalAddress with QBO ID: {qbo_id}")
            created = physical_address_repo.create(
                qbo_id=qbo_id,
                line1=address_ref.line1,
                line2=address_ref.line2,
                city=address_ref.city,
                country=address_ref.country,
                country_sub_division_code=address_ref.country_sub_division_code,
                postal_code=address_ref.postal_code,
            )
            return created.id if created else None

    def read_all(self) -> list[QboCompanyInfo]:
        """
        Read all QboCompanyInfos.
        """
        return self.repo.read_all()

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboCompanyInfo]:
        """
        Read a QboCompanyInfo by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_realm_id(self, realm_id: str) -> Optional[QboCompanyInfo]:
        """
        Read a QboCompanyInfo by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

