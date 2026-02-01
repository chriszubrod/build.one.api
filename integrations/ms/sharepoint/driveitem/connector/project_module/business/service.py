# Python Standard Library Imports
import logging
from typing import Optional, List, Dict

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project_module.business.model import DriveItemProjectModule
from integrations.ms.sharepoint.driveitem.connector.project_module.persistence.repo import DriveItemProjectModuleRepository
from integrations.ms.sharepoint.driveitem.connector.project.business.service import DriveItemProjectConnector
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.external.client import get_drive_item as graph_get_drive_item
from services.module.business.service import ModuleService

logger = logging.getLogger(__name__)


class DriveItemProjectModuleConnector:
    """
    Connector service for linking module-specific DriveItems (folders) to Projects.
    Ensures proper hierarchy: Project Root Folder -> Module Folder -> DriveItemProjectModule mapping.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemProjectModuleRepository] = None,
        project_connector: Optional[DriveItemProjectConnector] = None,
        driveitem_service: Optional[MsDriveItemService] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
    ):
        """Initialize the DriveItemProjectModuleConnector."""
        self.mapping_repo = mapping_repo or DriveItemProjectModuleRepository()
        self.project_connector = project_connector or DriveItemProjectConnector()
        self.driveitem_service = driveitem_service or MsDriveItemService()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()

    def _is_child_of_project_root(
        self,
        project_root_item_id: str,
        candidate_item_id: str,
        drive_id: str
    ) -> bool:
        """
        Check if a candidate folder is a child (direct or nested) of the project root folder.
        Traverses up the parent chain to verify.
        """
        current_item_id = candidate_item_id
        max_depth = 10  # Prevent infinite loops
        depth = 0
        
        while current_item_id and depth < max_depth:
            # Get item metadata from Graph
            result = graph_get_drive_item(drive_id, current_item_id)
            if result.get("status_code") != 200:
                logger.warning(f"Failed to get item {current_item_id} from Graph")
                return False
            
            item_data = result.get("item")
            if not item_data:
                return False
            
            # Check if this is the project root
            if item_data.get("item_id") == project_root_item_id:
                return True
            
            # Move up to parent
            current_item_id = item_data.get("parent_item_id")
            depth += 1
        
        return False

    def link_module_folder(
        self,
        project_id: int,
        module_id: int,
        graph_item_id: str
    ) -> dict:
        """
        Link a module-specific folder to a Project.
        
        This method:
        1. Verifies the project has a root folder
        2. Verifies the selected folder is a child of the project root
        3. Links the DriveItem in ms.DriveItem (fetches from Graph if needed)
        4. Creates the DriveItemProjectModule mapping
        
        Args:
            project_id: Database ID of Project record
            module_id: Database ID of Module record
            graph_item_id: MS Graph item ID to link (should be a folder, child of project root)
        
        Returns:
            Dict with status_code, message, and mapping data
        """
        # Step 1: Verify project has a root folder
        project_root = self.project_connector.get_driveitem_for_project(project_id)
        if not project_root:
            return {
                "message": "Project must have a root folder linked before linking module folders",
                "status_code": 400,
                "mapping": None
            }
        
        project_root_item_id = project_root.get("item_id")
        if not project_root_item_id:
            return {
                "message": "Project root folder is missing item_id",
                "status_code": 500,
                "mapping": None
            }
        
        # Get the drive for the project root
        project_root_driveitem = self.driveitem_repo.read_by_item_id(project_root_item_id)
        if not project_root_driveitem:
            return {
                "message": "Project root folder not found in database",
                "status_code": 404,
                "mapping": None
            }
        
        # Get drive to use for Graph API calls
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        drive = drive_repo.read_by_id(project_root_driveitem.ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for project root folder",
                "status_code": 404,
                "mapping": None
            }
        
        # Step 2: Verify module exists and get module name
        module_service = ModuleService()
        module = module_service.read_by_id(id=str(module_id))
        if not module:
            return {
                "message": f"Module with ID {module_id} not found",
                "status_code": 404,
                "mapping": None
            }
        
        # Step 3: Check if this module is already linked
        existing_mapping = self.mapping_repo.read_by_project_id_and_module_id(project_id, module_id)
        if existing_mapping:
            return {
                "message": f"Module '{module.name}' already has a linked folder for this project",
                "status_code": 400,
                "mapping": existing_mapping.to_dict()
            }
        
        # Step 4: Verify the selected folder is a child of project root
        # First, get the item from Graph to verify it exists and is a folder
        graph_result = graph_get_drive_item(drive.drive_id, graph_item_id)
        if graph_result.get("status_code") != 200:
            return {
                "message": f"Failed to fetch item from MS Graph: {graph_result.get('message')}",
                "status_code": graph_result.get("status_code", 500),
                "mapping": None
            }
        
        item_data = graph_result.get("item")
        if not item_data:
            return {
                "message": "No item data returned from MS Graph",
                "status_code": 500,
                "mapping": None
            }
        
        # Verify it's a folder
        if item_data.get("item_type") != "folder":
            return {
                "message": "Only folders can be linked as module folders",
                "status_code": 400,
                "mapping": None
            }
        
        # Verify it's a child of the project root
        if not self._is_child_of_project_root(project_root_item_id, graph_item_id, drive.drive_id):
            return {
                "message": "Selected folder must be within the project's root folder",
                "status_code": 400,
                "mapping": None
            }
        
        # Step 5: Link the DriveItem (will fetch from Graph and store locally if not already stored)
        logger.info(f"Linking module folder {graph_item_id} for project {project_id}, module {module.name} (ID: {module_id})...")
        driveitem_result = self.driveitem_service.link_item(
            drive_public_id=drive.public_id,
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
        
        ms_driveitem_id = driveitem.get("id") if isinstance(driveitem, dict) else driveitem.id
        
        # Check if this driveitem is already linked to another project module
        existing_driveitem_mapping = self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)
        if existing_driveitem_mapping:
            # Get module name for the existing mapping
            existing_module = module_service.read_by_id(id=str(existing_driveitem_mapping.module_id))
            existing_module_name = existing_module.name if existing_module else f"ID {existing_driveitem_mapping.module_id}"
            return {
                "message": f"Folder is already linked to project {existing_driveitem_mapping.project_id}, module '{existing_module_name}'",
                "status_code": 400,
                "mapping": existing_driveitem_mapping.to_dict()
            }
        
        # Step 6: Create the DriveItemProjectModule mapping
        try:
            mapping = self.mapping_repo.create(
                project_id=project_id,
                module_id=module_id,
                ms_driveitem_id=ms_driveitem_id,
            )
            logger.info(f"Created mapping: Project {project_id}, Module {module.name} (ID: {module_id}) <-> DriveItem {ms_driveitem_id}")
            
            return {
                "message": f"Folder linked to project module '{module.name}' successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "driveitem": driveitem if isinstance(driveitem, dict) else driveitem.to_dict(),
            }
        except Exception as e:
            logger.exception("Error creating driveitem-project-module mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def get_folder_for_module(self, project_id: int, module_id: int) -> Optional[dict]:
        """
        Get the linked folder for a specific module in a project.
        
        Args:
            project_id: Database ID of Project record
            module_id: Database ID of Module record
        
        Returns:
            Dict with folder details if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_project_id_and_module_id(project_id, module_id)
        if not mapping:
            return None
        
        # Get the driveitem details from ms.DriveItem
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return None
        
        return driveitem.to_dict()

    def get_all_module_folders(self, project_id: int) -> Dict[int, dict]:
        """
        Get all linked module folders for a project.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict mapping module_id to folder details
        """
        mappings = self.mapping_repo.read_by_project_id(project_id)
        if not mappings:
            return {}
        
        # Get all driveitems
        items = self.driveitem_repo.read_all()
        items_by_id = {item.id: item for item in items}
        
        result = {}
        for mapping in mappings:
            driveitem = items_by_id.get(mapping.ms_driveitem_id)
            if driveitem:
                result[mapping.module_id] = driveitem.to_dict()
        
        return result

    def unlink_module_folder(self, project_id: int, module_id: int) -> dict:
        """
        Unlink the folder from a project module.
        Note: This only removes the mapping, not the DriveItem record.
        
        Args:
            project_id: Database ID of Project record
            module_id: Database ID of Module record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_project_id_and_module_id(project_id, module_id)
        if not mapping:
            return {
                "message": f"No linked folder found for module ID {module_id} in this project",
                "status_code": 404,
                "mapping": None
            }
        
        # Get module name for logging
        module_service = ModuleService()
        module = module_service.read_by_id(id=str(module_id))
        module_name = module.name if module else f"ID {module_id}"
        
        try:
            deleted = self.mapping_repo.delete_by_project_id_and_module_id(project_id, module_id)
            logger.info(f"Unlinked folder from project {project_id}, module {module_name} (ID: {module_id})")
            
            return {
                "message": f"Folder unlinked from project module '{module_name}' successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking folder from project module")
            return {
                "message": f"Error unlinking folder: {str(e)}",
                "status_code": 500,
                "mapping": None
            }
