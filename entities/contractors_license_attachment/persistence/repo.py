# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.contractors_license_attachment.business.model import ContractorsLicenseAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ContractorsLicenseAttachmentRepository:
    """
    Repository for ContractorsLicenseAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the ContractorsLicenseAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ContractorsLicenseAttachment]:
        """
        Convert a database row into a ContractorsLicenseAttachment dataclass.
        """
        if not row:
            return None

        try:
            return ContractorsLicenseAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                contractors_license_id=row.ContractorsLicenseId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during contractors license attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during contractors license attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, contractors_license_id: int, attachment_id: int) -> ContractorsLicenseAttachment:
        """
        Create a new contractors license attachment link.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateContractorsLicenseAttachment",
                        params={
                            "ContractorsLicenseId": contractors_license_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateContractorsLicenseAttachment did not return a row.")
                        raise map_database_error(Exception("CreateContractorsLicenseAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create contractors license attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ContractorsLicenseAttachment]:
        """
        Read all contractors license attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadContractorsLicenseAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all contractors license attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ContractorsLicenseAttachment]:
        """
        Read a contractors license attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadContractorsLicenseAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read contractors license attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ContractorsLicenseAttachment]:
        """
        Read a contractors license attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadContractorsLicenseAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read contractors license attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_contractors_license_id(self, contractors_license_id: int) -> list[ContractorsLicenseAttachment]:
        """
        Read contractors license attachments by contractors license ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadContractorsLicenseAttachmentsByContractorsLicenseId",
                        params={"ContractorsLicenseId": contractors_license_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read contractors license attachments by contractors license ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ContractorsLicenseAttachment]:
        """
        Delete a contractors license attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteContractorsLicenseAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete contractors license attachment by ID: {error}")
            raise map_database_error(error)
