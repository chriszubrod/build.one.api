from dataclasses import dataclass
from datetime import datetime
import pyodbc

from persistence import pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


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
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "SELECT * FROM intuit.Urls;"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return SuccessResponse(
                        message="Intuit URLs found",
                        data=[IntuitUrls.from_db_row(row) for row in rows],
                        status_code=200
                    )
                else:
                    return BusinessResponse(message="Intuit URLs not found", status_code=404)
        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to read Intuit URLs: {str(err)}") from err
