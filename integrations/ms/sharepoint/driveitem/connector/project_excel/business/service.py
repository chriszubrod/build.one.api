# Python Standard Library Imports
import logging
from typing import Optional, List, Dict, Any

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.model import DriveItemProjectExcel
from integrations.ms.sharepoint.driveitem.connector.project_excel.persistence.repo import DriveItemProjectExcelRepository
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.external.client import (
    get_excel_worksheets,
    get_excel_worksheet,
    update_excel_range,
    append_excel_rows,
    clear_excel_range,
    get_drive_item as graph_get_drive_item,
)

logger = logging.getLogger(__name__)


class DriveItemProjectExcelConnector:
    """
    Connector service for linking Excel workbooks (DriveItems) to Projects.
    Provides functionality to push data to specific worksheets within the workbook.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemProjectExcelRepository] = None,
        driveitem_service: Optional[MsDriveItemService] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
    ):
        """Initialize the DriveItemProjectExcelConnector."""
        self.mapping_repo = mapping_repo or DriveItemProjectExcelRepository()
        self.driveitem_service = driveitem_service or MsDriveItemService()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()

    def link_excel_to_project(
        self,
        project_id: int,
        drive_public_id: str,
        graph_item_id: str,
        worksheet_name: str
    ) -> dict:
        """
        Link an Excel workbook DriveItem to a Project.
        
        This method:
        1. Links the DriveItem in ms.DriveItem (fetches from Graph if needed)
        2. Validates the file is an Excel workbook (.xlsx)
        3. Validates the worksheet exists in the workbook
        4. Creates the DriveItemProjectExcel mapping with worksheet_name
        
        Args:
            project_id: Database ID of Project record
            drive_public_id: Public ID of the linked drive
            graph_item_id: MS Graph item ID to link (should be an Excel .xlsx file)
            worksheet_name: Name of the worksheet to target for data operations
        
        Returns:
            Dict with status_code, message, and mapping data
        """
        # Check if project already has a linked Excel workbook
        existing_mapping = self.mapping_repo.read_by_project_id(project_id)
        if existing_mapping:
            return {
                "message": "Project already has a linked Excel workbook",
                "status_code": 400,
                "mapping": existing_mapping.to_dict()
            }
        
        # Step 1: Link the DriveItem (will fetch from Graph and store locally)
        logger.info(f"Linking Excel workbook {graph_item_id} for drive {drive_public_id}...")
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
        
        # Step 2: Verify it's an Excel file
        mime_type = driveitem.get("mime_type") if isinstance(driveitem, dict) else driveitem.mime_type
        excel_mime_types = [
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel"
        ]
        
        if mime_type not in excel_mime_types:
            return {
                "message": f"File is not an Excel workbook. MIME type: {mime_type}. Only .xlsx files are supported.",
                "status_code": 400,
                "mapping": None
            }
        
        # Get the drive for Graph API calls
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        ms_driveitem_id = driveitem.get("id") if isinstance(driveitem, dict) else driveitem.id
        
        # Get ms_drive_id from the driveitem
        ms_drive_id = driveitem.get("ms_drive_id") if isinstance(driveitem, dict) else driveitem.ms_drive_id
        if not ms_drive_id:
            # Fallback: try to find by reading from database using item_id
            driveitem_db = self.driveitem_repo.read_by_item_id(graph_item_id)
            if driveitem_db:
                ms_drive_id = driveitem_db.ms_drive_id
            else:
                return {
                    "message": "DriveItem not found in database and missing ms_drive_id",
                    "status_code": 404,
                    "mapping": None
                }
        
        drive = drive_repo.read_by_id(ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for workbook",
                "status_code": 404,
                "mapping": None
            }
        
        # Step 3: Validate worksheet exists in workbook
        worksheets_result = get_excel_worksheets(drive.drive_id, graph_item_id)
        if worksheets_result.get("status_code") != 200:
            return {
                "message": f"Failed to get worksheets from workbook: {worksheets_result.get('message')}",
                "status_code": worksheets_result.get("status_code", 500),
                "mapping": None
            }
        
        worksheets = worksheets_result.get("worksheets", [])
        worksheet_names = [ws.get("name") for ws in worksheets]
        
        if worksheet_name not in worksheet_names:
            return {
                "message": f"Worksheet '{worksheet_name}' not found in workbook. Available worksheets: {', '.join(worksheet_names)}",
                "status_code": 400,
                "mapping": None
            }
        
        # Check if this driveitem is already linked to another project
        existing_driveitem_mapping = self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)
        if existing_driveitem_mapping:
            return {
                "message": f"Excel workbook is already linked to project ID {existing_driveitem_mapping.project_id}",
                "status_code": 400,
                "mapping": existing_driveitem_mapping.to_dict()
            }
        
        # Step 4: Create the DriveItemProjectExcel mapping
        try:
            mapping = self.mapping_repo.create(
                project_id=project_id,
                ms_driveitem_id=ms_driveitem_id,
                worksheet_name=worksheet_name,
            )
            logger.info(f"Created Excel mapping: Project {project_id} <-> DriveItem {ms_driveitem_id} (worksheet: {worksheet_name})")
            
            return {
                "message": f"Excel workbook linked to project successfully (worksheet: {worksheet_name})",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "driveitem": driveitem if isinstance(driveitem, dict) else driveitem.to_dict(),
                "worksheet_name": worksheet_name,
            }
        except Exception as e:
            logger.exception("Error creating driveitem-project-excel mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def get_excel_for_project(self, project_id: int) -> Optional[dict]:
        """
        Get the linked Excel workbook details for a project.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict with workbook details and worksheet_name if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return None
        
        # Get the driveitem details from ms.DriveItem
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return None
        
        result = driveitem.to_dict()
        result["worksheet_name"] = mapping.worksheet_name
        return result

    def unlink_excel_from_project(self, project_id: int) -> dict:
        """
        Unlink the Excel workbook from a project.
        Note: This only removes the mapping, not the DriveItem record.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked Excel workbook found for this project",
                "status_code": 404,
                "mapping": None
            }
        
        try:
            deleted = self.mapping_repo.delete_by_project_id(project_id)
            logger.info(f"Unlinked Excel workbook from project {project_id}")
            
            return {
                "message": "Excel workbook unlinked from project successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking Excel workbook from project")
            return {
                "message": f"Error unlinking workbook: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def list_worksheets(self, project_id: int) -> dict:
        """
        List all worksheets in the linked Excel workbook.
        
        Args:
            project_id: Database ID of Project record
        
        Returns:
            Dict with status_code, message, and worksheets list
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked Excel workbook found for this project",
                "status_code": 404,
                "worksheets": []
            }
        
        # Get driveitem and drive
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return {
                "message": "DriveItem not found for linked workbook",
                "status_code": 404,
                "worksheets": []
            }
        
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        drive = drive_repo.read_by_id(driveitem.ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for workbook",
                "status_code": 404,
                "worksheets": []
            }
        
        # Get worksheets from Graph API
        return get_excel_worksheets(drive.drive_id, driveitem.item_id)

    def push_data_to_worksheet(
        self,
        project_id: int,
        data: List[List[Any]],
        range_address: Optional[str] = None
    ) -> dict:
        """
        Push data to the stored worksheet in the linked Excel workbook.
        
        Args:
            project_id: Database ID of Project record
            data: 2D array of values [[row1], [row2], ...]
            range_address: Optional Excel range address (e.g., "A1:D4"). If not provided, starts at A1.
        
        Returns:
            Dict with status_code, message, and updated range data
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked Excel workbook found for this project",
                "status_code": 404,
                "range": None
            }
        
        # Get driveitem and drive
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return {
                "message": "DriveItem not found for linked workbook",
                "status_code": 404,
                "range": None
            }
        
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        drive = drive_repo.read_by_id(driveitem.ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for workbook",
                "status_code": 404,
                "range": None
            }
        
        # Calculate range if not provided
        if not range_address:
            num_rows = len(data)
            num_cols = len(data[0]) if data else 1
            
            # Convert column number to letter
            def col_num_to_letter(n):
                result = ""
                while n > 0:
                    n -= 1
                    result = chr(65 + (n % 26)) + result
                    n //= 26
                return result
            
            end_col = col_num_to_letter(num_cols)
            range_address = f"A1:{end_col}{num_rows}"
        
        # Update the range
        return update_excel_range(
            drive.drive_id,
            driveitem.item_id,
            mapping.worksheet_name,
            range_address,
            data
        )

    def append_rows_to_worksheet(
        self,
        project_id: int,
        rows: List[List[Any]]
    ) -> dict:
        """
        Append rows to the stored worksheet in the linked Excel workbook.
        
        Args:
            project_id: Database ID of Project record
            rows: 2D array of values to append [[row1], [row2], ...]
        
        Returns:
            Dict with status_code, message, and appended range data
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked Excel workbook found for this project",
                "status_code": 404,
                "range": None
            }
        
        # Get driveitem and drive
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return {
                "message": "DriveItem not found for linked workbook",
                "status_code": 404,
                "range": None
            }
        
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        drive = drive_repo.read_by_id(driveitem.ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for workbook",
                "status_code": 404,
                "range": None
            }
        
        # Append rows
        return append_excel_rows(
            drive.drive_id,
            driveitem.item_id,
            mapping.worksheet_name,
            rows
        )

    def clear_worksheet_range(
        self,
        project_id: int,
        range_address: str
    ) -> dict:
        """
        Clear a range in the stored worksheet in the linked Excel workbook.
        
        Args:
            project_id: Database ID of Project record
            range_address: Excel range address (e.g., "A1:D4")
        
        Returns:
            Dict with status_code, message, and cleared range data
        """
        mapping = self.mapping_repo.read_by_project_id(project_id)
        if not mapping:
            return {
                "message": "No linked Excel workbook found for this project",
                "status_code": 404,
                "range": None
            }
        
        # Get driveitem and drive
        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return {
                "message": "DriveItem not found for linked workbook",
                "status_code": 404,
                "range": None
            }
        
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        drive_repo = MsDriveRepository()
        drive = drive_repo.read_by_id(driveitem.ms_drive_id)
        if not drive:
            return {
                "message": "Drive not found for workbook",
                "status_code": 404,
                "range": None
            }
        
        # Clear the range
        return clear_excel_range(
            drive.drive_id,
            driveitem.item_id,
            mapping.worksheet_name,
            range_address
        )
