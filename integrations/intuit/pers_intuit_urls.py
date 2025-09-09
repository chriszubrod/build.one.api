from dataclasses import dataclass
from datetime import datetime
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class IntuitUrls:
    """Represents an Intuit Urls record in the system."""
    urls_guid: str
    created_datetime: datetime
    modified_datetime: datetime
    name: str
    slug: str

    @classmethod
    def from_db_row(cls, row):
        """
        Create an IntuitUrls object from a database row.
        """
        return cls(
            urls_guid=getattr(row, 'UrlsGUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            name=getattr(row, 'Name'),
            slug=getattr(row, 'Slug')
        )


def read_intuit_urls():
    """
    Read the Intuit Urls record from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "SELECT * FROM intuit.Urls;"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[IntuitUrls.from_db_row(row) for row in rows],
                        message="Intuit URLs found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Intuit URLs not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except pyodbc.DatabaseError as err:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Intuit URLs: {str(err)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
