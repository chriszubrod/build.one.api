# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.site.connector.company.business.model import SiteCompany
from integrations.ms.sharepoint.site.connector.company.persistence.repo import SiteCompanyRepository
from integrations.ms.sharepoint.site.business.model import MsSite
from integrations.ms.sharepoint.site.business.service import MsSiteService

logger = logging.getLogger(__name__)


class SiteCompanyConnector:
    """
    Connector service for linking SharePoint Sites to Companies.
    Handles the mapping between ms.Site and dbo.Company.
    """

    def __init__(
        self,
        mapping_repo: Optional[SiteCompanyRepository] = None,
        site_service: Optional[MsSiteService] = None,
    ):
        """Initialize the SiteCompanyConnector."""
        self.mapping_repo = mapping_repo or SiteCompanyRepository()
        self.site_service = site_service or MsSiteService()

    def link_site_to_company(self, company_id: int, ms_graph_site_id: str) -> dict:
        """
        Link a SharePoint site to a Company.
        
        This method:
        1. Links the site from MS Graph (creates ms.Site record if needed)
        2. Creates the mapping between Company and Site
        
        Args:
            company_id: Database ID of Company record
            ms_graph_site_id: MS Graph site ID to link
        
        Returns:
            Dict with status_code, message, and mapping/site data
        """
        # Check if company already has a linked site
        existing_mapping = self.mapping_repo.read_by_company_id(company_id)
        if existing_mapping:
            # Get the existing site details
            existing_site = self.site_service.read_by_id(existing_mapping.site_id)
            return {
                "message": "Company already has a linked SharePoint site",
                "status_code": 400,
                "mapping": existing_mapping.to_dict() if existing_mapping else None,
                "site": existing_site.to_dict() if existing_site else None
            }
        
        # Link the site from MS Graph (this creates ms.Site record if needed)
        link_result = self.site_service.link_site(site_id=ms_graph_site_id)
        
        if link_result.get("status_code") >= 400:
            return {
                "message": link_result.get("message", "Failed to link site from MS Graph"),
                "status_code": link_result.get("status_code", 500),
                "mapping": None,
                "site": None
            }
        
        site_data = link_result.get("site")
        if not site_data:
            return {
                "message": "No site data returned from link operation",
                "status_code": 500,
                "mapping": None,
                "site": None
            }
        
        site_id = site_data.get("id")
        if not site_id:
            return {
                "message": "Site ID not found in link result",
                "status_code": 500,
                "mapping": None,
                "site": None
            }
        
        # Check if this site is already linked to another company
        existing_site_mapping = self.mapping_repo.read_by_site_id(site_id)
        if existing_site_mapping:
            return {
                "message": f"Site is already linked to company ID {existing_site_mapping.company_id}",
                "status_code": 400,
                "mapping": existing_site_mapping.to_dict(),
                "site": site_data
            }
        
        # Create the mapping
        try:
            mapping = self.mapping_repo.create(company_id=company_id, site_id=site_id)
            logger.info(f"Created mapping: Company {company_id} <-> Site {site_id}")
            
            return {
                "message": "Site linked to company successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "site": site_data
            }
        except Exception as e:
            logger.exception("Error creating site-company mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None,
                "site": site_data
            }

    def get_mapping_by_company_id(self, company_id: int) -> Optional[SiteCompany]:
        """
        Get mapping by Company ID.
        """
        return self.mapping_repo.read_by_company_id(company_id)

    def get_mapping_by_site_id(self, site_id: int) -> Optional[SiteCompany]:
        """
        Get mapping by Site ID.
        """
        return self.mapping_repo.read_by_site_id(site_id)

    def get_site_for_company(self, company_id: int) -> Optional[MsSite]:
        """
        Get the linked SharePoint site for a company.
        
        Args:
            company_id: Database ID of Company record
        
        Returns:
            MsSite if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_company_id(company_id)
        if not mapping:
            return None
        
        return self.site_service.read_by_id(mapping.site_id)

    def unlink_by_company_id(self, company_id: int) -> dict:
        """
        Unlink the SharePoint site from a company.
        
        Args:
            company_id: Database ID of Company record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_company_id(company_id)
        if not mapping:
            return {
                "message": "No linked site found for this company",
                "status_code": 404,
                "mapping": None
            }
        
        try:
            deleted = self.mapping_repo.delete_by_company_id(company_id)
            logger.info(f"Unlinked site from company {company_id}")
            
            return {
                "message": "Site unlinked from company successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking site from company")
            return {
                "message": f"Error unlinking site: {str(e)}",
                "status_code": 500,
                "mapping": None
            }
