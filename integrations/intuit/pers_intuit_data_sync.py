from dataclasses import dataclass
from datetime import datetime
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class DataSync:
    """Represents a data sync record in the system."""
    data_sync_guid: str
    data_source_name: str
    last_update_datetime: datetime

    @classmethod
    def from_db_row(cls, row):
        """
        Create a DataSync object from a database row.
        """
        if not row:
            return None

        return cls(
            data_sync_guid=row.data_sync_guid,
            data_source_name=row.data_source_name,
            last_update_datetime=row.last_update_datetime
        )


def read_intuit_data_sync_by_data_source_name(data_source_name):
    """
    Read the Intuit DataSync by data source name.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadDataSyncByDataSourceName (?)}"
                row = cursor.execute(sql, data_source_name).fetchone()

                if row:
                    return SuccessResponse(
                        message="Intuit Data Sync found",
                        data={
                            "sync_record": row,
                            "count": len(row),
                            "timestamp": datetime.now().isoformat()
                        },
                        status_code=200
                    )

                return BusinessResponse(
                    message="Intuit Data Sync not found",
                    status_code=404
                )

        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to read data sync: {str(err)}") from err


def update_intuit_data_sync_by_data_source_name(data_source_name, last_update_datetime):
    resp = {}
    sql = (
        '''
        UPDATE intuit.DataSync
        SET LastUpdateDatetime=?
        WHERE DataSourceName=?;
        '''
    )
    try:
        cnxn = pers_database.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, last_update_datetime, data_source_name).rowcount
        if count == 1:
            resp = {
                "message": "Intuit DataSync for {} has been successfully updated.".format(data_source_name),
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Intuit DataSync for {} has NOT been successfully updated.".format(data_source_name),
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = pers_database.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 500
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp
