# Python Standard Library Imports
from typing import Optional
import logging

# Local Imports
from integrations.ms.mail.message.connector.vendor.business.model import MsMessageVendor
from integrations.ms.mail.message.connector.vendor.persistence.repo import MsMessageVendorRepository
from integrations.ms.mail.message.persistence.repo import MsMessageRepository

logger = logging.getLogger(__name__)


class MsMessageVendorService:
    """
    Service for linking MsMessage to Vendor.
    """

    def __init__(
        self,
        repo: Optional[MsMessageVendorRepository] = None,
        message_repo: Optional[MsMessageRepository] = None
    ):
        self.repo = repo or MsMessageVendorRepository()
        self.message_repo = message_repo or MsMessageRepository()

    def link_message_to_vendor(
        self,
        message_public_id: str,
        vendor_id: int,
        notes: Optional[str] = None
    ) -> dict:
        """
        Link a stored message to a vendor.
        """
        message = self.message_repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "link": None
            }
        
        try:
            link = self.repo.create(
                ms_message_id=message.id,
                vendor_id=vendor_id,
                notes=notes
            )
            
            return {
                "message": "Message linked to vendor successfully",
                "status_code": 201,
                "link": link.to_dict()
            }
        except Exception as e:
            if "UQ_MsMessageVendor_Unique" in str(e):
                return {
                    "message": "Message is already linked to this vendor",
                    "status_code": 409,
                    "link": None
                }
            logger.exception("Error linking message to vendor")
            return {
                "message": f"Error linking message to vendor: {str(e)}",
                "status_code": 500,
                "link": None
            }

    def get_links_by_message(self, message_public_id: str) -> dict:
        """Get all vendor links for a message."""
        message = self.message_repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "links": []
            }
        
        links = self.repo.read_by_ms_message_id(message.id)
        return {
            "message": f"Found {len(links)} vendor links",
            "status_code": 200,
            "links": [link.to_dict() for link in links]
        }

    def get_links_by_vendor(self, vendor_id: int) -> dict:
        """Get all message links for a vendor."""
        links = self.repo.read_by_vendor_id(vendor_id)
        return {
            "message": f"Found {len(links)} message links",
            "status_code": 200,
            "links": [link.to_dict() for link in links]
        }

    def unlink(self, public_id: str) -> dict:
        """Remove a message-vendor link."""
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Link not found",
                "status_code": 404,
                "link": None
            }
        
        try:
            deleted = self.repo.delete_by_public_id(public_id)
            return {
                "message": "Link removed successfully",
                "status_code": 200,
                "link": deleted.to_dict() if deleted else None
            }
        except Exception as e:
            logger.exception("Error removing link")
            return {
                "message": f"Error removing link: {str(e)}",
                "status_code": 500,
                "link": None
            }
