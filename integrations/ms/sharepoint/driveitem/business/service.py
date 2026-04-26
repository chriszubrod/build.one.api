# Python Standard Library Imports
from typing import Optional
import logging

# Third-party Imports

# Local Imports
from integrations.ms.sharepoint.driveitem.business.model import MsDriveItem
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from integrations.ms.sharepoint.external.client import (
    list_drive_root_children as graph_list_drive_root_children,
    list_drive_item_children as graph_list_drive_item_children,
    get_drive_item as graph_get_drive_item,
    get_drive_item_content as graph_get_drive_item_content,
    upload_small_file as graph_upload_small_file,
    upload_large_file as graph_upload_large_file,
    create_folder as graph_create_folder,
)

logger = logging.getLogger(__name__)


class MsDriveItemService:
    """
    Service for MsDriveItem entity business operations.
    Combines persistence with MS Graph API calls.
    """

    def __init__(
        self,
        repo: Optional[MsDriveItemRepository] = None,
        drive_repo: Optional[MsDriveRepository] = None
    ):
        """Initialize the MsDriveItemService."""
        self.repo = repo or MsDriveItemRepository()
        self.drive_repo = drive_repo or MsDriveRepository()

    def browse_drive_root(self, drive_public_id: str) -> dict:
        """
        Browse items at the root of a linked drive.
        Does not store results - just returns items from MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
        
        Returns:
            Dict with status_code, message, and items list
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "items": []
            }
        
        # Fetch items from MS Graph
        return graph_list_drive_root_children(drive.drive_id)

    def browse_folder(self, drive_public_id: str, item_id: str) -> dict:
        """
        Browse items in a specific folder of a linked drive.
        Does not store results - just returns items from MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
            item_id: The MS Graph item ID of the folder
        
        Returns:
            Dict with status_code, message, and items list
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "items": []
            }
        
        # Fetch items from MS Graph
        return graph_list_drive_item_children(drive.drive_id, item_id)

    def get_item_metadata(self, drive_public_id: str, item_id: str) -> dict:
        """
        Get metadata for a specific item from MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
            item_id: The MS Graph item ID
        
        Returns:
            Dict with status_code, message, and item data
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None
            }
        
        # Fetch item from MS Graph
        return graph_get_drive_item(drive.drive_id, item_id)

    def download_item(self, drive_public_id: str, item_id: str) -> dict:
        """
        Download content of a file from MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
            item_id: The MS Graph item ID (must be a file)
        
        Returns:
            Dict with status_code, message, content (bytes), and content_type
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "content": None,
                "content_type": None
            }
        
        # First get item metadata to get the filename
        item_result = graph_get_drive_item(drive.drive_id, item_id)
        if item_result.get("status_code") != 200:
            return {
                "message": item_result.get("message", "Failed to get item metadata"),
                "status_code": item_result.get("status_code", 500),
                "content": None,
                "content_type": None,
                "filename": None
            }
        
        item = item_result.get("item", {})
        if item.get("item_type") != "file":
            return {
                "message": "Cannot download a folder",
                "status_code": 400,
                "content": None,
                "content_type": None,
                "filename": None
            }
        
        # Download content from MS Graph
        content_result = graph_get_drive_item_content(drive.drive_id, item_id)
        content_result["filename"] = item.get("name")
        
        return content_result

    def upload_file(
        self,
        drive_public_id: str,
        parent_item_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream"
    ) -> dict:
        """
        Upload a file to a folder in MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
            parent_item_id: The MS Graph item ID of the parent folder
            filename: The name for the new file
            content: The file content as bytes
            content_type: The MIME type of the file
        
        Returns:
            Dict with status_code, message, and created item data
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None
            }
        
        # Files > 4MB must use the resumable upload session API
        if len(content) > 4 * 1024 * 1024:
            return graph_upload_large_file(drive.drive_id, parent_item_id, filename, content, content_type)

        # Upload to MS Graph
        return graph_upload_small_file(
            drive.drive_id,
            parent_item_id,
            filename,
            content,
            content_type
        )

    def move_item(
        self,
        drive_public_id: str,
        item_id: str,
        new_parent_id: str,
    ) -> dict:
        """
        Move a file or folder to a different parent folder.

        Args:
            drive_public_id: Public ID of the linked drive
            item_id: MS Graph item ID of the item to move
            new_parent_id: MS Graph item ID of the destination folder

        Returns:
            Dict with status_code, message, and moved item data
        """
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {"message": "Drive not found", "status_code": 404, "item": None}

        from integrations.ms.sharepoint.external.client import move_item
        return move_item(drive.drive_id, item_id, new_parent_id)

    def create_folder(
        self,
        drive_public_id: str,
        parent_item_id: str,
        folder_name: str
    ) -> dict:
        """
        Create a folder in MS Graph.
        
        Args:
            drive_public_id: The public ID of the linked drive
            parent_item_id: The MS Graph item ID of the parent folder
            folder_name: The name for the new folder
        
        Returns:
            Dict with status_code, message, and created folder data
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None
            }
        
        # Create folder in MS Graph
        return graph_create_folder(drive.drive_id, parent_item_id, folder_name)

    def read_or_create_folder(
        self,
        drive_public_id: str,
        parent_item_id: str,
        folder_name: str,
    ) -> dict:
        """
        Idempotent folder create. Returns the existing folder when one
        with the given name already lives under `parent_item_id`;
        creates and returns a new one otherwise.

        Used by workflows that re-run (e.g. the invoice-completion
        flow that uploads the packet + supporting PDFs into a per-
        invoice subfolder). Plain `create_folder` uses Graph's
        `conflictBehavior: fail` and 409s on the second run, killing
        the workflow.

        Returns the same shape as `create_folder` for drop-in use:
            { status_code, message, item }
        """
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None,
            }

        # 1. List children of the parent and look for a folder match.
        from integrations.ms.sharepoint.external.client import (
            list_drive_item_children,
        )
        list_result = list_drive_item_children(drive.drive_id, parent_item_id)
        if list_result.get("status_code") == 200:
            for child in list_result.get("items") or []:
                if (
                    child.get("item_type") == "folder"
                    and child.get("name") == folder_name
                ):
                    return {
                        "message": "Folder exists; reusing.",
                        "status_code": 200,
                        "item": child,
                    }
        # If the listing failed (4xx/5xx), fall through to create — it
        # will surface its own error if there's a real problem.

        # 2. Otherwise create as usual.
        return self.create_folder(
            drive_public_id=drive_public_id,
            parent_item_id=parent_item_id,
            folder_name=folder_name,
        )

    def link_item(self, drive_public_id: str, item_id: str) -> dict:
        """
        Link a DriveItem by fetching from MS Graph and storing locally.
        
        Args:
            drive_public_id: The public ID of the linked drive
            item_id: The MS Graph item ID to link
        
        Returns:
            Dict with status_code, message, and linked item
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None
            }
        
        # Check if already linked
        existing = self.repo.read_by_item_id(item_id)
        if existing:
            return {
                "message": "Item is already linked",
                "status_code": 200,
                "item": existing.to_dict()
            }
        
        # Fetch item from MS Graph
        graph_result = graph_get_drive_item(drive.drive_id, item_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch item from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "item": None
            }
        
        item_data = graph_result.get("item")
        if not item_data:
            return {
                "message": "No item data returned from MS Graph",
                "status_code": 500,
                "item": None
            }
        
        # Store in database
        try:
            ms_item = self.repo.create(
                ms_drive_id=drive.id,
                item_id=item_data.get("item_id"),
                parent_item_id=item_data.get("parent_item_id"),
                name=item_data.get("name"),
                item_type=item_data.get("item_type"),
                size=item_data.get("size"),
                mime_type=item_data.get("mime_type"),
                web_url=item_data.get("web_url"),
                graph_created_datetime=item_data.get("graph_created_datetime"),
                graph_modified_datetime=item_data.get("graph_modified_datetime"),
            )
            
            return {
                "message": "Item linked successfully",
                "status_code": 201,
                "item": ms_item.to_dict()
            }
        except Exception as e:
            logger.exception("Error linking item")
            return {
                "message": f"Error linking item: {str(e)}",
                "status_code": 500,
                "item": None
            }

    def read_all(self) -> list[MsDriveItem]:
        """
        Read all linked MsDriveItems from the database.
        """
        return self.repo.read_all()

    def read_by_drive_public_id(self, drive_public_id: str) -> dict:
        """
        Read all linked MsDriveItems for a specific drive.
        
        Args:
            drive_public_id: The public ID of the linked drive
        
        Returns:
            Dict with status_code, message, and items list
        """
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(drive_public_id)
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "items": []
            }
        
        items = self.repo.read_by_ms_drive_id(drive.id)
        return {
            "message": f"Found {len(items)} linked items",
            "status_code": 200,
            "items": [item.to_dict() for item in items]
        }

    def read_by_public_id(self, public_id: str) -> Optional[MsDriveItem]:
        """
        Read a linked MsDriveItem by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_item_id(self, item_id: str) -> Optional[MsDriveItem]:
        """
        Read a linked MsDriveItem by MS Graph item ID.
        """
        return self.repo.read_by_item_id(item_id)

    def unlink_item(self, public_id: str) -> dict:
        """
        Unlink a DriveItem by removing it from the database.
        
        Args:
            public_id: The public ID of the linked item to remove
        
        Returns:
            Dict with status_code, message, and deleted item
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked item not found",
                "status_code": 404,
                "item": None
            }
        
        try:
            deleted = self.repo.delete_by_public_id(public_id)
            
            if deleted:
                return {
                    "message": "Item unlinked successfully",
                    "status_code": 200,
                    "item": deleted.to_dict()
                }
            else:
                return {
                    "message": "Failed to unlink item",
                    "status_code": 500,
                    "item": None
                }
        except Exception as e:
            logger.exception("Error unlinking item")
            return {
                "message": f"Error unlinking item: {str(e)}",
                "status_code": 500,
                "item": None
            }

    def refresh_item(self, public_id: str) -> dict:
        """
        Refresh a linked item by fetching latest data from MS Graph.
        
        Args:
            public_id: The public ID of the linked item to refresh
        
        Returns:
            Dict with status_code, message, and refreshed item
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked item not found",
                "status_code": 404,
                "item": None
            }
        
        # Get the linked drive
        drive = self.drive_repo.read_by_public_id(str(existing.ms_drive_id))
        if not drive:
            # Try reading by internal ID
            drives = self.drive_repo.read_all()
            drive = next((d for d in drives if d.id == existing.ms_drive_id), None)
        
        if not drive:
            return {
                "message": "Linked drive not found",
                "status_code": 404,
                "item": None
            }
        
        # Fetch fresh data from MS Graph
        graph_result = graph_get_drive_item(drive.drive_id, existing.item_id)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch item from MS Graph"),
                "status_code": graph_result.get("status_code", 500),
                "item": None
            }
        
        item_data = graph_result.get("item")
        if not item_data:
            return {
                "message": "No item data returned from MS Graph",
                "status_code": 500,
                "item": None
            }
        
        # Update local record
        try:
            updated = self.repo.update_by_public_id(
                public_id=public_id,
                ms_drive_id=existing.ms_drive_id,
                item_id=item_data.get("item_id"),
                parent_item_id=item_data.get("parent_item_id"),
                name=item_data.get("name"),
                item_type=item_data.get("item_type"),
                size=item_data.get("size"),
                mime_type=item_data.get("mime_type"),
                web_url=item_data.get("web_url"),
                graph_created_datetime=item_data.get("graph_created_datetime"),
                graph_modified_datetime=item_data.get("graph_modified_datetime"),
            )
            
            if updated:
                return {
                    "message": "Item refreshed successfully",
                    "status_code": 200,
                    "item": updated.to_dict()
                }
            else:
                return {
                    "message": "Failed to refresh item",
                    "status_code": 500,
                    "item": None
                }
        except Exception as e:
            logger.exception("Error refreshing item")
            return {
                "message": f"Error refreshing item: {str(e)}",
                "status_code": 500,
                "item": None
            }
