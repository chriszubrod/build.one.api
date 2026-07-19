# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor_compliance_document.business.model import VendorComplianceDocument
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorComplianceDocumentRepository:
    """
    Repository for VendorComplianceDocument persistence operations.
    """

    def __init__(self):
        """Initialize the VendorComplianceDocumentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorComplianceDocument]:
        """
        Convert a database row into a VendorComplianceDocument dataclass.
        """
        if not row:
            return None

        try:
            return VendorComplianceDocument(
                id=getattr(row, "Id", None),
                public_id=getattr(row, "PublicId", None),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                vendor_id=getattr(row, "VendorId", None),
                document_type=getattr(row, "DocumentType", None),
                issuing_authority=getattr(row, "IssuingAuthority", None),
                document_number=getattr(row, "DocumentNumber", None),
                classification=getattr(row, "Classification", None),
                issue_date=getattr(row, "IssueDate", None),
                expiry_date=getattr(row, "ExpiryDate", None),
                attachment_id=getattr(row, "AttachmentId", None),
                verification_status=getattr(row, "VerificationStatus", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor compliance document mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor compliance document mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_id: int,
        document_type: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        attachment_id: Optional[int] = None,
        verification_status: str = "Received",
        created_by_user_id: Optional[int] = None,
    ) -> VendorComplianceDocument:
        """
        Create a new vendor compliance document.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorComplianceDocument",
                    params={
                        "VendorId": vendor_id,
                        "DocumentType": document_type,
                        "IssuingAuthority": issuing_authority,
                        "DocumentNumber": document_number,
                        "Classification": classification,
                        "IssueDate": issue_date,
                        "ExpiryDate": expiry_date,
                        "AttachmentId": attachment_id,
                        "VerificationStatus": verification_status,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorComplianceDocument did not return a row.")
                    raise map_database_error(Exception("CreateVendorComplianceDocument failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor compliance document: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorComplianceDocumentById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor compliance document by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorComplianceDocumentByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor compliance document by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[VendorComplianceDocument]:
        """
        Read vendor compliance documents by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorComplianceDocumentsByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read vendor compliance documents by vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, doc: VendorComplianceDocument) -> VendorComplianceDocument:
        """
        Update a vendor compliance document by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorComplianceDocumentById",
                    params={
                        "Id": doc.id,
                        "RowVersion": doc.row_version_bytes,
                        "DocumentType": doc.document_type,
                        "IssuingAuthority": doc.issuing_authority,
                        "DocumentNumber": doc.document_number,
                        "Classification": doc.classification,
                        "IssueDate": doc.issue_date,
                        "ExpiryDate": doc.expiry_date,
                        "AttachmentId": doc.attachment_id,
                        "VerificationStatus": doc.verification_status,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError("Concurrency conflict")
                return self._from_db(row)
        except ValueError:
            raise
        except Exception as error:
            logger.error(f"Error during update vendor compliance document by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> bool:
        """
        Delete a vendor compliance document by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorComplianceDocumentById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return row is not None
        except Exception as error:
            logger.error(f"Error during delete vendor compliance document by ID: {error}")
            raise map_database_error(error)
