# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.contact.business.model import Contact
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ContactRepository:
    """
    Repository for Contact persistence operations.
    """

    def __init__(self):
        """Initialize the ContactRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Contact]:
        """
        Convert a database row into a Contact dataclass.
        """
        if not row:
            return None

        try:
            return Contact(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                email=row.Email,
                office_phone=row.OfficePhone,
                mobile_phone=row.MobilePhone,
                fax=row.Fax,
                notes=row.Notes,
                user_id=row.UserId,
                company_id=row.CompanyId,
                customer_id=row.CustomerId,
                project_id=row.ProjectId,
                vendor_id=row.VendorId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during contact mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during contact mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        email: str = None,
        office_phone: str = None,
        mobile_phone: str = None,
        fax: str = None,
        notes: str = None,
        user_id: int = None,
        company_id: int = None,
        customer_id: int = None,
        project_id: int = None,
        vendor_id: int = None,
    ) -> Contact:
        """
        Create a new contact.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateContact",
                    params={
                        "Email": email,
                        "OfficePhone": office_phone,
                        "MobilePhone": mobile_phone,
                        "Fax": fax,
                        "Notes": notes,
                        "UserId": user_id,
                        "CompanyId": company_id,
                        "CustomerId": customer_id,
                        "ProjectId": project_id,
                        "VendorId": vendor_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateContact did not return a row.")
                    raise map_database_error(Exception("CreateContact failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create contact: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Contact]:
        """
        Read all contacts.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContacts",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all contacts: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Contact]:
        """
        Read a contact by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contact by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Contact]:
        """
        Read a contact by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contact by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> list[Contact]:
        """
        Read contacts by user ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactsByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contacts by user ID: {error}")
            raise map_database_error(error)

    def read_by_company_id(self, company_id: int) -> list[Contact]:
        """
        Read contacts by company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactsByCompanyId",
                    params={"CompanyId": company_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contacts by company ID: {error}")
            raise map_database_error(error)

    def read_by_customer_id(self, customer_id: int) -> list[Contact]:
        """
        Read contacts by customer ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactsByCustomerId",
                    params={"CustomerId": customer_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contacts by customer ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[Contact]:
        """
        Read contacts by project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactsByProjectId",
                    params={"ProjectId": project_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contacts by project ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[Contact]:
        """
        Read contacts by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContactsByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contacts by vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, contact: Contact) -> Optional[Contact]:
        """
        Update a contact by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateContactById",
                    params={
                        "Id": contact.id,
                        "RowVersion": contact.row_version_bytes,
                        "Email": contact.email,
                        "OfficePhone": contact.office_phone,
                        "MobilePhone": contact.mobile_phone,
                        "Fax": contact.fax,
                        "Notes": contact.notes,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update contact by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Contact]:
        """
        Delete a contact by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteContactById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete contact by ID: {error}")
            raise map_database_error(error)
