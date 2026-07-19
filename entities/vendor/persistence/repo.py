# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
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
                is_deleted=row.IsDeleted,
                is_contract_labor=row.IsContractLabor,
                track_compliance=bool(getattr(row, "TrackCompliance", False)),
                notes=getattr(row, "Notes", None),
                hourly_rate=getattr(row, "HourlyRate", None),
                markup=getattr(row, "Markup", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, name: Optional[str], abbreviation: Optional[str], taxpayer_id: Optional[int] = None, vendor_type_id: Optional[int] = None, is_draft: bool = True, is_contract_labor: bool = False, track_compliance: bool = False, notes: Optional[str] = None, hourly_rate: Optional[Decimal] = None, markup: Optional[Decimal] = None, created_by_user_id: Optional[int] = None) -> Vendor:
        """
        Create a new vendor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Name": name,
                    "Abbreviation": abbreviation,
                    "VendorTypeId": vendor_type_id,
                    "TaxpayerId": taxpayer_id,
                    "IsDraft": is_draft,
                    "IsContractLabor": is_contract_labor,
                    "TrackCompliance": track_compliance,
                    "Notes": notes,
                    "CreatedByUserId": created_by_user_id,
                    "HourlyRate": hourly_rate,
                    "Markup": markup,
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

    def find_contract_labor_by_email(self, email: str) -> Optional[Vendor]:
        """Bind a sender's email back to the contract-labor Vendor.

        Returns the matching Vendor (IsContractLabor=1, not soft-deleted)
        when a Contact row carries the given email (case-insensitive), or
        None when no match. Single Vendor per call — defensive TOP 1 in
        the sproc handles the edge case of duplicate Contact rows.

        Used by the contract_labor_specialist agent to route a forwarded
        timesheet email back to the worker's Vendor row.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindContractLaborVendorByEmail",
                    params={"SenderEmail": email},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during find_contract_labor_by_email: {error}")
            raise map_database_error(error)

    def find_for_invoice(self, *, vendor_name: str,
                         sender_domain: Optional[str] = None) -> list[dict]:
        """Multi-strategy ranked vendor lookup for invoice classification.
        Returns up to 5 candidates with their match strategy + confidence.
        Each candidate is a dict (not a Vendor model) because the strategy
        + confidence + matched_term metadata is invoice-specific."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindVendorForInvoice",
                    params={
                        "VendorName": vendor_name,
                        "SenderDomain": sender_domain,
                    },
                )
                out: list[dict] = []
                for row in cursor.fetchall():
                    out.append({
                        "vendor": {
                            "id": row.VendorId,
                            "public_id": row.VendorPublicId,
                            "name": row.VendorName,
                            "abbreviation": row.Abbreviation,
                            "is_draft": bool(row.IsDraft),
                            # Per-vendor notes — bill_specialist reads this
                            # and applies any vendor-specific rules
                            # (e.g. "trim /N suffix") to its create_bill.
                            "notes": getattr(row, "Notes", None),
                        },
                        "confidence": float(row.Confidence) if row.Confidence is not None else None,
                        "strategy": row.Strategy,
                        "matched_term": row.MatchedTerm,
                    })
                return out
        except Exception as error:
            logger.error(f"Error during find_vendor_for_invoice: {error}")
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
                    "Notes": vendor.notes,
                    "HourlyRate": vendor.hourly_rate,
                    "Markup": vendor.markup,
                    "TrackCompliance": vendor.track_compliance,
                }
                # Only include IsDraft/IsContractLabor if explicitly set (not None)
                if vendor.is_draft is not None:
                    params["IsDraft"] = 1 if vendor.is_draft else 0
                if vendor.is_contract_labor is not None:
                    params["IsContractLabor"] = 1 if vendor.is_contract_labor else 0
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

    def soft_delete_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Soft delete a vendor by public ID (sets IsDeleted = 1).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SoftDeleteVendorByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during soft delete vendor by public ID: {error}")
            raise map_database_error(error)
