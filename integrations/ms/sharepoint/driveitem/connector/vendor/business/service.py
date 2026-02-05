# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.vendor.business.model import DriveItemVendor
from integrations.ms.sharepoint.driveitem.connector.vendor.persistence.repo import DriveItemVendorRepository
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository

logger = logging.getLogger(__name__)


class DriveItemVendorConnector:
    """
    Connector service for linking DriveItems (folders) to Vendors.
    Mirrors the Project pattern: one folder per vendor, one vendor per folder.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemVendorRepository] = None,
        driveitem_service: Optional[MsDriveItemService] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
        drive_repo: Optional[MsDriveRepository] = None,
    ):
        """Initialize the DriveItemVendorConnector."""
        self.mapping_repo = mapping_repo or DriveItemVendorRepository()
        self.driveitem_service = driveitem_service or MsDriveItemService()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()
        self.drive_repo = drive_repo or MsDriveRepository()

    def link_driveitem_to_vendor(
        self,
        vendor_id: int,
        drive_public_id: str,
        graph_item_id: str,
    ) -> dict:
        """
        Link a DriveItem (folder) to a Vendor.

        Args:
            vendor_id: Database ID of Vendor record
            drive_public_id: Public ID of the linked drive
            graph_item_id: MS Graph item ID to link (should be a folder)

        Returns:
            Dict with status_code, message, and mapping/driveitem data
        """
        existing_mapping = self.mapping_repo.read_by_vendor_id(vendor_id)
        if existing_mapping:
            return {
                "message": "Vendor already has a linked folder",
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
                "message": "Only folders can be linked to vendors",
                "status_code": 400,
                "mapping": None,
            }

        ms_driveitem_id = driveitem.get("id") if isinstance(driveitem, dict) else getattr(driveitem, "id", None)
        existing_by_item = self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)
        if existing_by_item:
            return {
                "message": f"Folder is already linked to vendor ID {existing_by_item.vendor_id}",
                "status_code": 400,
                "mapping": None,
            }

        try:
            mapping = self.mapping_repo.create(vendor_id=vendor_id, ms_driveitem_id=ms_driveitem_id)
            logger.info("Created mapping: Vendor %s <-> DriveItem %s", vendor_id, ms_driveitem_id)
            return {
                "message": "Folder linked to vendor successfully",
                "status_code": 201,
                "mapping": mapping.to_dict(),
                "driveitem": driveitem if isinstance(driveitem, dict) else driveitem.to_dict(),
            }
        except Exception as e:
            logger.exception("Error creating driveitem-vendor mapping")
            return {
                "message": str(e),
                "status_code": 500,
                "mapping": None,
            }

    def get_driveitem_for_vendor(self, vendor_id: int) -> Optional[dict]:
        """
        Get the linked driveitem details for a vendor.
        Includes Graph drive_id and item_id for API calls (e.g. backfill).

        Returns:
            Dict with driveitem fields plus drive_id (Graph), item_id (Graph); None if not linked.
        """
        mapping = self.mapping_repo.read_by_vendor_id(vendor_id)
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
        return out

    def unlink_by_vendor_id(self, vendor_id: int) -> dict:
        """
        Unlink the driveitem from a vendor. Only removes the mapping.

        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_vendor_id(vendor_id)
        if not mapping:
            return {
                "message": "No linked folder found for this vendor",
                "status_code": 404,
                "mapping": None,
            }
        try:
            deleted = self.mapping_repo.delete_by_vendor_id(vendor_id)
            logger.info("Unlinked folder from vendor %s", vendor_id)
            return {
                "message": "Folder unlinked from vendor successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None,
            }
        except Exception as e:
            logger.exception("Error unlinking folder from vendor")
            return {
                "message": str(e),
                "status_code": 500,
                "mapping": None,
            }
