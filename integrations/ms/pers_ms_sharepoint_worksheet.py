"""
This module contains the persistence layer for the BuildOne vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


@dataclass
class SharePointWorksheet:
    """Represents a SharePoint site in the system."""
    worksheet_id: Optional[int] = None
    worksheet_guid: Optional[str] = None
    worksheet_created_datetime: Optional[datetime] = None
    worksheet_modified_datetime: Optional[datetime] = None
    worksheet_ms_o_data_id: Optional[str] = None
    worksheet_ms_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    worksheet_position: Optional[int] = None
    worksheet_visibility: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> 'SharePointWorksheet':
        """Creates a SharePointWorksheet object from a database row."""
        if not row:
            return None

        return cls(
            worksheet_id=getattr(row, 'Id'),
            worksheet_guid=getattr(row, 'GUID'),
            worksheet_created_datetime=getattr(row, 'CreatedDatetime'),
            worksheet_modified_datetime=getattr(row, 'ModifiedDatetime'),
            worksheet_ms_o_data_id=getattr(row, 'MsODataId'),
            worksheet_ms_id=getattr(row, 'MsId'),
            worksheet_name=getattr(row, 'Name'),
            worksheet_position=getattr(row, 'Position'),
            worksheet_visibility=getattr(row, 'Visibility')
        )


def create_sharepoint_worksheet(sharepoint_worksheet: SharePointWorksheet):
    """
    Creates a SharePoint worksheet in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsSharePointWorksheet (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_worksheet.worksheet_created_datetime,
                    sharepoint_worksheet.worksheet_modified_datetime,
                    sharepoint_worksheet.worksheet_ms_o_data_id,
                    sharepoint_worksheet.worksheet_ms_id,
                    sharepoint_worksheet.worksheet_name,
                    sharepoint_worksheet.worksheet_position,
                    sharepoint_worksheet.worksheet_visibility
                ).rowcount

                if rowcount > 0:
                    return SuccessResponse(message="SharePoint worksheet created", status_code=200)

                return BusinessResponse(message="SharePoint worksheet not created", status_code=400)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to create SharePoint worksheet: {str(e)}") from e


def read_sharepoint_worksheets():
    """
    Retrieves all SharePoint worksheets from the database.

    Returns:
        List[SharePointWorksheet]: A list of SharePointWorksheet objects
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointWorksheet}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return SuccessResponse(
                        message="SharePoint worksheets found",
                        data=[SharePointWorksheet.from_db_row(row) for row in rows],
                        status_code=200
                    )

                return BusinessResponse(message="No SharePoint worksheets found", status_code=404)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read SharePoint worksheets: {str(e)}") from e


def read_sharepoint_worksheet_by_worksheet_id(worksheet_id: int):
    """
    Retrieves a SharePoint worksheet by worksheet id from the database.

    Returns:
        SharePointWorksheet: A SharePointWorksheet object
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointWorksheetById(?)}"
                row = cursor.execute(sql, worksheet_id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=SharePointWorksheet.from_db_row(row),
                        message="SharePoint worksheet found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=None,
                    message="SharePoint worksheet not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read SharePoint worksheet: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

