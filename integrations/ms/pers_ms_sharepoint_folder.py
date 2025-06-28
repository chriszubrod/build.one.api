"""
This module contains the persistence layer for the BuildOne vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

from persistence import pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class SharePointFolder:
    """Represents a SharePoint site in the system."""
    folder_id: Optional[int] = None
    folder_guid: Optional[str] = None
    folder_created_datetime: Optional[datetime] = None
    folder_modified_datetime: Optional[datetime] = None
    folder_c_tag: Optional[str] = None
    folder_ms_created_datetime: Optional[datetime] = None
    folder_e_tag: Optional[str] = None
    folder_folder_child_count: Optional[int] = None
    folder_ms_id: Optional[str] = None
    folder_last_modified_datetime: Optional[datetime] = None
    folder_name: Optional[str] = None
    folder_ms_parent_id: Optional[str] = None
    folder_shared_scope: Optional[str] = None
    folder_size: Optional[int] = None
    folder_web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> 'SharePointFolder':
        """Creates a SharePointFolder object from a database row."""
        if not row:
            return None

        return cls(
            folder_id=getattr(row, 'Id'),
            folder_guid=getattr(row, 'GUID'),
            folder_created_datetime=getattr(row, 'CreatedDatetime'),
            folder_modified_datetime=getattr(row, 'ModifiedDatetime'),
            folder_c_tag=getattr(row, 'CTag'),
            folder_ms_created_datetime=getattr(row, 'MsCreatedDatetime'),
            folder_e_tag=getattr(row, 'ETag'),
            folder_folder_child_count=getattr(row, 'FolderChildCount'),
            folder_ms_id=getattr(row, 'MsId'),
            folder_last_modified_datetime=getattr(row, 'LastModifiedDatetime'),
            folder_name=getattr(row, 'Name'),
            folder_ms_parent_id=getattr(row, 'MsParentId'),
            folder_shared_scope=getattr(row, 'SharedScope'),
            folder_size=getattr(row, 'Size'),
            folder_web_url=getattr(row, 'WebUrl')
        )


def create_sharepoint_folder(sharepoint_folder: SharePointFolder):
    """
    Creates a SharePoint folder in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsSharePointFolder (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_folder.folder_created_datetime,
                    sharepoint_folder.folder_modified_datetime,
                    sharepoint_folder.folder_c_tag,
                    sharepoint_folder.folder_ms_created_datetime,
                    sharepoint_folder.folder_e_tag,
                    sharepoint_folder.folder_folder_child_count,
                    sharepoint_folder.folder_ms_id,
                    sharepoint_folder.folder_last_modified_datetime,
                    sharepoint_folder.folder_name,
                    sharepoint_folder.folder_ms_parent_id,
                    sharepoint_folder.folder_shared_scope,
                    sharepoint_folder.folder_size,
                    sharepoint_folder.folder_web_url
                ).rowcount

                if rowcount > 0:
                    return SuccessResponse(message="SharePoint folder created", status_code=200)

                return BusinessResponse(message="SharePoint folder not created", status_code=400)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to create SharePoint folder: {str(e)}") from e


def read_sharepoint_folders():
    """
    Retrieves all SharePoint folders from the database.

    Returns:
        List[SharePointFolder]: A list of SharePointFolder objects
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointFolders}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return SuccessResponse(
                        message="SharePoint folders found",
                        data=[SharePointFolder.from_db_row(row) for row in rows],
                        status_code=200
                    )

                return BusinessResponse(message="No SharePoint folders found", status_code=404)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read SharePoint folders: {str(e)}") from e


def read_sharepoint_folder_by_folder_id(folder_id: int):
    """
    Retrieves a SharePoint folder by folder id from the database.

    Returns:
        SharePointFolder: A SharePointFolder object
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointFolderByFolderId(?)}"
                row = cursor.execute(sql, folder_id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=SharePointFolder.from_db_row(row),
                        message="SharePoint folder found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint folder not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint folder: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_folder_by_url(url: str):
    """
    Retrieves a SharePoint folder by url from the database.

    Returns:
        SharePointFolder: A SharePointFolder object
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointFolderByUrl(?)}"
                row = cursor.execute(sql, url).fetchone()

                if row:
                    return SuccessResponse(
                        message="SharePoint folder found",
                        data=SharePointFolder.from_db_row(row),
                        status_code=200
                    )

                return BusinessResponse(message="SharePoint folder not found", status_code=404)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read SharePoint folder: {str(e)}") from e


def update_sharepoint_folder_by_folder_id(sharepoint_folder: SharePointFolder):
    """
    Updates a SharePoint folder by folder id in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMsSharePointFolderByFolderId (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_folder.folder_modified_datetime,
                    sharepoint_folder.folder_c_tag,
                    sharepoint_folder.folder_ms_created_datetime,
                    sharepoint_folder.folder_e_tag,
                    sharepoint_folder.folder_folder_child_count,
                    sharepoint_folder.folder_ms_id,
                    sharepoint_folder.folder_last_modified_datetime,
                    sharepoint_folder.folder_name,
                    sharepoint_folder.folder_ms_parent_id,
                    sharepoint_folder.folder_shared_scope,
                    sharepoint_folder.folder_size,
                    sharepoint_folder.folder_web_url
                ).rowcount

                if rowcount > 0:
                    return SuccessResponse(message="SharePoint folder updated", status_code=200)

                return BusinessResponse(message="SharePoint folder not updated", status_code=400)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to update SharePoint folder: {str(e)}") from e
