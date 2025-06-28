"""
This module contains the persistence layer for the Map Attachment Sharepoint File.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


@dataclass
class MapAttachmentSharepointFile:
    """Represents a Map Attachment Sharepoint File in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    bill_line_item_attachment_id: Optional[int] = None
    ms_sharepoint_file_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapAttachmentSharepointFile':
        """Creates a MapAttachmentSharepointFile object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            bill_line_item_attachment_id=getattr(row, 'BillLineItemAttachmentId'),
            ms_sharepoint_file_id=getattr(row, 'MsSharePointFileId'),
        )


def create_map_attachment_sharepoint_file(
        bill_line_item_attachment_id: int,
        ms_sharepoint_file_id: int
    ) -> PersistenceResponse:
    """
    Creates a Map Attachment Sharepoint File in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                print('bill_line_item_attachment_id')
                print(type(bill_line_item_attachment_id))
                print('ms_sharepoint_file_id')
                print(type(ms_sharepoint_file_id))
                sql = "{CALL CreateAttachmentSharePointFile (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    bill_line_item_attachment_id,
                    ms_sharepoint_file_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Attachment Sharepoint File created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Attachment Sharepoint File not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Attachment Sharepoint File: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_attachment_sharepoint_files() -> PersistenceResponse:
    """
    Retrieves all Map Attachment Sharepoint Files from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAttachmentSharePointFile}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapAttachmentSharepointFile.from_db_row(row) for row in rows],
                        message="Map Attachment Sharepoint Files found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Attachment Sharepoint Files found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Attachment Sharepoint Files: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_attachment_sharepoint_file_by_attachment_id_file_id(bill_line_item_attachment_id: int, ms_sharepoint_file_id: int) -> PersistenceResponse:
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAttachmentSharePointFileByAttachmentIdSharePointFileId (?, ?)}"
                rows = cursor.execute(sql, bill_line_item_attachment_id, ms_sharepoint_file_id).fetchone()

                if rows:
                    return PersistenceResponse(
                        data=[MapAttachmentSharepointFile.from_db_row(row) for row in rows],
                        message="Map Attachment Sharepoint Files found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Attachment Sharepoint Files found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Attachment Sharepoint Files: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_attachment_sharepoint_file(map_attachment_sharepoint_file):
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateAttachmentSharePointFileById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    map_attachment_sharepoint_file.id,
                    map_attachment_sharepoint_file.bill_line_item_attachment_id,
                    map_attachment_sharepoint_file.ms_sharepoint_file_id
                ).rowcount
                cnxn.commit()

                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Attachment Sharepoint File updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Attachment Sharepoint File not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Attachment Sharepoint File: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
