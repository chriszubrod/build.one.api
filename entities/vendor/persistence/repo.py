# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor.business.model import Vendor
from shared.database import (
    call_procedure,
    conn_ctx,
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
                qbo_active=getattr(row, "QboActive", None),
                qbo_id=getattr(row, "QboId", None),
                realm_id=getattr(row, "RealmId", None),
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

    def read_public_ids_by_ids(self, ids: list[int], *, conn: Optional[pyodbc.Connection] = None) -> dict[int, str]:
        """Return a mapping of VendorId -> Vendor PublicId for the requested ids.

        Read-side echo seam (U-409): bill reads carry only the integer
        `vendor_id`, but `BillUpdate.vendor_public_id` is REQUIRED — iOS has no
        Vendor surface of its own, so the server echoes the identity it already
        knows rather than making the client translate int -> UUID.

        Reuses the existing `ReadVendors` sproc (no new sproc / no DDL): one
        query for the whole batch, not N+1. `ReadVendors` filters
        `IsDeleted = 0`, so a soft-deleted vendor is simply absent from the
        result and its bill echoes `null`.

        An id with no row is OMITTED from the mapping — callers `.get()` it to
        `None`. Never substitutes another vendor: a wrong echo would re-point
        the bill to the WRONG vendor on the next update (BillService.update
        resolves vendor_public_id -> vendor_id and overwrites), so every
        failure mode here degrades to `null`, including a DB error.
        """
        if not ids:
            return {}
        try:
            # Coercion inside the try so a bad id fails closed like any other error.
            wanted = {int(i) for i in ids if i is not None}
            if not wanted:
                return {}
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendors",
                        params={},
                    )
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
                return {row.Id: str(row.PublicId) for row in rows if row.Id in wanted}
        except Exception as error:
            logger.error(f"Error during read vendor public ids by ids: {error}")
            return {}

    def read_public_id_by_id(self, id: int, *, conn: Optional[pyodbc.Connection] = None) -> Optional[str]:
        """Vendor PublicId for a single id, or None (U-409, N=1 companion).

        Same contract as `read_public_ids_by_ids` — missing or soft-deleted
        resolves to None, never a substitute, and a DB error degrades to None
        rather than failing the bill read that called it — but over the
        indexed `ReadVendorById` (`WHERE Id = @Id AND IsDeleted = 0`) instead
        of scanning the full ~1.1k-row vendor catalogue for one UUID. The
        batch method stays the right shape for the bills LIST route.
        """
        if not id:
            return None
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendorById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                finally:
                    cursor.close()
                return str(row.PublicId) if row else None
        except Exception as error:
            logger.error(f"Error during read vendor public id by id: {error}")
            return None

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

    def read_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None) -> Optional[Vendor]:
        """
        Read a vendor directly by its dbo-native QBO identity (U-290), bypassing
        the qbo.Vendor / qbo.VendorVendor staging/mapping tables entirely.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorByQboIdAndRealmId",
                    params={"QboId": qbo_id, "RealmId": realm_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by QBO identity: {error}")
            raise map_database_error(error)

    def read_deleted_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None):
        """
        U-313 P1 guard: does a SOFT-DELETED Vendor already hold this exact QBO
        identity? `read_by_qbo_identity` above filters IsDeleted = 0, so a
        soft-deleted holder reads as a "miss" -- this is the deliberate
        including-deleted counterpart, used only to refuse a silent duplicate
        create/adopt (see VendorVendorConnector._resolve_vendor_candidate).
        Minimal projection (id/public_id/name only) -- not a full Vendor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadDeletedVendorByQboIdAndRealmId",
                    params={"QboId": qbo_id, "RealmId": realm_id},
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return SimpleNamespace(id=row.Id, public_id=row.PublicId, name=row.Name)
        except Exception as error:
            logger.error(f"Error during read deleted vendor by QBO identity: {error}")
            raise map_database_error(error)

    def set_qbo_identity(
        self,
        *,
        id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        active: Optional[bool] = None,
    ) -> None:
        """Stamp dbo-native QBO identity + active-mirror columns (idempotent-safe via CASE WHEN sproc)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetVendorQboIdentity",
                    params={
                        "Id": id,
                        "QboId": qbo_id,
                        "RealmId": realm_id,
                        "Active": active,
                    },
                )
                row = cursor.fetchone()
                if row and getattr(row, "Stolen", False):
                    logger.warning(
                        "Vendor %s stole QBO identity (qbo_id=%s realm_id=%s) from a different "
                        "Vendor row — a stale duplicate identity existed before this stamp",
                        id, qbo_id, realm_id,
                    )
        except Exception as error:
            logger.error(f"Error during set vendor qbo identity: {error}")
            raise map_database_error(error)

