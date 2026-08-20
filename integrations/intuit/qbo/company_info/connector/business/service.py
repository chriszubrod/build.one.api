# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.drift_types import DRIFT_COMPANY_IDENTITY_CONFLICT
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.company_info.connector.business.model import CompanyInfoCompany
from integrations.intuit.qbo.company_info.connector.persistence.repo import CompanyInfoCompanyRepository
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo as QboCompanyInfoModel
from integrations.intuit.qbo.company_info.external.client import QboCompanyInfoClient
from integrations.intuit.qbo.company_info.external.schemas import QboCompanyInfo as QboCompanyInfoExternalSchema
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.company.business.service import CompanyService
from entities.company.business.model import Company

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
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the CompanyInfoCompanyConnector."""
        self.mapping_repo = mapping_repo or CompanyInfoCompanyRepository()
        self.company_service = company_service or CompanyService()
        self.qbo_company_info_service = qbo_company_info_service or QboCompanyInfoService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

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

        # U-277 (Phase-4): resolve identity directly against dbo.Company's
        # native QboId/RealmId (U-238a) before falling back to the
        # qbo.CompanyInfoCompany mapping-table hop below. Every Company synced
        # even once already carries this identity (set_qbo_identity is called
        # on both the update and create paths below), so this covers the
        # steady-state case without touching qbo.CompanyInfo at all.
        #
        # Mirrors CustomerCustomerConnector's U-276 fast path exactly: the
        # mapping-table state is checked BEFORE any write, not after — writing
        # to the dbo-identity-matched Company first and detecting a conflict
        # afterward would corrupt that Company's Name/Website in the case
        # where the mapping table, not dbo identity, is actually still the
        # correct side. On a detected conflict we record it and deliberately
        # do NOT return here; falling through to the pre-existing
        # mapping-table path below is the safe choice.
        protected_company_id = None
        direct = (
            self.company_service.read_by_qbo_identity(
                qbo_company_info.qbo_id, qbo_company_info.realm_id or realm_id
            )
            if qbo_company_info.qbo_id else None
        )
        if direct:
            mapping_state, by_company, by_qbo_company_info = self._resolve_mapping_state(
                company_id=coerce_id(direct.id), qbo_company_info=qbo_company_info
            )
            if mapping_state == "conflict":
                self._raise_identity_mapping_conflict_issue(
                    qbo_company_info=qbo_company_info,
                    dbo_company_id=coerce_id(direct.id),
                    local_side_mapping=by_company,
                    qbo_side_mapping=by_qbo_company_info,
                    realm_id=realm_id,
                )
                # Fall through — do NOT write to `direct` while the two
                # identity sources disagree about which Company this is.
                # `protected_company_id` guards Step 3 below: the legacy
                # path's own by-name rediscovery can re-find this exact
                # Company (company_name was derived from this same
                # QboCompanyInfo) and would otherwise overwrite it anyway —
                # the write-before-check bug this whole pattern exists to
                # prevent, just reached one hop later (review-confirmed).
                protected_company_id = coerce_id(direct.id)
            else:
                logger.info(
                    f"Updating existing Company {direct.id} from QboCompanyInfo {qbo_company_info.id} "
                    f"(direct dbo identity match)"
                )
                direct.name = company_name
                direct.website = company_website
                company = self.company_service.repo.update_by_id(direct)
                if not company:
                    # ROWVERSION race: a concurrent writer touched this exact
                    # Company between the read_by_qbo_identity() lookup above
                    # and this UPDATE, so it affected 0 rows. Mirrors the
                    # adjacent legacy path's own guard (Step 3 below) instead
                    # of leaving a bare None to blow up on the next attribute
                    # access.
                    logger.error(
                        f"Failed to update Company {direct.id} via fast path - "
                        f"update_by_id returned None (concurrent write race)"
                    )
                    raise ValueError("Failed to update Company")
                if mapping_state == "missing":
                    try:
                        self.mapping_repo.create(
                            company_id=coerce_id(company.id), qbo_company_info_id=qbo_company_info.id
                        )
                    except Exception as e:
                        # A concurrent sync may have raced this exact QboCompanyInfo between
                        # the "missing" check above and this create — no sp_getapplock
                        # serializes create_mapping()'s call sites (same pre-existing gap as
                        # U-276's Customer/Project family). Re-check rather than assume: if
                        # it's now a real conflict, record it properly instead of a bare warning.
                        logger.error(
                            f"CompanyInfoCompany mapping create failed for Company "
                            f"{company.id} after a 'missing' pre-check: {e}"
                        )
                        recheck_state, recheck_by_company, recheck_by_qbo_company_info = (
                            self._resolve_mapping_state(
                                company_id=coerce_id(company.id), qbo_company_info=qbo_company_info
                            )
                        )
                        if recheck_state == "conflict":
                            self._raise_identity_mapping_conflict_issue(
                                qbo_company_info=qbo_company_info,
                                dbo_company_id=coerce_id(company.id),
                                local_side_mapping=recheck_by_company,
                                qbo_side_mapping=recheck_by_qbo_company_info,
                                realm_id=realm_id,
                            )
                return company

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
                    coerce_id(existing_company.id)
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
        if company and protected_company_id is not None and coerce_id(company.id) == protected_company_id:
            # The legacy path (Step 1's mapping lookup or Step 2's by-name
            # rediscovery) resolved back to the SAME Company the fast path
            # just flagged as identity-conflicted above. Writing to it here
            # would silently defeat that guard — return it untouched; the
            # reconciliation issue already recorded is the actionable trail.
            logger.warning(
                f"Legacy fallback re-resolved Company {company.id} to the same row the fast-path "
                f"conflict guard just protected — skipping write."
            )
            return company

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
            company_id_int = coerce_id(company.id)
            
            # Delete old broken mapping if it exists
            if mapping and mapping.company_id != company_id_int:
                logger.info(f"Deleting broken mapping (old Company ID: {mapping.company_id})")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
            
            # Create new mapping if needed
            if not mapping:
                try:
                    mapping = self.create_mapping(
                        company_id_int,
                        qbo_company_info_id,
                        qbo_id=qbo_company_info.qbo_id,
                        realm_id=qbo_company_info.realm_id or realm_id,
                    )
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

                # Fetch current QBO CompanyInfo to get SyncToken
                with QboCompanyInfoClient(realm_id=realm_id) as client:
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
                with QboCompanyInfoClient(realm_id=realm_id) as client:
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
            with QboCompanyInfoClient(realm_id=realm_id) as client:
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

    def _resolve_mapping_state(self, *, company_id: int, qbo_company_info: QboCompanyInfoModel):
        """
        Read-only check of the CompanyInfoCompany mapping table against a
        dbo-identity match, BEFORE any write happens (U-277 fast path).
        Mirrors CustomerCustomerConnector._resolve_mapping_state exactly — see
        that docstring for the full rationale. Checks BOTH directions since a
        company_id-only check would miss a stale mapping still binding this
        qbo_company_info_id to a DIFFERENT Company.

        Returns (state, by_company, by_qbo_company_info) where state is one of
        "consistent" / "missing" / "conflict".
        """
        by_company = self.mapping_repo.read_by_company_id(company_id)
        if by_company and by_company.qbo_company_info_id == qbo_company_info.id:
            return "consistent", by_company, by_company
        by_qbo_company_info = self.mapping_repo.read_by_qbo_company_info_id(qbo_company_info.id)
        if not by_company and not by_qbo_company_info:
            return "missing", by_company, by_qbo_company_info
        return "conflict", by_company, by_qbo_company_info

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_company_info: QboCompanyInfoModel,
        dbo_company_id: int,
        local_side_mapping: Optional[CompanyInfoCompany],
        qbo_side_mapping: Optional[CompanyInfoCompany],
        realm_id: str,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        _resolve_mapping_state. Mirrors CustomerCustomerConnector's identically
        named/shaped method — covers all three conflict shapes (qbo-side only,
        local-side only, or both) in ONE issue, never silently dropping either
        side's blocker.
        """
        parts = [
            f"CompanyInfoCompany identity conflict. dbo.Company {dbo_company_id} carries native "
            f"QBO identity for QboCompanyInfo {qbo_company_info.id} (QboId={qbo_company_info.qbo_id}, "
            f"RealmId={qbo_company_info.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboCompanyInfo to a DIFFERENT "
                f"Company {qbo_side_mapping.company_id} (mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: Company {dbo_company_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboCompanyInfo "
                f"{local_side_mapping.qbo_company_info_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="company_identity_conflict",
            entity_type="Company",
            entity_public_id=None,
            qbo_id=str(qbo_company_info.qbo_id) if qbo_company_info.qbo_id else None,
            realm_id=qbo_company_info.realm_id or realm_id or "",
            details=" ".join(parts),
        )

    def create_mapping(
        self,
        company_id: int,
        qbo_company_info_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> CompanyInfoCompany:
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
        
        # Stamp dbo-native identity FIRST — if this fails, nothing else has been
        # created yet, so the caller's existing rollback (delete the just-created
        # entity) fully cleans up with no orphaned mapping row.
        self.company_service.repo.set_qbo_identity(
            id=company_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
        mapping = self.mapping_repo.create(company_id=company_id, qbo_company_info_id=qbo_company_info_id)
        return mapping

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

