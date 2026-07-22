# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.certificate_of_insurance.business.model import CertificateOfInsurance
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CertificateOfInsuranceRepository:
    """
    Repository for CertificateOfInsurance persistence operations.
    """

    def __init__(self):
        """Initialize the CertificateOfInsuranceRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CertificateOfInsurance]:
        """
        Convert a database row into a CertificateOfInsurance dataclass.
        """
        if not row:
            return None

        try:
            return CertificateOfInsurance(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                vendor_id=row.VendorId,
                issuing_authority=row.IssuingAuthority,
                issue_date=row.IssueDate,
                attachment_id=row.AttachmentId,
                verification_status=row.VerificationStatus,
                is_deleted=bool(row.IsDeleted) if getattr(row, "IsDeleted", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during certificate of insurance mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during certificate of insurance mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_id: int,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        attachment_id: Optional[int] = None,
        verification_status: str = "Received",
        created_by_user_id: Optional[int] = None,
    ) -> CertificateOfInsurance:
        """
        Create a new certificate of insurance.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateCertificateOfInsurance",
                        params={
                            "VendorId": vendor_id,
                            "IssuingAuthority": issuing_authority,
                            "IssueDate": issue_date,
                            "AttachmentId": attachment_id,
                            "VerificationStatus": verification_status,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateCertificateOfInsurance did not return a row.")
                        raise map_database_error(Exception("CreateCertificateOfInsurance failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create certificate of insurance: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[CertificateOfInsurance]:
        """
        Read all certificates of insurance.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCertificatesOfInsurance",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all certificates of insurance: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[CertificateOfInsurance]:
        """
        Read a certificate of insurance by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCertificateOfInsuranceById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read certificate of insurance by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[CertificateOfInsurance]:
        """
        Read a certificate of insurance by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCertificateOfInsuranceByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read certificate of insurance by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[CertificateOfInsurance]:
        """
        Read certificates of insurance by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCertificatesOfInsuranceByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read certificates of insurance by vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, certificate_of_insurance: CertificateOfInsurance) -> Optional[CertificateOfInsurance]:
        """
        Update a certificate of insurance by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateCertificateOfInsuranceById",
                    params={
                        "Id": certificate_of_insurance.id,
                        "RowVersion": certificate_of_insurance.row_version_bytes,
                        "IssuingAuthority": certificate_of_insurance.issuing_authority,
                        "IssueDate": certificate_of_insurance.issue_date,
                        "AttachmentId": certificate_of_insurance.attachment_id,
                        "VerificationStatus": certificate_of_insurance.verification_status,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update certificate of insurance by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[CertificateOfInsurance]:
        """
        Soft-delete a certificate of insurance by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCertificateOfInsuranceById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete certificate of insurance by ID: {error}")
            raise map_database_error(error)
