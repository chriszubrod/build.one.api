# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.drive.connector.company.business.model import DriveCompany
from integrations.ms.sharepoint.drive.connector.company.persistence.repo import DriveCompanyRepository
from integrations.ms.sharepoint.site.business.service import MsSiteService
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository

logger = logging.getLogger(__name__)


class DriveCompanyConnector:
    """
    Connector service for linking Drives to Companies.
    Ensures proper hierarchy: Site -> Drive -> DriveCompany mapping.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveCompanyRepository] = None,
        site_service: Optional[MsSiteService] = None,
        drive_service: Optional[MsDriveService] = None,
        drive_repo: Optional[MsDriveRepository] = None,
    ):
        """Initialize the DriveCompanyConnector."""
        self.mapping_repo = mapping_repo or DriveCompanyRepository()
        self.site_service = site_service or MsSiteService()
        self.drive_service = drive_service or MsDriveService()
        self.drive_repo = drive_repo or MsDriveRepository()

    def link_drive_to_company(self, company_id: int, graph_site_id: str, graph_drive_id: str) -> dict:
        """
        Link a Drive to a Company.
        
        This method:
        1. Creates/finds the Site in ms.Site
        2. Creates/finds the Drive in ms.Drive
        3. Creates the DriveCompany mapping
        
        Args:
            company_id: Database ID of Company record
            graph_site_id: MS Graph site ID (the site the drive belongs to)
            graph_drive_id: MS Graph drive ID to link
        
        Returns:
            Dict with status_code, message, and mapping data
        """
        # Check if company already has a linked drive
        existing_mapping = self.mapping_repo.read_by_company_id(company_id)
        if existing_mapping:
            return {
                "message": "Company already has a linked drive",
                "status_code": 400,
                "mapping": existing_mapping.to_dict()
            }
        
        # Step 1: Ensure the Site is stored
        logger.info(f"Linking site {graph_site_id} to database...")
        site_result = self.site_service.link_site(site_id=graph_site_id)
        
        if site_result.get("status_code") not in [200, 201]:
            return {
                "message": f"Failed to link site: {site_result.get('message')}",
                "status_code": site_result.get("status_code", 500),
                "mapping": None
            }
        
        site = site_result.get("site")
        if not site:
            return {
                "message": "Site was linked but no site data returned",
                "status_code": 500,
                "mapping": None
            }
        
        site_public_id = site.get("public_id") if isinstance(site, dict) else site.public_id
        
        # Step 2: Ensure the Drive is stored
        logger.info(f"Linking drive {graph_drive_id} to site {site_public_id}...")
        drive_result = self.drive_service.link_drive(
            site_public_id=site_public_id,
            drive_id=graph_drive_id
        )
        
        if drive_result.get("status_code") not in [200, 201]:
            return {
                "message": f"Failed to link drive: {drive_result.get('message')}",
                "status_code": drive_result.get("status_code", 500),
                "mapping": None
            }
        
        drive = drive_result.get("drive")
        if not drive:
            return {
                "message": "Drive was linked but no drive data returned",
                "status_code": 500,
                "mapping": None
            }
        
        ms_drive_id = drive.get("id") if isinstance(drive, dict) else drive.id
        
        # Check if this drive is already linked to another company
        existing_drive_mapping = self.mapping_repo.read_by_ms_drive_id(ms_drive_id)
        if existing_drive_mapping:
            return {
                "message": f"Drive is already linked to company ID {existing_drive_mapping.company_id}",
                "status_code": 400,
                "mapping": existing_drive_mapping.to_dict()
            }
        
        # Step 3: Create the DriveCompany mapping
        try:
            mapping = self.mapping_repo.create(
                company_id=company_id,
                ms_drive_id=ms_drive_id,
            )
            logger.info(f"Created mapping: Company {company_id} <-> Drive {ms_drive_id}")
            
            return {
                "message": "Drive linked to company successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "site": site if isinstance(site, dict) else site.to_dict(),
                "drive": drive if isinstance(drive, dict) else drive.to_dict(),
            }
        except Exception as e:
            logger.exception("Error creating drive-company mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def get_mapping_by_company_id(self, company_id: int) -> Optional[DriveCompany]:
        """
        Get mapping by Company ID.
        """
        return self.mapping_repo.read_by_company_id(company_id)

    def get_mapping_by_ms_drive_id(self, ms_drive_id: int) -> Optional[DriveCompany]:
        """
        Get mapping by MS Drive ID.
        """
        return self.mapping_repo.read_by_ms_drive_id(ms_drive_id)

    def get_drive_for_company(self, company_id: int) -> Optional[dict]:
        """
        Get the linked drive details for a company.
        
        Args:
            company_id: Database ID of Company record
        
        Returns:
            Dict with drive details if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_company_id(company_id)
        if not mapping:
            return None
        
        # Get the drive details from ms.Drive
        drive = self.drive_repo.read_by_id(mapping.ms_drive_id)
        if not drive:
            return None
        
        return drive.to_dict()

    def unlink_by_company_id(self, company_id: int) -> dict:
        """
        Unlink the drive from a company.
        Note: This only removes the mapping, not the Site or Drive records.
        
        Args:
            company_id: Database ID of Company record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_company_id(company_id)
        if not mapping:
            return {
                "message": "No linked drive found for this company",
                "status_code": 404,
                "mapping": None
            }
        
        try:
            deleted = self.mapping_repo.delete_by_company_id(company_id)
            logger.info(f"Unlinked drive from company {company_id}")
            
            return {
                "message": "Drive unlinked from company successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking drive from company")
            return {
                "message": f"Error unlinking drive: {str(e)}",
                "status_code": 500,
                "mapping": None
            }
