"""
This module contains the persistence layer for the Map Project Sharepoint Workbook.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pyodbc

from integrations.adapters import register_adapter
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@register_adapter
@dataclass
class MapProjectToSharepointWorkbook:
    """Represents a Map Project Sharepoint Workbook in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    project_id: Optional[int] = None
    ms_sharepoint_workbook_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapProjectToSharepointWorkbook':
        """Creates a MapProjectToSharepointWorkbook object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            project_id=getattr(row, 'ProjectId'),
            ms_sharepoint_workbook_id=getattr(row, 'MsSharePointWorkbookId'),
        )


def read_map_project_to_sharepoint_workbook_by_project_id(
        project_id: int
    ) -> PersistenceResponse:
    """
    Retrieves all Map Project Sharepoint Workbooks by project id from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectSharePointWorkbookByProjectId(?)}"
                rows = cursor.execute(sql, project_id).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapProjectToSharepointWorkbook.from_db_row(row) for row in rows],
                        message="Map Project Sharepoint Workbooks found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Project Sharepoint Workbooks found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Project Sharepoint Workbooks: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def create_map_project_to_sharepoint_workbook(
        project_id: int,
        ms_sharepoint_workbook_id: int
    ) -> PersistenceResponse:
    """Creates a mapping row in map.ProjectSharePointWorkbook."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateProjectSharePointWorkbook(?, ?)}"
                rowcount = cursor.execute(sql, int(project_id), int(ms_sharepoint_workbook_id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Project SharePoint Workbook created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Project SharePoint Workbook not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Project SharePoint Workbook: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
