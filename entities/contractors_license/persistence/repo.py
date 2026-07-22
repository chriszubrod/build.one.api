# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.contractors_license.business.model import ContractorsLicense
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ContractorsLicenseRepository:
    """
    Repository for ContractorsLicense persistence operations.
    """

    def __init__(self):
        """Initialize the ContractorsLicenseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ContractorsLicense]:
        """
        Convert a database row into a ContractorsLicense dataclass.
        """
        if not row:
            return None

        try:
            return ContractorsLicense(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                vendor_id=row.VendorId,
                license_number=row.LicenseNumber,
                issuing_authority=row.IssuingAuthority,
                classification=row.Classification,
                issue_date=row.IssueDate,
                expiry_date=row.ExpiryDate,
                verification_status=row.VerificationStatus,
                is_deleted=bool(row.IsDeleted) if getattr(row, "IsDeleted", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during contractors license mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during contractors license mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_id: int,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: str = "Received",
        created_by_user_id: Optional[int] = None,
    ) -> ContractorsLicense:
        """
        Create a new contractors license.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateContractorsLicense",
                        params={
                            "VendorId": vendor_id,
                            "LicenseNumber": license_number,
                            "IssuingAuthority": issuing_authority,
                            "Classification": classification,
                            "IssueDate": issue_date,
                            "ExpiryDate": expiry_date,
                            "VerificationStatus": verification_status,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateContractorsLicense did not return a row.")
                        raise map_database_error(Exception("CreateContractorsLicense failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create contractors license: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ContractorsLicense]:
        """
        Read all contractors licenses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractorsLicenses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all contractors licenses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ContractorsLicense]:
        """
        Read a contractors license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractorsLicenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contractors license by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ContractorsLicense]:
        """
        Read a contractors license by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractorsLicenseByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contractors license by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[ContractorsLicense]:
        """
        Read contractors licenses by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractorsLicensesByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contractors licenses by vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, contractors_license: ContractorsLicense) -> Optional[ContractorsLicense]:
        """
        Update a contractors license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractorsLicenseById",
                    params={
                        "Id": contractors_license.id,
                        "RowVersion": contractors_license.row_version_bytes,
                        "LicenseNumber": contractors_license.license_number,
                        "IssuingAuthority": contractors_license.issuing_authority,
                        "Classification": contractors_license.classification,
                        "IssueDate": contractors_license.issue_date,
                        "ExpiryDate": contractors_license.expiry_date,
                        "VerificationStatus": contractors_license.verification_status,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update contractors license by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ContractorsLicense]:
        """
        Soft-delete a contractors license by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteContractorsLicenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete contractors license by ID: {error}")
            raise map_database_error(error)
