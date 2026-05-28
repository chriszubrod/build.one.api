# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.vendor_project_rate.business.model import VendorProjectRate
from entities.vendor_project_rate.persistence.repo import VendorProjectRateRepository
from shared.authz import current_user_id


def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid decimal value: {value!r}") from e


def _resolve_vendor_id(public_id: str) -> int:
    from entities.vendor.business.service import VendorService
    v = VendorService().read_by_public_id(public_id=public_id)
    if not v:
        raise ValueError(f"Vendor with public_id {public_id!r} not found.")
    return int(v.id)


def _resolve_project_id(public_id: str) -> int:
    from entities.project.business.service import ProjectService
    p = ProjectService().read_by_public_id(public_id=public_id)
    if not p:
        raise ValueError(f"Project with public_id {public_id!r} not found.")
    return int(p.id)


class VendorProjectRateService:
    def __init__(self, repo: Optional[VendorProjectRateRepository] = None):
        self.repo = repo or VendorProjectRateRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        vendor_public_id: str,
        project_public_id: str,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        notes: Optional[str] = None,
    ) -> VendorProjectRate:
        vendor_id = _resolve_vendor_id(vendor_public_id)
        project_id = _resolve_project_id(project_public_id)

        # Duplicate check — the UNIQUE filtered index will also catch this, but
        # raising here surfaces a clean ValueError instead of a SQL error.
        existing = [r for r in self.repo.read_by_vendor_id(vendor_id) if r.project_id == project_id]
        if existing:
            raise ValueError(
                f"Override row already exists for this Vendor + Project pair. "
                f"Update the existing row (public_id={existing[0].public_id}) instead."
            )

        return self.repo.create(
            vendor_id=vendor_id,
            project_id=project_id,
            hourly_rate=_coerce_decimal(hourly_rate),
            markup=_coerce_decimal(markup),
            notes=notes,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[VendorProjectRate]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorProjectRate]:
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[VendorProjectRate]:
        return self.repo.read_by_vendor_id(vendor_id)

    def read_by_project_id(self, project_id: int) -> list[VendorProjectRate]:
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        notes: Optional[str] = None,
    ) -> Optional[VendorProjectRate]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        if row_version is not None:
            existing.row_version = row_version
        if hourly_rate is not None:
            existing.hourly_rate = _coerce_decimal(hourly_rate)
        if markup is not None:
            existing.markup = _coerce_decimal(markup)
        if notes is not None:
            existing.notes = notes if notes != "" else None
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[VendorProjectRate]:
        logger = logging.getLogger(__name__)
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        logger.info(f"Soft deleting VendorProjectRate {public_id} (id={existing.id})")
        return self.repo.soft_delete_by_public_id(public_id=public_id)

    def read_effective_rate(self, *, vendor_id: int, project_id: int) -> dict:
        return self.repo.read_effective_rate(vendor_id=vendor_id, project_id=project_id)
