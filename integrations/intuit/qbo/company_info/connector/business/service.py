# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.company_info.connector.business.model import CompanyInfoCompany
from integrations.intuit.qbo.company_info.connector.persistence.repo import CompanyInfoCompanyRepository
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo as QboCompanyInfoModel
from integrations.intuit.qbo.company_info.external.client import QboCompanyInfoClient
from integrations.intuit.qbo.company_info.external.schemas import QboCompanyInfo as QboCompanyInfoExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from services.company.business.service import CompanyService
from services.company.business.model import Company

logger = logging.getLogger(__name__)


class CompanyInfoCompanyConnector:
    """
    Connector service for bidirectional synchronization between QboCompanyInfo and Company modules.
    Implements conflict resolution using last-write-wins logic.
    """

    def __init__(
        self,
        mapping_repo: Optional[CompanyInfoCompanyRepository] = None,
        company_service: Optional[CompanyService] = None,
        qbo_company_info_service: Optional[QboCompanyInfoService] = None,
    ):
        """Initialize the CompanyInfoCompanyConnector."""
        self.mapping_repo = mapping_repo or CompanyInfoCompanyRepository()
        self.company_service = company_service or CompanyService()
        self.qbo_company_info_service = qbo_company_info_service or QboCompanyInfoService()

    def sync_from_qbo_to_company(self, qbo_company_info_id: int, realm_id: str) -> Company:
        """
        Sync data from QboCompanyInfo to Company module.
        
        This method prevents duplicate Company records by:
        1. First checking if a valid mapping exists
        2. If mapping is broken/missing, searching for existing Company by name
        3. Only creating a new Company if no match is found
        
        Args:
            qbo_company_info_id: Database ID of QboCompanyInfo record
            realm_id: QBO realm ID for API calls
        
        Returns:
            Company: The synced Company record
        """
        # Read QboCompanyInfo
        qbo_company_info_repo = self.qbo_company_info_service.repo
        qbo_company_info = qbo_company_info_repo.read_by_id(qbo_company_info_id)
        
        if not qbo_company_info:
            raise ValueError(f"QboCompanyInfo with ID {qbo_company_info_id} not found")
        
        # Map QBO fields to Company fields
        # Company.Name maps to CompanyInfo.LegalName
        company_name = qbo_company_info.legal_name
        company_website = qbo_company_info.web_addr
        
        # Step 1: Try to find Company via existing mapping
        mapping = self.mapping_repo.read_by_qbo_company_info_id(qbo_company_info_id)
        company = None
        needs_mapping_repair = False
        
        if mapping:
            company = self.company_service.read_by_id(str(mapping.company_id))
            if not company:
                logger.warning(f"Mapping exists but Company {mapping.company_id} not found. Will search by name.")
                needs_mapping_repair = True
        
        # Step 2: If no Company found via mapping, search by name to prevent duplicates
        if not company and company_name:
            existing_company = self.company_service.read_by_name(company_name)
            if existing_company:
                logger.info(f"Found existing Company by name '{company_name}' (ID: {existing_company.id}). Using existing record.")
                company = existing_company
                
                # Check if this Company is already mapped to a different QboCompanyInfo
                existing_company_mapping = self.mapping_repo.read_by_company_id(
                    int(existing_company.id) if isinstance(existing_company.id, str) else existing_company.id
                )
                if existing_company_mapping and existing_company_mapping.qbo_company_info_id != qbo_company_info_id:
                    logger.warning(
                        f"Company {existing_company.id} is already mapped to QboCompanyInfo {existing_company_mapping.qbo_company_info_id}. "
                        f"Cannot remap to QboCompanyInfo {qbo_company_info_id}."
                    )
                    # Still update the Company data, but don't change mapping
                    needs_mapping_repair = False
                    mapping = existing_company_mapping
                else:
                    needs_mapping_repair = True
        
        # Step 3: Update existing Company or create new one
        if company:
            qbo_modified = self._parse_datetime(qbo_company_info.modified_datetime)
            company_modified = self._parse_datetime(company.modified_datetime)
            
            # Check if Company data actually changed
            data_changed = (
                company.name != company_name or
                company.website != company_website
            )
            
            # Always update Company when syncing from QBO (QBO is source of truth)
            if qbo_modified and company_modified:
                if qbo_modified > company_modified:
                    logger.info(
                        f"QBO CompanyInfo is newer (QBO: {qbo_modified}, Company: {company_modified}). "
                        f"Updating Company {company.id} with QBO data."
                    )
                elif company_modified > qbo_modified:
                    logger.warning(
                        f"Conflict: Company ModifiedDatetime ({company_modified}) is newer than QBO ({qbo_modified}), "
                        f"but updating Company {company.id} with QBO data (QBO sync takes precedence)."
                    )
                else:
                    if data_changed:
                        logger.debug("ModifiedDatetime matches but data differs - updating Company for consistency")
                    else:
                        logger.debug("Updating Company ModifiedDatetime to reflect sync time (data unchanged)")
            else:
                if data_changed:
                    logger.debug("Missing ModifiedDatetime(s) but data differs - updating Company")
                else:
                    logger.debug("Updating Company ModifiedDatetime to reflect sync time (data unchanged)")
            
            # Update Company
            company.name = company_name
            company.website = company_website
            company = self.company_service.repo.update_by_id(company)
            if company:
                logger.info(f"Successfully updated Company {company.id}. New ModifiedDatetime: {company.modified_datetime}")
            else:
                logger.error(f"Failed to update Company - update_by_id returned None")
                raise ValueError("Failed to update Company")
        else:
            # No existing Company found - create new one
            logger.info(f"No existing Company found. Creating new Company from QboCompanyInfo {qbo_company_info_id}")
            company = self.company_service.create(name=company_name or "", website=company_website or "")
            needs_mapping_repair = True
        
        # Step 4: Repair or create mapping if needed
        if needs_mapping_repair:
            company_id_int = int(company.id) if isinstance(company.id, str) else company.id
            
            # Delete old broken mapping if it exists
            if mapping and mapping.company_id != company_id_int:
                logger.info(f"Deleting broken mapping (old Company ID: {mapping.company_id})")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
            
            # Create new mapping if needed
            if not mapping:
                try:
                    mapping = self.create_mapping(company_id_int, qbo_company_info_id)
                    logger.info(f"Created mapping: Company {company_id_int} <-> QboCompanyInfo {qbo_company_info_id}")
                except ValueError as e:
                    logger.warning(f"Could not create mapping: {e}")
        
        return company

    def sync_from_company_to_qbo(self, company_id: int, realm_id: str) -> QboCompanyInfoModel:
        """
        Sync data from Company module to QboCompanyInfo.
        
        Args:
            company_id: Database ID of Company record
            realm_id: QBO realm ID for API calls
        
        Returns:
            QboCompanyInfo: The synced QboCompanyInfo record
        """
        # Read Company
        company = self.company_service.read_by_id(str(company_id))
        
        if not company:
            raise ValueError(f"Company with ID {company_id} not found")
        
        # Find mapping
        mapping = self.mapping_repo.read_by_company_id(company_id)
        
        if not mapping:
            raise ValueError(f"No mapping found for Company {company_id}. Create mapping first.")
        
        # Read QboCompanyInfo
        qbo_company_info_repo = self.qbo_company_info_service.repo
        qbo_company_info = qbo_company_info_repo.read_by_id(mapping.qbo_company_info_id)
        
        if not qbo_company_info:
            raise ValueError(f"QboCompanyInfo with ID {mapping.qbo_company_info_id} not found")
        
        # Compare ModifiedDatetime for conflict resolution
        qbo_modified = self._parse_datetime(qbo_company_info.modified_datetime)
        company_modified = self._parse_datetime(company.modified_datetime)
        
        if qbo_modified and company_modified:
            if company_modified > qbo_modified:
                # Company is newer - update QBO
                logger.info(
                    f"Company is newer (Company: {company_modified}, QBO: {qbo_modified}). "
                    f"Updating QboCompanyInfo {qbo_company_info.id} with Company data."
                )
                
                # Get valid access token
                auth_service = QboAuthService()
                qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
                
                if not qbo_auth or not qbo_auth.access_token:
                    raise ValueError(f"No valid access token found for realm_id: {realm_id}")
                
                # Fetch current QBO CompanyInfo to get SyncToken
                with QboCompanyInfoClient(
                    access_token=qbo_auth.access_token,
                    realm_id=realm_id
                ) as client:
                    qbo_external = client.get_company_info()
                    
                    # Update fields
                    qbo_external.company_name = company.name
                    if qbo_external.web_addr:
                        qbo_external.web_addr.uri = company.website
                    else:
                        from integrations.intuit.qbo.company_info.external.schemas import QboWebAddr
                        qbo_external.web_addr = QboWebAddr(uri=company.website)
                    
                    # Attempt to update via QBO API
                    try:
                        updated_external = client.update_company_info(qbo_external)
                        logger.info("Successfully updated QboCompanyInfo via QBO API")
                    except Exception as e:
                        logger.warning(
                            f"Failed to update QboCompanyInfo via QBO API: {e}. "
                            "QBO CompanyInfo API may have limited update capabilities. "
                            "Updating local database record only."
                        )
                        # Update local database record
                        updated_external = qbo_external
                
                # Update local database record
                # Company.Name maps to CompanyInfo.LegalName
                qbo_company_info = qbo_company_info_repo.update_by_qbo_id(
                    qbo_id=qbo_company_info.qbo_id,
                    row_version=qbo_company_info.row_version_bytes,
                    sync_token=updated_external.sync_token if hasattr(updated_external, 'sync_token') else qbo_company_info.sync_token,
                    realm_id=realm_id,
                    company_name=qbo_company_info.company_name,  # Keep existing company_name
                    legal_name=company.name,
                    company_addr_id=qbo_company_info.company_addr_id,
                    legal_addr_id=qbo_company_info.legal_addr_id,
                    customer_communication_addr_id=qbo_company_info.customer_communication_addr_id,
                    tax_payer_id=qbo_company_info.tax_payer_id,
                    fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
                    country=qbo_company_info.country,
                    email=qbo_company_info.email,
                    web_addr=company.website,
                    currency_ref=qbo_company_info.currency_ref,
                )
                
            elif qbo_modified > company_modified:
                # QBO is newer - log conflict
                logger.warning(
                    f"Conflict detected: QBO is newer (QBO: {qbo_modified}, Company: {company.modified_datetime}). "
                    f"QboCompanyInfo {qbo_company_info.id} not updated. Consider syncing from QBO to Company."
                )
            else:
                # Same timestamp - update QBO anyway to ensure consistency
                logger.debug("ModifiedDatetime matches - updating QboCompanyInfo for consistency")
                # Update QBO with Company data (same logic as above)
                auth_service = QboAuthService()
                qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
                
                if qbo_auth and qbo_auth.access_token:
                    with QboCompanyInfoClient(
                        access_token=qbo_auth.access_token,
                        realm_id=realm_id
                    ) as client:
                        qbo_external = client.get_company_info()
                        qbo_external.company_name = company.name
                        if qbo_external.web_addr:
                            qbo_external.web_addr.uri = company.website
                        else:
                            from integrations.intuit.qbo.company_info.external.schemas import QboWebAddr
                            qbo_external.web_addr = QboWebAddr(uri=company.website)
                        
                        try:
                            updated_external = client.update_company_info(qbo_external)
                        except Exception:
                            updated_external = qbo_external
                        
                        # Company.Name maps to CompanyInfo.LegalName
                        qbo_company_info = qbo_company_info_repo.update_by_qbo_id(
                            qbo_id=qbo_company_info.qbo_id,
                            row_version=qbo_company_info.row_version_bytes,
                            sync_token=updated_external.sync_token if hasattr(updated_external, 'sync_token') else qbo_company_info.sync_token,
                            realm_id=realm_id,
                            company_name=qbo_company_info.company_name,  # Keep existing
                            legal_name=company.name,
                            company_addr_id=qbo_company_info.company_addr_id,
                            legal_addr_id=qbo_company_info.legal_addr_id,
                            customer_communication_addr_id=qbo_company_info.customer_communication_addr_id,
                            tax_payer_id=qbo_company_info.tax_payer_id,
                            fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
                            country=qbo_company_info.country,
                            email=qbo_company_info.email,
                            web_addr=company.website,
                            currency_ref=qbo_company_info.currency_ref,
                        )
        else:
            # One or both timestamps missing - update QBO
            logger.debug("Missing ModifiedDatetime - updating QboCompanyInfo")
            # Update QBO with Company data (same logic as above)
            auth_service = QboAuthService()
            qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
            
            if qbo_auth and qbo_auth.access_token:
                with QboCompanyInfoClient(
                    access_token=qbo_auth.access_token,
                    realm_id=realm_id
                ) as client:
                    qbo_external = client.get_company_info()
                    qbo_external.company_name = company.name
                    if qbo_external.web_addr:
                        qbo_external.web_addr.uri = company.website
                    else:
                        from integrations.intuit.qbo.company_info.external.schemas import QboWebAddr
                        qbo_external.web_addr = QboWebAddr(uri=company.website)
                    
                    try:
                        updated_external = client.update_company_info(qbo_external)
                    except Exception:
                        updated_external = qbo_external
                    
                    # Company.Name maps to CompanyInfo.LegalName
                    qbo_company_info = qbo_company_info_repo.update_by_qbo_id(
                        qbo_id=qbo_company_info.qbo_id,
                        row_version=qbo_company_info.row_version_bytes,
                        sync_token=updated_external.sync_token if hasattr(updated_external, 'sync_token') else qbo_company_info.sync_token,
                        realm_id=realm_id,
                        company_name=qbo_company_info.company_name,  # Keep existing
                        legal_name=company.name,
                        company_addr_id=qbo_company_info.company_addr_id,
                        legal_addr_id=qbo_company_info.legal_addr_id,
                        customer_communication_addr_id=qbo_company_info.customer_communication_addr_id,
                        tax_payer_id=qbo_company_info.tax_payer_id,
                        fiscal_year_start_month=qbo_company_info.fiscal_year_start_month,
                        country=qbo_company_info.country,
                        email=qbo_company_info.email,
                        web_addr=company.website,
                        currency_ref=qbo_company_info.currency_ref,
                    )
        
        return qbo_company_info

    def create_mapping(self, company_id: int, qbo_company_info_id: int) -> CompanyInfoCompany:
        """
        Create a mapping between Company and QboCompanyInfo.
        
        Args:
            company_id: Database ID of Company record
            qbo_company_info_id: Database ID of QboCompanyInfo record
        
        Returns:
            CompanyInfoCompany: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_company = self.mapping_repo.read_by_company_id(company_id)
        if existing_by_company:
            raise ValueError(
                f"Company {company_id} is already mapped to QboCompanyInfo {existing_by_company.qbo_company_info_id}"
            )
        
        existing_by_qbo = self.mapping_repo.read_by_qbo_company_info_id(qbo_company_info_id)
        if existing_by_qbo:
            raise ValueError(
                f"QboCompanyInfo {qbo_company_info_id} is already mapped to Company {existing_by_qbo.company_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(company_id=company_id, qbo_company_info_id=qbo_company_info_id)

    def get_mapping_by_company_id(self, company_id: int) -> Optional[CompanyInfoCompany]:
        """
        Get mapping by Company ID.
        
        Args:
            company_id: Database ID of Company record
        
        Returns:
            CompanyInfoCompany: The mapping record, or None if not found
        """
        return self.mapping_repo.read_by_company_id(company_id)

    def get_mapping_by_qbo_company_info_id(self, qbo_company_info_id: int) -> Optional[CompanyInfoCompany]:
        """
        Get mapping by QboCompanyInfo ID.
        
        Args:
            qbo_company_info_id: Database ID of QboCompanyInfo record
        
        Returns:
            CompanyInfoCompany: The mapping record, or None if not found
        """
        return self.mapping_repo.read_by_qbo_company_info_id(qbo_company_info_id)

    @staticmethod
    def _parse_datetime(datetime_str: Optional[str]) -> Optional[datetime]:
        """
        Parse datetime string to datetime object.
        
        Args:
            datetime_str: ISO format datetime string (e.g., "2025-01-06T23:17:40" or "2025-01-06 23:17:40")
        
        Returns:
            datetime: Parsed datetime object, or None if parsing fails
        """
        if not datetime_str:
            return None
        
        try:
            # Handle ISO format - remove timezone info if present
            dt_str = datetime_str.replace('Z', '').replace('+00:00', '')
            if '+' in dt_str:
                dt_str = dt_str.split('+')[0]
            if '-' in dt_str and dt_str.count('-') > 2:  # Has timezone offset
                # Format: "2025-01-06T23:17:40-08:00"
                parts = dt_str.rsplit('-', 2)
                dt_str = parts[0]  # Take everything before the timezone
            
            # Try parsing with space separator (SQL Server format)
            if ' ' in dt_str and 'T' not in dt_str:
                return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            # Try parsing with T separator (ISO format)
            elif 'T' in dt_str:
                dt_str = dt_str.replace('T', ' ')
                if '.' in dt_str:
                    # Has milliseconds
                    return datetime.strptime(dt_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                else:
                    return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            else:
                # Simple date format
                return datetime.strptime(dt_str, '%Y-%m-%d')
        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

