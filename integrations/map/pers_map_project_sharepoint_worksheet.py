"""
This module contains the persistence layer for the Map Project Sharepoint Worksheet.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class MapProjectSharepointWorksheet:
    """Represents a Map Project Sharepoint Worksheet in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    project_id: Optional[int] = None
    ms_sharepoint_worksheet_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapProjectSharepointWorksheet':
        """Creates a MapProjectSharepointWorksheet object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            project_id=getattr(row, 'ProjectId'),
            ms_sharepoint_worksheet_id=getattr(row, 'MsSharePointWorksheetId'),
        )


def read_map_project_sharepoint_worksheet_by_project_id(
        project_id: int
    ) -> PersistenceResponse:
    """
    Retrieves all Map Project Sharepoint Worksheets by project id from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectSharePointWorksheetByProjectId(?)}"
                rows = cursor.execute(sql, project_id).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapProjectSharepointWorksheet.from_db_row(row) for row in rows],
                        message="Map Project Sharepoint Worksheets found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Project Sharepoint Worksheets found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Project Sharepoint Worksheets: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
