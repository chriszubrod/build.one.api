# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.business_license.business.model import BusinessLicense
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BusinessLicenseRepository:
    """
    Repository for BusinessLicense persistence operations.
    """

    def __init__(self):
        """Initialize the BusinessLicenseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BusinessLicense]:
        """
        Convert a database row into a BusinessLicense dataclass.
        """
        if not row:
            return None

        try:
            return BusinessLicense(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                vendor_id=row.VendorId,
                license_number=row.LicenseNumber,
                issuing_authority=row.IssuingAuthority,
                issue_date=row.IssueDate,
                expiry_date=row.ExpiryDate,
                verification_status=row.VerificationStatus,
                is_deleted=bool(row.IsDeleted) if getattr(row, "IsDeleted", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during business license mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during business license mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_id: int,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: str = "Received",
        created_by_user_id: Optional[int] = None,
    ) -> BusinessLicense:
        """
        Create a new business license.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBusinessLicense",
                        params={
                            "VendorId": vendor_id,
                            "LicenseNumber": license_number,
                            "IssuingAuthority": issuing_authority,
                            "IssueDate": issue_date,
                            "ExpiryDate": expiry_date,
                            "VerificationStatus": verification_status,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBusinessLicense did not return a row.")
                        raise map_database_error(Exception("CreateBusinessLicense failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create business license: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BusinessLicense]:
        """
        Read all business licenses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBusinessLicenses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all business licenses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BusinessLicense]:
        """
        Read a business license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBusinessLicenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read business license by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BusinessLicense]:
        """
        Read a business license by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBusinessLicenseByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read business license by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[BusinessLicense]:
        """
        Read business licenses by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBusinessLicensesByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read business licenses by vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, business_license: BusinessLicense) -> Optional[BusinessLicense]:
        """
        Update a business license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateBusinessLicenseById",
                    params={
                        "Id": business_license.id,
                        "RowVersion": business_license.row_version_bytes,
                        "LicenseNumber": business_license.license_number,
                        "IssuingAuthority": business_license.issuing_authority,
                        "IssueDate": business_license.issue_date,
                        "ExpiryDate": business_license.expiry_date,
                        "VerificationStatus": business_license.verification_status,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update business license by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BusinessLicense]:
        """
        Soft-delete a business license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBusinessLicenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete business license by ID: {error}")
            raise map_database_error(error)
