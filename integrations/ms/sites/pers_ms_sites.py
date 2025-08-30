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
class MsSite:
    """Represents a site in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    odata_context: Optional[str] = None
    description: Optional[str] = None
    display_name: Optional[str] = None
    site_id: Optional[str] = None
    last_modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    root: Optional[str] = None
    site_collection_host_name: Optional[str] = None
    web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['MsSite']:
        """Creates a MsSite instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            odata_context=getattr(row, 'ODataContext', None),
            description=getattr(row, 'Description', None),
            display_name=getattr(row, 'DisplayName', None),
            site_id=getattr(row, 'SiteId', None),
            last_modified_datetime=getattr(row, 'LastModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            root=getattr(row, 'Root', None),
            site_collection_host_name=getattr(row, 'SiteCollectionHostName', None),
            web_url=getattr(row, 'WebUrl', None)
        )


def read_ms_sites() -> PersistenceResponse:
    """
    Retrieves all sites from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointSites}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MsSite.from_db_row(row) for row in rows],
                        message="MsSites found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No MsSites found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read MsSites: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

