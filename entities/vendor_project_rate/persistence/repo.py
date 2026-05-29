# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor_project_rate.business.model import VendorProjectRate
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class VendorProjectRateRepository:
    """Repository for VendorProjectRate persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorProjectRate]:
        if not row:
            return None
        try:
            return VendorProjectRate(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=row.VendorId,
                project_id=row.ProjectId,
                hourly_rate=row.HourlyRate,
                markup=row.Markup,
                notes=getattr(row, "Notes", None),
                is_deleted=bool(row.IsDeleted),
                vendor_name=getattr(row, "VendorName", None),
                vendor_public_id=getattr(row, "VendorPublicId", None),
                project_name=getattr(row, "ProjectName", None),
                project_public_id=getattr(row, "ProjectPublicId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping VendorProjectRate row: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        vendor_id: int,
        project_id: int,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> VendorProjectRate:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorProjectRate",
                    params={
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "HourlyRate": hourly_rate,
                        "Markup": markup,
                        "Notes": notes,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateVendorProjectRate failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor project rate: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadVendorProjectRateById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during read vendor project rate by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadVendorProjectRateByPublicId", params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during read vendor project rate by public id: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadVendorProjectRatesByVendorId", params={"VendorId": vendor_id})
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error during read vendor project rates by vendor id: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadVendorProjectRatesByProjectId", params={"ProjectId": project_id})
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error during read vendor project rates by project id: {error}")
            raise map_database_error(error)

    def update_by_id(self, rate: VendorProjectRate) -> Optional[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorProjectRateById",
                    params={
                        "Id": rate.id,
                        "RowVersion": rate.row_version_bytes,
                        "HourlyRate": rate.hourly_rate,
                        "Markup": rate.markup,
                        "Notes": rate.notes,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during update vendor project rate: {error}")
            raise map_database_error(error)

    def soft_delete_by_public_id(self, public_id: str) -> Optional[VendorProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="SoftDeleteVendorProjectRateByPublicId", params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during soft-delete vendor project rate: {error}")
            raise map_database_error(error)

    def read_effective_rate(self, *, vendor_id: int, project_id: int) -> dict:
        """Resolves (Vendor × Project) → effective rate.

        Returns {hourly_rate, markup, rate_source} where rate_source ∈
        {'override', 'default', 'none'}. Used by Phase 4 aggregation.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEffectiveRateForVendorProject",
                    params={"VendorId": vendor_id, "ProjectId": project_id},
                )
                row = cursor.fetchone()
                if not row:
                    return {"hourly_rate": None, "markup": None, "rate_source": "none"}
                return {
                    "hourly_rate": row.HourlyRate,
                    "markup": row.Markup,
                    "rate_source": row.RateSource,
                }
        except Exception as error:
            logger.error(f"Error during read_effective_rate (vendor): {error}")
            raise map_database_error(error)
