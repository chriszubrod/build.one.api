# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor.business.model import Vendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorRepository:
    """
    Repository for Vendor persistence operations.
    """

    def __init__(self):
        """Initialize the VendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Vendor]:
        """
        Convert a database row into a Vendor dataclass.
        """
        if not row:
            return None

        try:
            return Vendor(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                abbreviation=row.Abbreviation,
                taxpayer_id=row.TaxpayerId,
                vendor_type_id=row.VendorTypeId,
                is_draft=row.IsDraft,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, name: Optional[str], abbreviation: Optional[str], taxpayer_id: Optional[int] = None, vendor_type_id: Optional[int] = None, is_draft: bool = True) -> Vendor:
        """
        Create a new vendor.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            name: Vendor name
            abbreviation: Vendor abbreviation
            taxpayer_id: Optional taxpayer ID
            vendor_type_id: Optional vendor type ID
            is_draft: Whether vendor is in draft state
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                params = {
                    "Name": name,
                    "Abbreviation": abbreviation,
                    "VendorTypeId": vendor_type_id,
                    "TaxpayerId": taxpayer_id,
                    "IsDraft": is_draft,
                }
                call_procedure(
                    cursor=cursor,
                    name="CreateVendor",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendor did not return a row.")
                    raise map_database_error(Exception("CreateVendor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendors",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all vendors: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Vendor]:
        """
        Read a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Read a vendor by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Vendor]:
        """
        Read a vendor by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, vendor: Vendor) -> Optional[Vendor]:
        """
        Update a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": vendor.id,
                    "RowVersion": vendor.row_version_bytes,
                    "Name": vendor.name,
                    "Abbreviation": vendor.abbreviation,
                    "VendorTypeId": vendor.vendor_type_id,
                    "TaxpayerId": vendor.taxpayer_id,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if vendor.is_draft is not None:
                    params["IsDraft"] = 1 if vendor.is_draft else 0
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update vendor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Vendor]:
        """
        Delete a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during delete vendor by ID: {error}")
            raise map_database_error(error)
