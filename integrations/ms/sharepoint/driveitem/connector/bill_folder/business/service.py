# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.model import DriveItemBillFolder
from integrations.ms.sharepoint.driveitem.connector.bill_folder.persistence.repo import DriveItemBillFolderRepository
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository

logger = logging.getLogger(__name__)

VALID_FOLDER_TYPES = ("source", "processed")


class DriveItemBillFolderConnector:
    """
    Connector service for linking DriveItems (folders) to the Bill module at the company level.
    Supports two folder types: 'source' (intake) and 'processed' (completed).
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemBillFolderRepository] = None,
        driveitem_service: Optional[MsDriveItemService] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
        drive_repo: Optional[MsDriveRepository] = None,
    ):
        """Initialize the DriveItemBillFolderConnector."""
        self.mapping_repo = mapping_repo or DriveItemBillFolderRepository()
        self.driveitem_service = driveitem_service or MsDriveItemService()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()
        self.drive_repo = drive_repo or MsDriveRepository()

    def link_folder(
        self,
        company_id: int,
        drive_public_id: str,
        graph_item_id: str,
        folder_type: str,
    ) -> dict:
        """
        Link a DriveItem (folder) to the bill module for a company.

        Args:
            company_id: Database ID of the Company record
            drive_public_id: Public ID of the linked drive
            graph_item_id: MS Graph item ID to link (should be a folder)
            folder_type: 'source' or 'processed'

        Returns:
            Dict with status_code, message, and mapping/driveitem data
        """
        if folder_type not in VALID_FOLDER_TYPES:
            return {
                "message": f"Invalid folder_type: '{folder_type}'. Must be 'source' or 'processed'.",
                "status_code": 400,
                "mapping": None,
            }

        existing_mapping = self.mapping_repo.read_by_company_id_and_folder_type(company_id, folder_type)
        if existing_mapping:
            return {
                "message": f"Company already has a linked '{folder_type}' folder. Unlink it first.",
                "status_code": 400,
                "mapping": existing_mapping.to_dict(),
            }

        driveitem_result = self.driveitem_service.link_item(
            drive_public_id=drive_public_id,
            item_id=graph_item_id,
        )
        if driveitem_result.get("status_code") not in (200, 201):
            return {
                "message": driveitem_result.get("message", "Failed to link driveitem"),
                "status_code": driveitem_result.get("status_code", 500),
                "mapping": None,
            }

        driveitem = driveitem_result.get("item")
        if not driveitem:
            return {
                "message": "DriveItem was linked but no item data returned",
                "status_code": 500,
                "mapping": None,
            }

        item_type = driveitem.get("item_type") if isinstance(driveitem, dict) else getattr(driveitem, "item_type", None)
        if item_type != "folder":
            return {
                "message": "Only folders can be linked as bill processing folders",
                "status_code": 400,
                "mapping": None,
            }

        ms_driveitem_id = driveitem.get("id") if isinstance(driveitem, dict) else getattr(driveitem, "id", None)

        try:
            mapping = self.mapping_repo.create(
                company_id=company_id,
                ms_driveitem_id=ms_driveitem_id,
                folder_type=folder_type,
            )
            logger.info("Created bill folder mapping: Company %s, Type '%s' <-> DriveItem %s", company_id, folder_type, ms_driveitem_id)
            return {
                "message": f"Folder linked as '{folder_type}' successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "driveitem": driveitem if isinstance(driveitem, dict) else driveitem.to_dict(),
            }
        except Exception as e:
            logger.exception("Error creating driveitem-bill-folder mapping")
            return {
                "message": str(e),
                "status_code": 500,
                "mapping": None,
            }

    def get_folder(self, company_id: int, folder_type: str) -> Optional[dict]:
        """
        Get the linked driveitem details for a bill folder.
        Includes Graph drive_id and item_id for API calls.

        Returns:
            Dict with driveitem fields plus drive_id (Graph), item_id (Graph); None if not linked.
        """
        if folder_type not in VALID_FOLDER_TYPES:
            return None

        mapping = self.mapping_repo.read_by_company_id_and_folder_type(company_id, folder_type)
        if not mapping:
            return None

        items = self.driveitem_repo.read_all()
        driveitem = next((item for item in items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return None

        out = driveitem.to_dict()
        drive = self.drive_repo.read_by_id(driveitem.ms_drive_id) if driveitem.ms_drive_id else None
        if drive and drive.drive_id:
            out["drive_id"] = drive.drive_id
        out["item_id"] = driveitem.item_id
        out["folder_type"] = folder_type
        return out

    def unlink_folder(self, company_id: int, folder_type: str) -> dict:
        """
        Unlink the driveitem from the bill module. Only removes the mapping.

        Returns:
            Dict with status_code, message, and deleted mapping
        """
        if folder_type not in VALID_FOLDER_TYPES:
            return {
                "message": f"Invalid folder_type: '{folder_type}'. Must be 'source' or 'processed'.",
                "status_code": 400,
                "mapping": None,
            }

        mapping = self.mapping_repo.read_by_company_id_and_folder_type(company_id, folder_type)
        if not mapping:
            return {
                "message": f"No linked '{folder_type}' folder found for this company",
                "status_code": 404,
                "mapping": None,
            }
        try:
            deleted = self.mapping_repo.delete_by_company_id_and_folder_type(company_id, folder_type)
            logger.info("Unlinked '%s' folder from company %s", folder_type, company_id)
            return {
                "message": f"'{folder_type}' folder unlinked successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None,
            }
        except Exception as e:
            logger.exception("Error unlinking bill folder from company")
            return {
                "message": str(e),
                "status_code": 500,
                "mapping": None,
            }
