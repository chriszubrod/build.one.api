# Python Standard Library Imports
from typing import Optional
import logging

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.site.business.model import MsSite
from integrations.ms.sharepoint.site.persistence.repo import MsSiteRepository
from integrations.ms.sharepoint.external.client import (
    search_sites as graph_search_sites,
    get_site_by_id as graph_get_site_by_id,
    get_site_by_path as graph_get_site_by_path,
)

logger = logging.getLogger(__name__)


class MsSiteService:
    """
    Service for MsSite entity business operations.
    Combines persistence with MS Graph API calls.
    """

    def __init__(self, repo: Optional[MsSiteRepository] = None):
        """Initialize the MsSiteService."""
        self.repo = repo or MsSiteRepository()

    def search_sites(self, query: str) -> dict:
        """
        Search for SharePoint sites via MS Graph API.
        Does not store results - just returns search results.
        
        Args:
            query: Search query string
        
        Returns:
            Dict with status_code, message, and sites list
        """
        return graph_search_sites(query)

    def get_site_by_path(self, hostname: str, site_path: str) -> dict:
        """
        Get a SharePoint site by hostname and path via MS Graph API.
        Does not store result - just returns site info.
        
        Args:
            hostname: The SharePoint hostname
            site_path: The site path
        
        Returns:
            Dict with status_code, message, and site data
        """
        return graph_get_site_by_path(hostname, site_path)

    def link_site(self, site_id: str) -> dict:
        """
        Link a SharePoint site by fetching from MS Graph and storing locally.
        
        Args:
            site_id: The MS Graph site ID to link
        
        Returns:
            Dict with status_code, message, and linked site
        """
        # Check if already linked
        existing = self.repo.read_by_site_id(site_id)
        if existing:
            return {
                "message": "Site is already linked",
                "status_code": 200,
                "site": existing.to_dict()
            }
        
        # Fetch site from MS Graph
        graph_result = graph_get_site_by_id(site_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch site from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "site": None
            }
        
        site_data = graph_result.get("site")
        if not site_data:
            return {
                "message": "No site data returned from MS Graph",
                "status_code": 500,
                "site": None
            }
        
        # Store in database
        try:
            ms_site = self.repo.create(
                site_id=site_data.get("site_id"),
                display_name=site_data.get("display_name"),
                web_url=site_data.get("web_url"),
                hostname=site_data.get("hostname"),
            )
            
            return {
                "message": "Site linked successfully",
                "status_code": 201,
                "site": ms_site.to_dict()
            }
        except Exception as e:
            logger.exception("Error linking site")
            return {
                "message": f"Error linking site: {str(e)}",
                "status_code": 500,
                "site": None
            }

    def read_all(self) -> list[MsSite]:
        """
        Read all linked MsSites from the database.
        """
        return self.repo.read_all()

    def read_by_public_id(self, public_id: str) -> Optional[MsSite]:
        """
        Read a linked MsSite by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_site_id(self, site_id: str) -> Optional[MsSite]:
        """
        Read a linked MsSite by MS Graph site ID.
        """
        return self.repo.read_by_site_id(site_id)

    def update_by_public_id(
        self, *, public_id: str, display_name: str
    ) -> dict:
        """
        Update a linked site's display name.
        Fetches fresh data from MS Graph and updates local record.
        
        Args:
            public_id: The public ID of the linked site
            display_name: New display name (optional override)
        
        Returns:
            Dict with status_code, message, and updated site
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "site": None
            }
        
        try:
            updated = self.repo.update_by_public_id(
                public_id=public_id,
                site_id=existing.site_id,
                display_name=display_name,
                web_url=existing.web_url,
                hostname=existing.hostname,
            )
            
            if updated:
                return {
                    "message": "Site updated successfully",
                    "status_code": 200,
                    "site": updated.to_dict()
                }
            else:
                return {
                    "message": "Failed to update site",
                    "status_code": 500,
                    "site": None
                }
        except Exception as e:
            logger.exception("Error updating site")
            return {
                "message": f"Error updating site: {str(e)}",
                "status_code": 500,
                "site": None
            }

    def unlink_site(self, public_id: str) -> dict:
        """
        Unlink a SharePoint site by removing it from the database.
        
        Args:
            public_id: The public ID of the linked site to remove
        
        Returns:
            Dict with status_code, message, and deleted site
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "site": None
            }
        
        try:
            deleted = self.repo.delete_by_public_id(public_id)
            
            if deleted:
                return {
                    "message": "Site unlinked successfully",
                    "status_code": 200,
                    "site": deleted.to_dict()
                }
            else:
                return {
                    "message": "Failed to unlink site",
                    "status_code": 500,
                    "site": None
                }
        except Exception as e:
            logger.exception("Error unlinking site")
            return {
                "message": f"Error unlinking site: {str(e)}",
                "status_code": 500,
                "site": None
            }

    def refresh_site(self, public_id: str) -> dict:
        """
        Refresh a linked site by fetching latest data from MS Graph.
        
        Args:
            public_id: The public ID of the linked site to refresh
        
        Returns:
            Dict with status_code, message, and refreshed site
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "site": None
            }
        
        # Fetch fresh data from MS Graph
        graph_result = graph_get_site_by_id(existing.site_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch site from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "site": None
            }
        
        site_data = graph_result.get("site")
        if not site_data:
            return {
                "message": "No site data returned from MS Graph",
                "status_code": 500,
                "site": None
            }
        
        # Update local record
        try:
            updated = self.repo.update_by_public_id(
                public_id=public_id,
                site_id=site_data.get("site_id"),
                display_name=site_data.get("display_name"),
                web_url=site_data.get("web_url"),
                hostname=site_data.get("hostname"),
            )
            
            if updated:
                return {
                    "message": "Site refreshed successfully",
                    "status_code": 200,
                    "site": updated.to_dict()
                }
            else:
                return {
                    "message": "Failed to refresh site",
                    "status_code": 500,
                    "site": None
                }
        except Exception as e:
            logger.exception("Error refreshing site")
            return {
                "message": f"Error refreshing site: {str(e)}",
                "status_code": 500,
                "site": None
            }
