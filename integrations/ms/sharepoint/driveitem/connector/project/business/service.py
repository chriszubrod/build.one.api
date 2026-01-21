# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project.business.model import DriveItemProject
from integrations.ms.sharepoint.driveitem.connector.project.persistence.repo import DriveItemProjectRepository
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository

logger = logging.getLogger(__name__)


class DriveItemProjectConnector:
    """
    Connector service for linking DriveItems (folders) to Projects.
    Ensures proper hierarchy: Drive -> DriveItem -> DriveItemProject mapping.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemProjectRepository] = None,
        driveitem_service: Optional[MsDriveItemService] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
    ):
        """Initialize the DriveItemProjectConnector."""
        self.mapping_repo = mapping_repo or DriveItemProjectRepository()
        self.driveitem_service = driveitem_service or MsDriveItemService()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()

    def link_driveitem_to_project(
        self,
        project_id: int,
        drive_public_id: str,
        graph_item_id: str
    ) -> dict:
        """
        Link a DriveItem (folder) to a Project.
        
        This method:
        1. Links the DriveItem in ms.DriveItem (fetches from Graph if needed)
        2. Creates the DriveItemProject mapping
        
        Args:
            project_id: Database ID of Project record
            drive_public_id: Public ID of the linked drive
            graph_item_id: MS Graph item ID to link (should be a folder)
        
        Returns:
            Dict with status_code, message, and mapping data
        """
        # Check if project already has a linked driveitem
        existing_mapping = self.mapping_repo.read_by_project_id(project_id)
        if existing_mapping:
            return {
                "message": "Project already has a linked folder",
                "status_code": 400,
                "mapping": existing_mapping.to_dict()
            }
        
        # Step 1: Link the DriveItem (will fetch from Graph and store locally)
        logger.info(f"Linking driveitem {graph_item_id} for drive {drive_public_id}...")
        driveitem_result = self.driveitem_service.link_item(
            drive_public_id=drive_public_id,
            item_id=graph_item_id
        )
        
        if driveitem_result.get("status_code") not in [200, 201]:
            return {
                "message": f"Failed to link driveitem: {driveitem_result.get('message')}",
                "status_code": driveitem_result.get("status_code", 500),
                "mapping": None
            }
        
        driveitem = driveitem_result.get("item")
        if not driveitem:
            return {
                "message": "DriveItem was linked but no item data returned",
                "status_code": 500,
                "mapping": None
            }
        
        # Verify it's a folder
        item_type = driveitem.get("item_type") if isinstance(driveitem, dict) else driveitem.item_type
        if item_type != "folder":
            return {
                "message": "Only folders can be linked to projects",
                "status_code": 400,
                "mapping": None
            }
        
        ms_driveitem_id = driveitem.get("id") if isinstance(driveitem, dict) else driveitem.id
        
        # Check if this driveitem is already linked to another project
        existing_driveitem_mapping = self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)
        if existing_driveitem_mapping:
            return {
                "message": f"DriveItem is already linked to project ID {existing_driveitem_mapping.project_id}",
                "status_code": 400,
                "mapping": existing_driveitem_mapping.to_dict()
            }
        
        # Step 2: Create the DriveItemProject mapping
        try:
            mapping = self.mapping_repo.create(
                project_id=project_id,
                ms_driveitem_id=ms_driveitem_id,
            )
            logger.info(f"Created mapping: Project {project_id} <-> DriveItem {ms_driveitem_id}")
            
            return {
                "message": "Folder linked to project successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "driveitem": driveitem if isinstance(driveitem, dict) else driveitem.to_dict(),
            }
        except Exception as e:
            logger.exception("Error creating driveitem-project mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def get_mapping_by_project_id(self, project_id: int) -> Optional[DriveItemProject]:
        """
        Get mapping by Project ID.
        """
        return self.mapping_repo.read_by_project_id(project_id)

    def get_mapping_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemProject]:
        """
        Get mapping by MS DriveItem ID.
        """
        return self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)

    def get_driveitem_for_project(self, project_id: int) -> Optional[dict]:
        """
        Get the linked driveitem details for a project.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict with driveitem details if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return None
        
        # Get the driveitem details from ms.DriveItem
        # Need to find by internal ID
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return None
        
        return driveitem.to_dict()

    def unlink_by_project_id(self, project_id: int) -> dict:
        """
        Unlink the driveitem from a project.
        Note: This only removes the mapping, not the DriveItem record.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked folder found for this project",
                "status_code": 404,
                "mapping": None
            }
        
        try:
            deleted = self.mapping_repo.delete_by_project_id(project_id)
            logger.info(f"Unlinked folder from project {project_id}")
            
            return {
                "message": "Folder unlinked from project successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking folder from project")
            return {
                "message": f"Error unlinking folder: {str(e)}",
                "status_code": 500,
                "mapping": None
            }
