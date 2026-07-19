# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Third-party Imports

# Local Imports
from entities.vendor.business.model import Vendor
from entities.taxpayer.business.service import TaxpayerService
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor.persistence.repo import VendorRepository
from shared.authz import current_user_id


class VendorService:
    """
    Service for Vendor entity business operations.
    """

    def __init__(self, repo: Optional[VendorRepository] = None):
        """Initialize the VendorService."""
        self.repo = repo or VendorRepository()

    @staticmethod
    def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
        """Decimal(str(...)) coerce per memory's financial-precision rule."""
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Invalid decimal value: {value!r}") from e

    def create(self, *, tenant_id: int = 1, name: str, abbreviation: Optional[str] = None, taxpayer_public_id: Optional[str] = None, vendor_type_public_id: Optional[str] = None, is_draft: bool = True, is_contract_labor: bool = False, track_compliance: bool = False, notes: Optional[str] = None, hourly_rate=None, markup=None) -> Vendor:
        """
        Create a new vendor.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Vendor name (required)
            abbreviation: Vendor abbreviation
            taxpayer_public_id: Optional taxpayer public ID
            vendor_type_public_id: Optional vendor type public ID
            is_draft: Whether vendor is in draft state
        """
        if not name or not name.strip():
            raise ValueError("Vendor name is required.")
        name = name.strip()

        existing = self.read_by_name(name=name)
        if existing:
            raise ValueError(f"Vendor with name '{name}' already exists.")

        taxpayer_id = None
        vendor_type_id = None
        if taxpayer_public_id:
            taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
            if taxpayer:
                taxpayer_id = taxpayer.id
        if vendor_type_public_id:
            vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
            if vendor_type:
                vendor_type_id = vendor_type.id

        return self.repo.create(tenant_id=tenant_id, name=name, abbreviation=abbreviation, taxpayer_id=taxpayer_id, vendor_type_id=vendor_type_id, is_draft=is_draft, is_contract_labor=is_contract_labor, track_compliance=track_compliance, notes=notes, hourly_rate=self._coerce_decimal(hourly_rate), markup=self._coerce_decimal(markup), created_by_user_id=current_user_id.get())

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors (excludes soft-deleted).
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Vendor]:
        """
        Read a vendor by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Read a vendor by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Vendor]:
        """
        Read a vendor by name.
        """
        return self.repo.read_by_name(name)

    def find_contract_labor_by_email(self, email: str) -> Optional[Vendor]:
        """Bind a sender's email back to the contract-labor Vendor row.

        Returns the matching Vendor (IsContractLabor=1, not soft-deleted)
        whose Contact row carries the given email (case-insensitive), or
        None when no match. Used by the contract_labor_specialist agent
        to route a forwarded timesheet email back to the worker's
        Vendor row.
        """
        return self.repo.find_contract_labor_by_email(email)

    def find_for_invoice(self, *, vendor_name: str,
                         sender_domain: Optional[str] = None) -> list[dict]:
        """Multi-strategy ranked vendor lookup for invoice classification.
        Use this when an agent or upstream is trying to bind a fresh
        invoice's vendor name (often DI-extracted, often imperfect) to
        an existing Vendor row. Returns up to 5 candidates with strategy
        + confidence labels — caller picks the highest-confidence match
        and surfaces ambiguity if multiple are close."""
        return self.repo.find_for_invoice(
            vendor_name=vendor_name, sender_domain=sender_domain,
        )

    def search_by_name(self, *, query: str, limit: int = 10):
        """
        Case-insensitive substring search against Name + Abbreviation.
        Soft-deleted rows are excluded; prefix matches rank above
        substring matches.

        In-memory filter over `read_all()` (~1100 rows). Cheap enough
        today; upgrade to a dedicated LIKE sproc if it becomes a hot
        path or fuzzy ranking gets more complex.
        """
        q = (query or "").strip().lower()
        if not q or limit <= 0:
            return []

        prefix_hits = []
        substring_hits = []

        for vendor in self.repo.read_all():
            if getattr(vendor, "is_deleted", False):
                continue
            name = (vendor.name or "").lower()
            abbreviation = (getattr(vendor, "abbreviation", "") or "").lower()

            if name.startswith(q) or abbreviation.startswith(q):
                prefix_hits.append(vendor)
            elif q in name or q in abbreviation:
                substring_hits.append(vendor)

            if len(prefix_hits) >= limit:
                break

        return (prefix_hits + substring_hits)[:limit]

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        name: str = None,
        abbreviation: str = None,
        taxpayer_public_id: str = None,
        vendor_type_public_id: str = None,
        is_draft: bool = None,
        is_contract_labor: bool = None,
        track_compliance: Optional[bool] = None,
        notes: Optional[str] = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
    ) -> Optional[Vendor]:
        """
        Update a vendor by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        # Use provided row_version or keep existing
        if row_version is not None:
            existing.row_version = row_version

        # Check for duplicate name if name is being changed
        if name is not None and name != existing.name:
            duplicate = self.read_by_name(name=name)
            if duplicate and duplicate.public_id != public_id:
                raise ValueError(f"Vendor with name '{name}' already exists.")

        if name is not None:
            existing.name = name
        if abbreviation is not None:
            existing.abbreviation = abbreviation
        if is_draft is not None:
            existing.is_draft = is_draft
        if is_contract_labor is not None:
            existing.is_contract_labor = is_contract_labor
        if track_compliance is not None:
            existing.track_compliance = track_compliance
        # React form always sends `notes` on PUT (echoing current value
        # if unedited). None means "field omitted entirely" → preserve.
        # Empty string means user cleared the textarea → store NULL.
        if notes is not None:
            existing.notes = notes if notes != "" else None

        # Handle taxpayer FK — empty string clears, valid public_id sets
        if taxpayer_public_id is not None:
            if taxpayer_public_id == '':
                existing.taxpayer_id = None
            else:
                taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
                if taxpayer:
                    existing.taxpayer_id = int(taxpayer.id)
                else:
                    existing.taxpayer_id = None

        if hourly_rate is not None:
            existing.hourly_rate = self._coerce_decimal(hourly_rate)
        if markup is not None:
            existing.markup = self._coerce_decimal(markup)

        # Handle vendor_type FK — empty string clears, valid public_id sets
        if vendor_type_public_id is not None:
            if vendor_type_public_id == '':
                existing.vendor_type_id = None
            else:
                vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
                if vendor_type:
                    existing.vendor_type_id = int(vendor_type.id)
                else:
                    existing.vendor_type_id = None

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Vendor]:
        """
        Soft delete a vendor by public ID.

        Sets IsDeleted = 1. All child entity FK relationships (Bill, Expense,
        Contact, etc.) are preserved — no cascade needed.

        TODO: In Phase 10, validate tenant_id matches record's tenant
        """
        logger = logging.getLogger(__name__)

        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        logger.info(f"Soft deleting vendor {public_id} (id={existing.id})")
        return self.repo.soft_delete_by_public_id(public_id=public_id)
