# Python Standard Library Imports
import logging
from typing import Any, Dict, List, Optional

# Local Imports
from workflows.workflow.business.capabilities.base import Capability, CapabilityResult, with_retry

logger = logging.getLogger(__name__)


class SharePointCapabilities(Capability):
    """
    SharePoint capabilities using MS Graph API.
    
    Provides file upload and Excel worksheet operations.
    """
    
    @property
    def name(self) -> str:
        return "sharepoint"
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def upload_file(
        self,
        access_token: str,
        drive_id: str,
        folder_path: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> CapabilityResult:
        """
        Upload a file to a SharePoint folder.
        
        Args:
            access_token: MS Graph access token
            drive_id: SharePoint drive ID
            folder_path: Path within the drive (e.g., "Invoices/2026")
            filename: Name for the uploaded file
            content: File content bytes
            content_type: MIME type
            
        Returns:
            CapabilityResult with file URL and metadata
        """
        self._log_call(
            "upload_file",
            drive_id=drive_id,
            folder_path=folder_path,
            filename=filename,
            content_size=len(content),
        )
        
        try:
            from integrations.ms.sharepoint.external import client as sp_client
            
            # Get or create the folder
            folder_result = sp_client.get_folder_by_path(
                access_token=access_token,
                drive_id=drive_id,
                folder_path=folder_path,
            )
            
            if folder_result.get("status_code") == 404:
                # Create folder
                folder_result = sp_client.create_folder_path(
                    access_token=access_token,
                    drive_id=drive_id,
                    folder_path=folder_path,
                )
            
            if folder_result.get("status_code") not in (200, 201):
                return CapabilityResult.fail(
                    error=f"Failed to get/create folder: {folder_result.get('message')}",
                )
            
            folder_id = folder_result.get("item", {}).get("id")
            
            # Upload the file
            upload_result = sp_client.upload_small_file(
                access_token=access_token,
                drive_id=drive_id,
                parent_item_id=folder_id,
                filename=filename,
                content=content,
                content_type=content_type,
            )
            
            if upload_result.get("status_code") not in (200, 201):
                return CapabilityResult.fail(
                    error=f"Failed to upload file: {upload_result.get('message')}",
                )
            
            item = upload_result.get("item", {})
            result = CapabilityResult.ok(
                data={
                    "item_id": item.get("id"),
                    "name": item.get("name"),
                    "web_url": item.get("webUrl"),
                    "size": item.get("size"),
                },
            )
            self._log_result("upload_file", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "upload_file")
    
    def upload_to_project_folder(
        self,
        access_token: str,
        project_id: int,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        subfolder: Optional[str] = None,
    ) -> CapabilityResult:
        """
        Upload a file to a project's SharePoint folder.
        
        Uses project configuration to determine the drive and folder.
        
        Args:
            access_token: MS Graph access token
            project_id: Project ID
            filename: Name for the uploaded file
            content: File content bytes
            content_type: MIME type
            subfolder: Optional subfolder within project folder
            
        Returns:
            CapabilityResult with file URL and metadata
        """
        self._log_call(
            "upload_to_project_folder",
            project_id=project_id,
            filename=filename,
            subfolder=subfolder,
        )
        
        try:
            # Get project SharePoint configuration
            from entities.project.business.service import ProjectService
            project_service = ProjectService()
            
            project_result = project_service.read_by_id(project_id)
            if project_result.get("status_code") != 200:
                return CapabilityResult.fail(
                    error="Project not found",
                )
            
            project = project_result.get("project", {})
            drive_id = project.get("sharepoint_drive_id")
            folder_path = project.get("sharepoint_folder_path", "")
            
            if not drive_id:
                return CapabilityResult.fail(
                    error="Project does not have SharePoint configured",
                )
            
            if subfolder:
                folder_path = f"{folder_path}/{subfolder}" if folder_path else subfolder
            
            return self.upload_file(
                access_token=access_token,
                drive_id=drive_id,
                folder_path=folder_path,
                filename=filename,
                content=content,
                content_type=content_type,
            )
            
        except Exception as e:
            return self._handle_error(e, "upload_to_project_folder")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def append_worksheet_rows(
        self,
        access_token: str,
        drive_id: str,
        item_id: str,
        worksheet_name: str,
        rows: List[List[Any]],
    ) -> CapabilityResult:
        """
        Append rows to an Excel worksheet.
        
        Args:
            access_token: MS Graph access token
            drive_id: SharePoint drive ID
            item_id: Excel file item ID
            worksheet_name: Name of the worksheet
            rows: List of rows to append (each row is a list of cell values)
            
        Returns:
            CapabilityResult with row count
        """
        self._log_call(
            "append_worksheet_rows",
            drive_id=drive_id,
            item_id=item_id,
            worksheet_name=worksheet_name,
            row_count=len(rows),
        )
        
        try:
            from integrations.ms.sharepoint.external import client as sp_client
            
            result = sp_client.append_excel_rows(
                access_token=access_token,
                drive_id=drive_id,
                item_id=item_id,
                worksheet_name=worksheet_name,
                values=rows,
            )
            
            if result.get("status_code") not in (200, 201):
                return CapabilityResult.fail(
                    error=f"Failed to append rows: {result.get('message')}",
                )
            
            cap_result = CapabilityResult.ok(
                data={
                    "rows_added": len(rows),
                    "range": result.get("range"),
                },
            )
            self._log_result("append_worksheet_rows", cap_result)
            return cap_result
            
        except Exception as e:
            return self._handle_error(e, "append_worksheet_rows")
    
    def append_to_project_worksheet(
        self,
        access_token: str,
        project_id: int,
        rows: List[List[Any]],
        worksheet_name: Optional[str] = None,
    ) -> CapabilityResult:
        """
        Append rows to a project's Excel worksheet.
        
        Uses project configuration to determine the workbook and sheet.
        Note: No retry decorator since it calls append_worksheet_rows which has retry.
        
        Args:
            access_token: MS Graph access token
            project_id: Project ID
            rows: List of rows to append
            worksheet_name: Optional specific worksheet (defaults to project config)
            
        Returns:
            CapabilityResult with row count
        """
        self._log_call(
            "append_to_project_worksheet",
            project_id=project_id,
            row_count=len(rows),
        )
        
        try:
            from entities.project.business.service import ProjectService
            project_service = ProjectService()
            
            project_result = project_service.read_by_id(project_id)
            if project_result.get("status_code") != 200:
                return CapabilityResult.fail(error="Project not found")
            
            project = project_result.get("project", {})
            drive_id = project.get("sharepoint_drive_id")
            workbook_id = project.get("sharepoint_workbook_id")
            default_worksheet = project.get("sharepoint_worksheet_name", "Sheet1")
            
            if not drive_id or not workbook_id:
                return CapabilityResult.fail(
                    error="Project does not have worksheet configured",
                )
            
            return self.append_worksheet_rows(
                access_token=access_token,
                drive_id=drive_id,
                item_id=workbook_id,
                worksheet_name=worksheet_name or default_worksheet,
                rows=rows,
            )
            
        except Exception as e:
            return self._handle_error(e, "append_to_project_worksheet")
