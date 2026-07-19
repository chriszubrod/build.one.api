# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor_insurance_policy.business.model import VendorInsurancePolicy
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorInsurancePolicyRepository:
    """
    Repository for VendorInsurancePolicy persistence operations.
    """

    def __init__(self):
        """Initialize the VendorInsurancePolicyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorInsurancePolicy]:
        """
        Convert a database row into a VendorInsurancePolicy dataclass.
        """
        if not row:
            return None

        try:
            return VendorInsurancePolicy(
                id=getattr(row, "Id", None),
                public_id=getattr(row, "PublicId", None),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                vendor_compliance_document_id=getattr(row, "VendorComplianceDocumentId", None),
                coverage_type=getattr(row, "CoverageType", None),
                carrier=getattr(row, "Carrier", None),
                policy_number=getattr(row, "PolicyNumber", None),
                each_occurrence=getattr(row, "EachOccurrence", None),
                aggregate=getattr(row, "Aggregate", None),
                effective_date=getattr(row, "EffectiveDate", None),
                expiry_date=getattr(row, "ExpiryDate", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor insurance policy mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor insurance policy mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_compliance_document_id: int,
        coverage_type: str,
        carrier: Optional[str] = None,
        policy_number: Optional[str] = None,
        each_occurrence=None,
        aggregate=None,
        effective_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> VendorInsurancePolicy:
        """
        Create a new vendor insurance policy.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorInsurancePolicy",
                    params={
                        "VendorComplianceDocumentId": vendor_compliance_document_id,
                        "CoverageType": coverage_type,
                        "Carrier": carrier,
                        "PolicyNumber": policy_number,
                        "EachOccurrence": each_occurrence,
                        "Aggregate": aggregate,
                        "EffectiveDate": effective_date,
                        "ExpiryDate": expiry_date,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorInsurancePolicy did not return a row.")
                    raise map_database_error(Exception("CreateVendorInsurancePolicy failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor insurance policy: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[VendorInsurancePolicy]:
        """
        Read a vendor insurance policy by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorInsurancePolicyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor insurance policy by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[VendorInsurancePolicy]:
        """
        Read a vendor insurance policy by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorInsurancePolicyByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor insurance policy by public ID: {error}")
            raise map_database_error(error)

    def read_by_compliance_document_id(self, vendor_compliance_document_id: int) -> list[VendorInsurancePolicy]:
        """
        Read vendor insurance policies by compliance document ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorInsurancePoliciesByComplianceDocumentId",
                    params={"VendorComplianceDocumentId": vendor_compliance_document_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read vendor insurance policies by compliance document ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, policy: VendorInsurancePolicy) -> VendorInsurancePolicy:
        """
        Update a vendor insurance policy by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorInsurancePolicyById",
                    params={
                        "Id": policy.id,
                        "RowVersion": policy.row_version_bytes,
                        "CoverageType": policy.coverage_type,
                        "Carrier": policy.carrier,
                        "PolicyNumber": policy.policy_number,
                        "EachOccurrence": policy.each_occurrence,
                        "Aggregate": policy.aggregate,
                        "EffectiveDate": policy.effective_date,
                        "ExpiryDate": policy.expiry_date,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError("Concurrency conflict")
                return self._from_db(row)
        except ValueError:
            raise
        except Exception as error:
            logger.error(f"Error during update vendor insurance policy by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> bool:
        """
        Delete a vendor insurance policy by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorInsurancePolicyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return row is not None
        except Exception as error:
            logger.error(f"Error during delete vendor insurance policy by ID: {error}")
            raise map_database_error(error)
