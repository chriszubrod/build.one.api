from dataclasses import dataclass
from datetime import datetime, timedelta
from shared.database import get_db_connection
from shared.response import PersistenceResponse
import pyodbc


@dataclass
class IntuitAuth:
    """Represents an Intuit Auth record in the system."""
    auth_guid: str
    created_datetime: datetime
    modified_datetime: datetime
    code: str
    realm_id: str
    token_type: str
    id_token: str
    access_token: str
    expires_in: int
    refresh_token: str
    x_refresh_token_expires_in: int

    def is_access_token_expired(self) -> bool:
        """Check if the access token is expired."""
        expiration_datetime = self.modified_datetime + timedelta(
            seconds=self.expires_in
        )
        return datetime.now() >= expiration_datetime

    def is_refresh_token_expired(self) -> bool:
        """Check if the refresh token is expired."""
        expiration_datetime = self.modified_datetime + timedelta(
            seconds=self.x_refresh_token_expires_in
        )
        return datetime.now() >= expiration_datetime

    @classmethod
    def from_db_row(cls, row):
        """
        Create an IntuitAuth object from a database row.
        """
        return cls(
            auth_guid=getattr(row, 'AuthGUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            code=getattr(row, 'Code'),
            realm_id=getattr(row, 'RealmId'),
            token_type=getattr(row, 'TokenType'),
            id_token=getattr(row, 'IdToken'),
            access_token=getattr(row, 'AccessToken'),
            expires_in=getattr(row, 'ExpiresIn'),
            refresh_token=getattr(row, 'RefreshToken'),
            x_refresh_token_expires_in=getattr(row, 'XRefreshTokenExpiresIn')
        )


def read_db_intuit_auth():
    """
    Read the Intuit Auth record from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "SELECT * FROM intuit.Auth;"
                row = cursor.execute(sql).fetchone()

                if row:
                    return SuccessResponse(
                        message="Intuit Auth found",
                        data=IntuitAuth.from_db_row(row),
                        status_code=200
                    )
                else:
                    return PersistenceResponse(
                        message="Intuit Auth not found",
                        status_code=404
                    )

        except pyodbc.DatabaseError as err:
            return DatabaseError(message="Failed to read Intuit Auth", status_code=500)


def create_db_intuit_auth(now, code, realmId):
    resp = {}
    sql = (
        '''
        INSERT INTO intuit.Auth (CreatedDatetime, ModifiedDatetime, Code, RealmId)
        VALUES (?, ?, ?, ?);
        '''
    )
    try:
        cnxn = pers_database.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, now, now, code, realmId).rowcount
        if count == 1:
            resp = {
                "message": "Auth has been successfully created.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Auth has NOT been successfully created.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = pers_database.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def update_db_intuit_auth_code_realmid_by_authguid(now, authguid, code, realmId):
    resp = {}
    sql = (
        '''
        UPDATE intuit.Auth
        SET ModifiedDatetime=?, Code=?, RealmId=?
        WHERE AuthGUID=?;
        '''
    )
    try:
        cnxn = pers_database.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, now, code, realmId, authguid).rowcount
        if count == 1:
            resp = {
                "message": "Auth has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Auth has NOT been successfully updated.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = pers_database.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def update_db_intuit_auth_by_authguid(now, tokentype, idtoken, accesstoken, expiresin, refreshtoken, xrefreshtokenexpiresin, authguid):
    resp = {}
    sql = (
        '''
        UPDATE intuit.Auth
        SET ModifiedDatetime=?, TokenType=?, IdToken=?, AccessToken=?, ExpiresIn=?, RefreshToken=?, XRefreshTokenExpiresIn=?
        WHERE AuthGUID=?;
        '''
    )
    try:
        cnxn = pers_database.open_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, now, tokentype, idtoken, accesstoken, expiresin, refreshtoken, xrefreshtokenexpiresin, authguid).rowcount
        if count == 1:
            resp = {
                "message": "Auth has been successfully updated.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Auth has NOT been successfully updated.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = pers_database.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp


def delete_auth_by_authguid(authguid):
    resp = {}
    sql = (
        '''
        DELETE FROM qbo.Auth
        WHERE AuthGUID=?;
        '''
    )
    try:
        cnxn = pers_database.get_db_cnxn()
        cnxn.autocommit = False
        crsr = cnxn.cursor()
        count = crsr.execute(sql, authguid).rowcount
        if count == 1:
            resp = {
                "message": "Auth has been successfully deleted.",
                "rowcount": count,
                "status_code": 201
            }
        else:
            resp = {
                "message": "Auth has NOT been successfully deleted.",
                "rowcount": 0,
                "status_code": 501
            }
    except pyodbc.DatabaseError as err:
        cnxn.rollback()
        err = pers_database.exception_handler(error=err)
        resp = {
            "message": err["description"],
            "rowcount": 0,
            "status_code": 501
        }
    else:
        cnxn.commit()
    finally:
        cnxn.autocommit = True
        return resp
