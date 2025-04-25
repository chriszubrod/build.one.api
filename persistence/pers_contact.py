"""
Module for contact.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse

@dataclass
class Contact:
    """Represents a contact in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    transaction_id: Optional[int] = None
    customer_id: Optional[int] = None
    user_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Contact']:
        """Creates a Contact instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            first_name=getattr(row, 'FirstName', None),
            last_name=getattr(row, 'LastName', None),
            email=getattr(row, 'Email', None),
            phone=getattr(row, 'Phone', None),
            transaction_id=getattr(row, 'TransactionId', None),
            customer_id=getattr(row, 'CustomerId', None),
            user_id=getattr(row, 'UserId', None)
        )

def create_contact(contact: Contact) -> PersistenceResponse:
    """
    Creates a contact in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateContact(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    contact.created_datetime,
                    contact.modified_datetime,
                    contact.first_name,
                    contact.last_name,
                    contact.email,
                    contact.phone
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Contact created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to create contact",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create contact: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_contacts() -> PersistenceResponse:
    """
    Retrieves all contacts from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadContacts()}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[Contact.from_db_row(row) for row in rows],
                        message="Contacts read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Contacts not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read contacts: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_contact_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a contact from the database by guid.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadContactByGuid(?)}"
                row = cursor.execute(sql, guid).fetchone()

                if row:
                    return PersistenceResponse(
                        data=Contact.from_db_row(row),
                        message="Contact read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Contact by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read contact by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_contact_by_email(email: str) -> PersistenceResponse:
    """
    Retrieves a contact from the database by email address.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadContactByEmail(?)}"
                row = cursor.execute(sql, email).fetchone()

                if row:
                    return PersistenceResponse(
                        data=Contact.from_db_row(row),
                        message="Contact read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Contact not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read contact by email: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_contact_by_user_id(user_id: int) -> PersistenceResponse:
    """
    Retrieves a contact from the database by user id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadContactByUserId(?)}"
                row = cursor.execute(sql, user_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Contact.from_db_row(row),
                        message="Contact read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Contact not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read contact by user id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_contact_by_user_id(contact: Contact) -> PersistenceResponse:
    """
    Updates a contact in the database by user id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateContactByUserId(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    contact.id,
                    contact.guid,
                    contact.created_datetime,
                    contact.modified_datetime,
                    contact.first_name,
                    contact.last_name,
                    contact.email,
                    contact.phone,
                    contact.transaction_id,
                    contact.customer_id,
                    contact.user_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Contact updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to update contact",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update contact by user id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
