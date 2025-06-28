"""
This module contains the persistence layer for the BuildOne vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class SharePointFile:
    """Represents a SharePoint file in the system."""
    file_id: Optional[int] = None
    file_guid: Optional[str] = None
    file_created_datetime: Optional[datetime] = None
    file_modified_datetime: Optional[datetime] = None
    file_ms_graph_download_url: Optional[str] = None
    file_c_tag: Optional[str] = None
    file_ms_created_datetime: Optional[datetime] = None
    file_e_tag: Optional[str] = None
    file_hash_quick_h_or_hash: Optional[str] = None
    file_mime_type: Optional[str] = None
    file_ms_id: Optional[str] = None
    file_last_modified_datetime: Optional[datetime] = None
    file_name: Optional[str] = None
    file_ms_parent_id: Optional[str] = None
    file_shared_scope: Optional[str] = None
    file_size: Optional[int] = None
    file_web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> 'SharePointFile':
        """Creates a SharePointFile object from a database row."""
        if not row:
            return None

        return cls(
            file_id=getattr(row, 'Id'),
            file_guid=getattr(row, 'GUID'),
            file_created_datetime=getattr(row, 'CreatedDatetime'),
            file_modified_datetime=getattr(row, 'ModifiedDatetime'),
            file_ms_graph_download_url=getattr(row, 'MsGraphDownloadUrl'),
            file_c_tag=getattr(row, 'CTag'),
            file_ms_created_datetime=getattr(row, 'MsCreatedDatetime'),
            file_e_tag=getattr(row, 'ETag'),
            file_hash_quick_h_or_hash=getattr(row, 'FileHashQuickXorHash'),
            file_mime_type=getattr(row, 'FileMimeType'),
            file_ms_id=getattr(row, 'MsId'),
            file_last_modified_datetime=getattr(row, 'LastModifiedDatetime'),
            file_name=getattr(row, 'Name'),
            file_ms_parent_id=getattr(row, 'MsParentId'),
            file_shared_scope=getattr(row, 'SharedScope'),
            file_size=getattr(row, 'Size'),
            file_web_url=getattr(row, 'WebUrl')
        )


def create_sharepoint_file(sharepoint_file: SharePointFile):
    """
    Creates a SharePoint file in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsSharePointFile (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_file.file_ms_graph_download_url,
                    sharepoint_file.file_c_tag,
                    sharepoint_file.file_ms_created_datetime,
                    sharepoint_file.file_e_tag,
                    sharepoint_file.file_hash_quick_h_or_hash,
                    sharepoint_file.file_mime_type,
                    sharepoint_file.file_ms_id,
                    sharepoint_file.file_last_modified_datetime,
                    sharepoint_file.file_name,
                    sharepoint_file.file_ms_parent_id,
                    sharepoint_file.file_shared_scope,
                    sharepoint_file.file_size,
                    sharepoint_file.file_web_url
                ).rowcount

                if rowcount > 0:
                    return PersistenceResponse(
                        data=sharepoint_file,
                        message="SharePoint file created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint file not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to create SharePoint file: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_files():
    """
    Retrieves all SharePoint files from the database.

    Returns:
        List[SharePointFile]: A list of SharePointFile objects
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointFiles}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[SharePointFile.from_db_row(row) for row in rows],
                        message="SharePoint files found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="No SharePoint files found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint files: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sharepoint_file_by_ms_id(ms_id: str) -> PersistenceResponse:
    """
    Retrieves a SharePoint file from the database by its Microsoft ID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointFileByMsId (?)}"
                row = cursor.execute(sql, ms_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=SharePointFile.from_db_row(row),
                        message="SharePoint file found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="No SharePoint file found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint file: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_sharepoint_file(sharepoint_file: SharePointFile):
    """
    Updates a SharePoint file in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMsSharePointFileByFileId (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_file.file_id,
                    sharepoint_file.file_ms_graph_download_url,
                    sharepoint_file.file_c_tag,
                    sharepoint_file.file_ms_created_datetime,
                    sharepoint_file.file_e_tag,
                    sharepoint_file.file_hash_quick_h_or_hash,
                    sharepoint_file.file_mime_type,
                    sharepoint_file.file_ms_id,
                    sharepoint_file.file_last_modified_datetime,
                    sharepoint_file.file_name,
                    sharepoint_file.file_ms_parent_id,
                    sharepoint_file.file_shared_scope,
                    sharepoint_file.file_size,
                    sharepoint_file.file_web_url
                ).rowcount

                if rowcount > 0:
                    return PersistenceResponse(
                        data=sharepoint_file,
                        message="SharePoint file updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint file not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to update SharePoint file: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
