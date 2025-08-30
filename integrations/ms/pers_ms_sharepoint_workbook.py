"""
This module contains the persistence layer for the BuildOne vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class SharePointWorkbook:
    """Represents a SharePoint site in the system."""
    workbook_id: Optional[int] = None
    workbook_guid: Optional[str] = None
    workbook_created_datetime: Optional[datetime] = None
    workbook_modified_datetime: Optional[datetime] = None
    workbook_ms_graph_download_url: Optional[str] = None
    workbook_c_tag: Optional[str] = None
    workbook_ms_created_datetime: Optional[datetime] = None
    workbook_e_tag: Optional[str] = None
    workbook_file_hash_quick_x_or_hash: Optional[str] = None
    workbook_file_mime_type: Optional[str] = None
    workbook_ms_id: Optional[str] = None
    workbook_last_modified_datetime: Optional[datetime] = None
    workbook_name: Optional[str] = None
    workbook_ms_parent_id: Optional[str] = None
    workbook_shared_scope: Optional[str] = None
    workbook_size: Optional[int] = None
    workbook_web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> 'SharePointWorkbook':
        """Creates a SharePointWorkbook object from a database row."""
        if not row:
            return None

        return cls(
            workbook_id=getattr(row, 'Id'),
            workbook_guid=getattr(row, 'GUID'),
            workbook_created_datetime=getattr(row, 'CreatedDatetime'),
            workbook_modified_datetime=getattr(row, 'ModifiedDatetime'),
            workbook_ms_graph_download_url=getattr(row, 'MsGraphDownloadUrl'),
            workbook_c_tag=getattr(row, 'CTag'),
            workbook_ms_created_datetime=getattr(row, 'MsCreatedDatetime'),
            workbook_e_tag=getattr(row, 'ETag'),
            workbook_file_hash_quick_x_or_hash=getattr(row, 'FileHashQuickXorHash'),
            workbook_file_mime_type=getattr(row, 'FileMimeType'),
            workbook_ms_id=getattr(row, 'MsId'),
            workbook_last_modified_datetime=getattr(row, 'LastModifiedDatetime'),
            workbook_name=getattr(row, 'Name'),
            workbook_ms_parent_id=getattr(row, 'MsParentId'),
            workbook_shared_scope=getattr(row, 'SharedScope'),
            workbook_size=getattr(row, 'Size'),
            workbook_web_url=getattr(row, 'WebUrl')
        )


def create_sharepoint_workbook(sharepoint_workbook: SharePointWorkbook):
    """
    Creates a SharePoint workbook in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsSharePointWorkbook (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_workbook.workbook_created_datetime,
                    sharepoint_workbook.workbook_modified_datetime,
                    sharepoint_workbook.workbook_ms_graph_download_url,
                    sharepoint_workbook.workbook_c_tag,
                    sharepoint_workbook.workbook_ms_created_datetime,
                    sharepoint_workbook.workbook_e_tag,
                    sharepoint_workbook.workbook_file_hash_quick_x_or_hash,
                    sharepoint_workbook.workbook_file_mime_type,
                    sharepoint_workbook.workbook_ms_id,
                    sharepoint_workbook.workbook_last_modified_datetime,
                    sharepoint_workbook.workbook_name,
                    sharepoint_workbook.workbook_ms_parent_id,
                    sharepoint_workbook.workbook_shared_scope,
                    sharepoint_workbook.workbook_size,
                    sharepoint_workbook.workbook_web_url
                ).rowcount

                if rowcount > 0:
                    return PersistenceResponse(
                        data=sharepoint_workbook,
                        message="SharePoint workbook created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint workbook not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to create SharePoint workbook: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_workbooks():
    """
    Retrieves all SharePoint workbooks from the database.

    Returns:
        List[SharePointWorkbook]: A list of SharePointWorkbook objects
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointWorkbooks}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[SharePointWorkbook.from_db_row(row) for row in rows],
                        message="SharePoint workbooks found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="No SharePoint workbooks found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint workbooks: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_workbook_by_workbook_id(workbook_id: int):
    """
    Retrieves a SharePoint workbook by workbook id from the database.

    Returns:
        SharePointWorkbook: A SharePointWorkbook object
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointWorkbookById(?)}"
                row = cursor.execute(sql, workbook_id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=SharePointWorkbook.from_db_row(row),
                        message="SharePoint workbook found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint workbook not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint workbook: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_workbook_by_url(url: str):
    """
    Retrieves a SharePoint workbook by url from the database.

    Returns:
        SharePointWorkbook: A SharePointWorkbook object
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointWorkbookByUrl(?)}"
                row = cursor.execute(sql, url).fetchone()

                if row:
                    return PersistenceResponse(
                        data=SharePointWorkbook.from_db_row(row),
                        message="SharePoint workbook found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint workbook not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint workbook: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_sharepoint_workbook_by_workbook_id(sharepoint_workbook: SharePointWorkbook):
    """
    Updates a SharePoint workbook by workbook id in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMsSharePointWorkbookByWorkbookId (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_workbook.workbook_modified_datetime,
                    sharepoint_workbook.workbook_c_tag,
                    sharepoint_workbook.workbook_ms_created_datetime,
                    sharepoint_workbook.workbook_e_tag,
                    sharepoint_workbook.workbook_file_hash_quick_x_or_hash,
                    sharepoint_workbook.workbook_file_mime_type,
                    sharepoint_workbook.workbook_ms_id,
                    sharepoint_workbook.workbook_last_modified_datetime,
                    sharepoint_workbook.workbook_name,
                    sharepoint_workbook.workbook_ms_parent_id,
                    sharepoint_workbook.workbook_shared_scope,
                    sharepoint_workbook.workbook_size,
                    sharepoint_workbook.workbook_web_url
                ).rowcount

                if rowcount > 0:
                    return PersistenceResponse(
                        data=sharepoint_workbook,
                        message="SharePoint workbook updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint workbook not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to update SharePoint workbook: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
