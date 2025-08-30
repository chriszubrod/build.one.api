"""
Module for Microsoft Graph API Sites persistence layer.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class MsDrive:
    """Represents a drive in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    created_by: Optional[str] = None
    created_datetime: Optional[datetime] = None
    description: Optional[str] = None
    drive_id: Optional[str] = None
    drive_type: Optional[str] = None
    last_modified_by: Optional[str] = None
    last_modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    owner: Optional[str] = None
    quota: Optional[str] = None
    web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['MsDrive']:
        """Creates a MsDrive instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            created_by=getattr(row, 'CreatedBy', None),
            created_by_datetime=getattr(row, 'CreatedByDatetime', None),
            description=getattr(row, 'Description', None),
            drive_id=getattr(row, 'DriveId', None),
            drive_type=getattr(row, 'DriveType', None),
            last_modified_by=getattr(row, 'LastModifiedBy', None),
            last_modified_datetime=getattr(row, 'LastModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            owner=getattr(row, 'Owner', None),
            quota=getattr(row, 'Quota', None),
            web_url=getattr(row, 'WebUrl', None)
        )


@dataclass
class MsDriveItem:
    """Represents a drive item in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    c_tag: Optional[str] = None
    created_by: Optional[str] = None
    created_datetime: Optional[datetime] = None
    e_tag: Optional[str] = None
    file_system_info: Optional[str] = None
    folder: Optional[str] = None
    drive_item_id: Optional[str] = None
    last_modified_by: Optional[str] = None
    last_modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    parent_reference: Optional[str] = None
    shared: Optional[str] = None
    size: Optional[int] = None
    web_url: Optional[str] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['MsDriveItem']:
        """Creates a MsDriveItem instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            c_tag=getattr(row, 'CTag', None),
            created_by=getattr(row, 'CreatedBy', None),
            created_by_datetime=getattr(row, 'CreatedByDatetime', None),
            e_tag=getattr(row, 'ETag', None),
            file_system_info=getattr(row, 'FileSystemInfo', None),
            folder=getattr(row, 'Folder', None),
            drive_item_id=getattr(row, 'DriveItemId', None),
            last_modified_by=getattr(row, 'LastModifiedBy', None),
            last_modified_datetime=getattr(row, 'LastModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            parent_reference=getattr(row, 'ParentReference', None),
            shared=getattr(row, 'Shared', None),
            size=getattr(row, 'Size', None),
            web_url=getattr(row, 'WebUrl', None)
        )


def read_ms_drives() -> PersistenceResponse:
    """
    Retrieves all drives from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointDrives}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MsDrive.from_db_row(row) for row in rows],
                        message="MsDrives found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No MsDrives found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read MsDrives: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

