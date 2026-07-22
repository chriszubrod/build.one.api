# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor_type_required_coverage.business.model import VendorTypeRequiredCoverage
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorTypeRequiredCoverageRepository:
    """
    Repository for VendorTypeRequiredCoverage persistence operations.
    """

    def __init__(self):
        """Initialize the VendorTypeRequiredCoverageRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorTypeRequiredCoverage]:
        """
        Convert a database row into a VendorTypeRequiredCoverage dataclass.
        """
        if not row:
            return None

        try:
            return VendorTypeRequiredCoverage(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                vendor_type_id=row.VendorTypeId,
                coverage_type=row.CoverageType,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor type required coverage mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor type required coverage mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_type_id: int,
        coverage_type: str,
        created_by_user_id: Optional[int] = None,
    ) -> VendorTypeRequiredCoverage:
        """
        Create a new vendor type required coverage row.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateVendorTypeRequiredCoverage",
                        params={
                            "VendorTypeId": vendor_type_id,
                            "CoverageType": coverage_type,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateVendorTypeRequiredCoverage did not return a row.")
                        raise map_database_error(Exception("CreateVendorTypeRequiredCoverage failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create vendor type required coverage: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[VendorTypeRequiredCoverage]:
        """
        Read all vendor type required coverage rows.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypeRequiredCoverages",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all vendor type required coverages: {error}")
            raise map_database_error(error)

    def read_by_vendor_type_id(self, vendor_type_id: int) -> list[VendorTypeRequiredCoverage]:
        """
        Read required coverages for a vendor type.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypeRequiredCoveragesByVendorTypeId",
                    params={"VendorTypeId": vendor_type_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read vendor type required coverages by vendor type ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> bool:
        """
        Hard-delete a vendor type required coverage row by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorTypeRequiredCoverageById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return row is not None
        except Exception as error:
            logger.error(f"Error during delete vendor type required coverage by ID: {error}")
            raise map_database_error(error)
