# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.attachment.business.model import DriveItemAttachment
from integrations.ms.sharepoint.driveitem.connector.attachment.persistence.repo import DriveItemAttachmentRepository
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository

logger = logging.getLogger(__name__)


class DriveItemAttachmentConnector:
    """
    Connector service for linking DriveItems (SharePoint files) to Attachments.
    Used to track which Attachment records have been uploaded to SharePoint.
    """

    def __init__(
        self,
        mapping_repo: Optional[DriveItemAttachmentRepository] = None,
        driveitem_repo: Optional[MsDriveItemRepository] = None,
        drive_repo: Optional[MsDriveRepository] = None,
    ):
        """Initialize the DriveItemAttachmentConnector."""
        self.mapping_repo = mapping_repo or DriveItemAttachmentRepository()
        self.driveitem_repo = driveitem_repo or MsDriveItemRepository()
        self.drive_repo = drive_repo or MsDriveRepository()

    def link_driveitem_to_attachment(
        self,
        attachment_id: int,
        ms_driveitem_id: int
    ) -> dict:
        """
        Link a DriveItem (SharePoint file) to an Attachment.
        
        Args:
            attachment_id: Database ID of Attachment record
            ms_driveitem_id: Database ID of MsDriveItem record
        
        Returns:
            Dict with status_code, message, and mapping data
        """
        # Check if attachment is already linked
        existing = self.mapping_repo.read_by_attachment_id(attachment_id)
        if existing:
            return {
                "message": "Attachment already linked to a SharePoint file",
                "status_code": 200,
                "mapping": existing.to_dict()
            }
        
        # Check if driveitem is already linked to another attachment
        existing_driveitem = self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)
        if existing_driveitem:
            return {
                "message": f"DriveItem already linked to attachment ID {existing_driveitem.attachment_id}",
                "status_code": 400,
                "mapping": existing_driveitem.to_dict()
            }
        
        try:
            mapping = self.mapping_repo.create(
                attachment_id=attachment_id,
                ms_driveitem_id=ms_driveitem_id,
            )
            logger.info(f"Created mapping: Attachment {attachment_id} <-> DriveItem {ms_driveitem_id}")
            
            return {
                "message": "Attachment linked to SharePoint file successfully",
                "status_code": 201,
                "mapping": mapping.to_dict()
            }
        except Exception as e:
            logger.exception("Error creating driveitem-attachment mapping")
            return {
                "message": f"Error creating mapping: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def get_driveitem_for_attachment(self, attachment_id: int) -> Optional[dict]:
        """
        Get the linked DriveItem for an Attachment.
        
        Args:
            attachment_id: Database ID of Attachment record
        
        Returns:
            Dict with DriveItem details if linked, None otherwise
        """
        mapping = self.mapping_repo.read_by_attachment_id(attachment_id)
        if not mapping:
            return None
        
        # Get the driveitem details from ms.DriveItem
        # Note: Using read_all() and filter since there's no read_by_id method
        all_items = self.driveitem_repo.read_all()
        driveitem = next((item for item in all_items if item.id == mapping.ms_driveitem_id), None)
        if not driveitem:
            return None
        
        # Get the drive for additional context
        drive = self.drive_repo.read_by_id(driveitem.ms_drive_id) if driveitem.ms_drive_id else None
        
        result = driveitem.to_dict()
        if drive:
            result['drive_id'] = drive.drive_id
            result['drive_public_id'] = drive.public_id
        
        return result

    def get_attachment_for_driveitem(self, ms_driveitem_id: int) -> Optional[DriveItemAttachment]:
        """
        Get the mapping record for a DriveItem.
        
        Args:
            ms_driveitem_id: Database ID of MsDriveItem record
        
        Returns:
            DriveItemAttachment mapping if exists, None otherwise
        """
        return self.mapping_repo.read_by_ms_driveitem_id(ms_driveitem_id)

    def unlink_attachment(self, attachment_id: int) -> dict:
        """
        Unlink a DriveItem from an Attachment.
        Note: This only removes the mapping, not the DriveItem or Attachment records.
        
        Args:
            attachment_id: Database ID of Attachment record
        
        Returns:
            Dict with status_code, message, and deleted mapping
        """
        mapping = self.mapping_repo.read_by_attachment_id(attachment_id)
        if not mapping:
            return {
                "message": "No linked SharePoint file found for this attachment",
                "status_code": 404,
                "mapping": None
            }
        
        try:
            deleted = self.mapping_repo.delete_by_attachment_id(attachment_id)
            logger.info(f"Unlinked attachment {attachment_id} from DriveItem")
            
            return {
                "message": "Attachment unlinked from SharePoint file successfully",
                "status_code": 200,
                "mapping": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error unlinking attachment from driveitem")
            return {
                "message": f"Error unlinking: {str(e)}",
                "status_code": 500,
                "mapping": None
            }

    def is_attachment_synced(self, attachment_id: int) -> bool:
        """
        Check if an Attachment has been synced to SharePoint.
        
        Args:
            attachment_id: Database ID of Attachment record
        
        Returns:
            True if linked to a DriveItem, False otherwise
        """
        mapping = self.mapping_repo.read_by_attachment_id(attachment_id)
        return mapping is not None
