# Python Standard Library Imports
from typing import Optional
import logging

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.drive.business.model import MsDrive
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from integrations.ms.sharepoint.site.persistence.repo import MsSiteRepository
from integrations.ms.sharepoint.external.client import (
    list_site_drives as graph_list_site_drives,
    get_drive_by_id as graph_get_drive_by_id,
)

logger = logging.getLogger(__name__)


class MsDriveService:
    """
    Service for MsDrive entity business operations.
    Combines persistence with MS Graph API calls.
    """

    def __init__(
        self,
        repo: Optional[MsDriveRepository] = None,
        site_repo: Optional[MsSiteRepository] = None
    ):
        """Initialize the MsDriveService."""
        self.repo = repo or MsDriveRepository()
        self.site_repo = site_repo or MsSiteRepository()

    def list_available_drives(self, site_public_id: str) -> dict:
        """
        List available drives from MS Graph for a linked site.
        Does not store results - just returns available drives.
        
        Args:
            site_public_id: The public ID of the linked site
        
        Returns:
            Dict with status_code, message, and drives list
        """
        # Get the linked site
        site = self.site_repo.read_by_public_id(site_public_id)
        if not site:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "drives": []
            }
        
        # Fetch drives from MS Graph
        return graph_list_site_drives(site.site_id)

    def link_drive(self, site_public_id: str, drive_id: str) -> dict:
        """
        Link a drive by fetching from MS Graph and storing locally.
        
        Args:
            site_public_id: The public ID of the linked site
            drive_id: The MS Graph drive ID to link
        
        Returns:
            Dict with status_code, message, and linked drive
        """
        # Get the linked site
        site = self.site_repo.read_by_public_id(site_public_id)
        if not site:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "drive": None
            }
        
        # Check if already linked
        existing = self.repo.read_by_drive_id(drive_id)
        if existing:
            return {
                "message": "Drive is already linked",
                "status_code": 200,
                "drive": existing.to_dict()
            }
        
        # Fetch drive from MS Graph
        graph_result = graph_get_drive_by_id(drive_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch drive from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "drive": None
            }
        
        drive_data = graph_result.get("drive")
        if not drive_data:
            return {
                "message": "No drive data returned from MS Graph",
                "status_code": 500,
                "drive": None
            }
        
        # Store in database
        try:
            ms_drive = self.repo.create(
                ms_site_id=site.id,
                drive_id=drive_data.get("drive_id"),
                name=drive_data.get("name"),
                web_url=drive_data.get("web_url"),
                drive_type=drive_data.get("drive_type"),
            )
            
            return {
                "message": "Drive linked successfully",
                "status_code": 201,
                "drive": ms_drive.to_dict()
            }
        except Exception as e:
            logger.exception("Error linking drive")
            return {
                "message": f"Error linking drive: {str(e)}",
                "status_code": 500,
                "drive": None
            }

    def read_all(self) -> list[MsDrive]:
        """
        Read all linked MsDrives from the database.
        """
        return self.repo.read_all()

    def read_by_site_public_id(self, site_public_id: str) -> dict:
        """
        Read all linked MsDrives for a specific site.
        
        Args:
            site_public_id: The public ID of the linked site
        
        Returns:
            Dict with status_code, message, and drives list
        """
        # Get the linked site
        site = self.site_repo.read_by_public_id(site_public_id)
        if not site:
            return {
                "message": "Linked site not found",
                "status_code": 404,
                "drives": []
            }
        
        drives = self.repo.read_by_ms_site_id(site.id)
        return {
            "message": f"Found {len(drives)} linked drives",
            "status_code": 200,
            "drives": [drive.to_dict() for drive in drives]
        }

    def read_by_public_id(self, public_id: str) -> Optional[MsDrive]:
        """
        Read a linked MsDrive by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_drive_id(self, drive_id: str) -> Optional[MsDrive]:
        """
        Read a linked MsDrive by MS Graph drive ID.
        """
        return self.repo.read_by_drive_id(drive_id)

    def update_by_public_id(self, *, public_id: str, name: str) -> dict:
        """
        Update a linked drive's name.
        
        Args:
            public_id: The public ID of the linked drive
            name: New name
        
        Returns:
            Dict with status_code, message, and updated drive
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "drive": None
            }
        
        try:
            updated = self.repo.update_by_public_id(
                public_id=public_id,
                ms_site_id=existing.ms_site_id,
                drive_id=existing.drive_id,
                name=name,
                web_url=existing.web_url,
                drive_type=existing.drive_type,
            )
            
            if updated:
                return {
                    "message": "Drive updated successfully",
                    "status_code": 200,
                    "drive": updated.to_dict()
                }
            else:
                return {
                    "message": "Failed to update drive",
                    "status_code": 500,
                    "drive": None
                }
        except Exception as e:
            logger.exception("Error updating drive")
            return {
                "message": f"Error updating drive: {str(e)}",
                "status_code": 500,
                "drive": None
            }

    def unlink_drive(self, public_id: str) -> dict:
        """
        Unlink a drive by removing it from the database.
        
        Args:
            public_id: The public ID of the linked drive to remove
        
        Returns:
            Dict with status_code, message, and deleted drive
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "drive": None
            }
        
        try:
            deleted = self.repo.delete_by_public_id(public_id)
            
            if deleted:
                return {
                    "message": "Drive unlinked successfully",
                    "status_code": 200,
                    "drive": deleted.to_dict()
                }
            else:
                return {
                    "message": "Failed to unlink drive",
                    "status_code": 500,
                    "drive": None
                }
        except Exception as e:
            logger.exception("Error unlinking drive")
            return {
                "message": f"Error unlinking drive: {str(e)}",
                "status_code": 500,
                "drive": None
            }

    def refresh_drive(self, public_id: str) -> dict:
        """
        Refresh a linked drive by fetching latest data from MS Graph.
        
        Args:
            public_id: The public ID of the linked drive to refresh
        
        Returns:
            Dict with status_code, message, and refreshed drive
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "drive": None
            }
        
        # Fetch fresh data from MS Graph
        graph_result = graph_get_drive_by_id(existing.drive_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch drive from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "drive": None
            }
        
        drive_data = graph_result.get("drive")
        if not drive_data:
            return {
                "message": "No drive data returned from MS Graph",
                "status_code": 500,
                "drive": None
            }
        
        # Update local record
        try:
            updated = self.repo.update_by_public_id(
                public_id=public_id,
                ms_site_id=existing.ms_site_id,
                drive_id=drive_data.get("drive_id"),
                name=drive_data.get("name"),
                web_url=drive_data.get("web_url"),
                drive_type=drive_data.get("drive_type"),
            )
            
            if updated:
                return {
                    "message": "Drive refreshed successfully",
                    "status_code": 200,
                    "drive": updated.to_dict()
                }
            else:
                return {
                    "message": "Failed to refresh drive",
                    "status_code": 500,
                    "drive": None
                }
        except Exception as e:
            logger.exception("Error refreshing drive")
            return {
                "message": f"Error refreshing drive: {str(e)}",
                "status_code": 500,
                "drive": None
            }
