# Python Standard Library Imports
from typing import Optional
import logging

# Local Imports
from integrations.ms.mail.message.connector.bill.business.model import MsMessageBill
from integrations.ms.mail.message.connector.bill.persistence.repo import MsMessageBillRepository
from integrations.ms.mail.message.persistence.repo import MsMessageRepository

logger = logging.getLogger(__name__)


class MsMessageBillService:
    """
    Service for linking MsMessage to Bill.
    """

    def __init__(
        self,
        repo: Optional[MsMessageBillRepository] = None,
        message_repo: Optional[MsMessageRepository] = None
    ):
        self.repo = repo or MsMessageBillRepository()
        self.message_repo = message_repo or MsMessageRepository()

    def link_message_to_bill(
        self,
        message_public_id: str,
        bill_id: int,
        notes: Optional[str] = None
    ) -> dict:
        """
        Link a stored message to a bill.
        
        Args:
            message_public_id: The public ID of the linked message
            bill_id: The bill ID to link to
            notes: Optional notes about the relationship
        
        Returns:
            Dict with status_code, message, and link data
        """
        # Get the message
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
                bill_id=bill_id,
                notes=notes
            )
            
            return {
                "message": "Message linked to bill successfully",
                "status_code": 201,
                "link": link.to_dict()
            }
        except Exception as e:
            # Check for unique constraint violation
            if "UQ_MsMessageBill_Unique" in str(e):
                return {
                    "message": "Message is already linked to this bill",
                    "status_code": 409,
                    "link": None
                }
            logger.exception("Error linking message to bill")
            return {
                "message": f"Error linking message to bill: {str(e)}",
                "status_code": 500,
                "link": None
            }

    def get_links_by_message(self, message_public_id: str) -> dict:
        """Get all bill links for a message."""
        message = self.message_repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "links": []
            }
        
        links = self.repo.read_by_ms_message_id(message.id)
        return {
            "message": f"Found {len(links)} bill links",
            "status_code": 200,
            "links": [link.to_dict() for link in links]
        }

    def get_links_by_bill(self, bill_id: int) -> dict:
        """Get all message links for a bill."""
        links = self.repo.read_by_bill_id(bill_id)
        return {
            "message": f"Found {len(links)} message links",
            "status_code": 200,
            "links": [link.to_dict() for link in links]
        }

    def unlink(self, public_id: str) -> dict:
        """Remove a message-bill link."""
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
