# Python Standard Library Imports
from decimal import Decimal
from typing import Any, Optional

# Local Imports
from entities.asset.business.model import Asset, AssetAccountExclusion, AssetFinancingNote, AssetWithQbo
from entities.asset.persistence.repo import (
    AssetAccountExclusionRepository,
    AssetFinancingNoteRepository,
    AssetRepository,
)
from shared.access import EntityNotAccessibleError
from shared.authz import current_company_id, current_is_system_admin, current_user_id


def _serialize_asset_with_qbo(asset: AssetWithQbo) -> dict:
    data = asset.to_dict()
    for key in (
        "fixed_asset_account_balance",
        "accum_dep_account_balance",
    ):
        val = data.get(key)
        if val is not None:
            data[key] = str(val)
    return data


class AssetService:
    """Service for Asset register operations (read-only against QBO)."""

    def __init__(self, repo: Optional[AssetRepository] = None):
        self.repo = repo or AssetRepository()

    def _require_company_id(self) -> int:
        cid = current_company_id.get()
        if cid is None:
            raise ValueError("Active company is required for asset operations.")
        return int(cid)

    def _assert_company_access(self, asset: Asset) -> None:
        if current_is_system_admin.get():
            return
        cid = current_company_id.get()
        if cid is None or asset.company_id is None or int(asset.company_id) != int(cid):
            raise EntityNotAccessibleError("Asset", asset.id or 0)

    def create(
        self,
        *,
        tenant_id: int = None,
        name: str,
        asset_type: str,
        make: Optional[str] = None,
        model: Optional[str] = None,
        model_year: Optional[int] = None,
        serial_number: Optional[str] = None,
        status: str = "active",
        acquisition_date: Optional[str] = None,
        disposal_date: Optional[str] = None,
        qbo_fixed_asset_account_id: Optional[str] = None,
        qbo_accum_dep_account_id: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Asset:
        cid = company_id if company_id is not None else self._require_company_id()
        return self.repo.create(
            name=name,
            asset_type=asset_type,
            make=make,
            model=model,
            model_year=model_year,
            serial_number=serial_number,
            status=status,
            acquisition_date=acquisition_date,
            disposal_date=disposal_date,
            qbo_fixed_asset_account_id=qbo_fixed_asset_account_id,
            qbo_accum_dep_account_id=qbo_accum_dep_account_id,
            company_id=cid,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[Asset]:
        return self.repo.read_by_company_id(self._require_company_id())

    def read_by_public_id(self, public_id: str) -> Optional[Asset]:
        asset = self.repo.read_by_public_id(public_id=public_id)
        if asset:
            self._assert_company_access(asset)
        return asset

    def read_with_qbo_by_public_id(self, public_id: str) -> Optional[dict]:
        asset = self.repo.read_with_qbo_by_public_id(public_id=public_id)
        if not asset:
            return None
        self._assert_company_access(asset)
        return _serialize_asset_with_qbo(asset)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: Optional[str] = None,
        asset_type: Optional[str] = None,
        make: Optional[str] = None,
        model: Optional[str] = None,
        model_year: Optional[int] = None,
        serial_number: Optional[str] = None,
        status: Optional[str] = None,
        acquisition_date: Optional[str] = None,
        disposal_date: Optional[str] = None,
        qbo_fixed_asset_account_id: Optional[str] = None,
        qbo_accum_dep_account_id: Optional[str] = None,
    ) -> Optional[Asset]:
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        self._assert_company_access(existing)
        existing.row_version = row_version
        if name is not None:
            existing.name = name
        if asset_type is not None:
            existing.asset_type = asset_type
        if make is not None:
            existing.make = make
        if model is not None:
            existing.model = model
        if model_year is not None:
            existing.model_year = model_year
        if serial_number is not None:
            existing.serial_number = serial_number
        if status is not None:
            existing.status = status
        if acquisition_date is not None:
            existing.acquisition_date = acquisition_date
        if disposal_date is not None:
            existing.disposal_date = disposal_date
        if qbo_fixed_asset_account_id is not None:
            existing.qbo_fixed_asset_account_id = qbo_fixed_asset_account_id
        if qbo_accum_dep_account_id is not None:
            existing.qbo_accum_dep_account_id = qbo_accum_dep_account_id
        updated = self.repo.update_by_id(existing)
        if updated is None:
            raise ValueError(
                "Concurrency conflict: Asset has been modified by another user."
            )
        return updated

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Asset]:
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        self._assert_company_access(existing)

        return self.repo.delete_cascade_by_id(int(existing.id))

    def read_divergence_check(self) -> dict:
        raw = self.repo.read_divergence_check(self._require_company_id())
        return _serialize_divergence_payload(raw)


def _serialize_divergence_payload(raw: dict) -> dict:
    out: dict[str, Any] = {}
    for section, rows in raw.items():
        serialized = []
        for row in rows:
            item = dict(row)
            for key, val in list(item.items()):
                if isinstance(val, Decimal):
                    item[key] = str(val)
            serialized.append(item)
        out[section] = serialized
    return out


class AssetFinancingNoteService:
    def __init__(self, repo: Optional[AssetFinancingNoteRepository] = None):
        self.repo = repo or AssetFinancingNoteRepository()

    def _assert_company_access(self, note: AssetFinancingNote) -> None:
        """Authorize against the PARENT asset, not the note's own stamped CompanyId.

        `create` derives the note's CompanyId from its parent asset, so the two agree
        for every note this service writes. That invariant is not enforced by the
        schema, though — `CreateAssetFinancingNote` takes @AssetId and @CompanyId
        independently — so trusting the note's own column would make authorization
        depend on an invariant a direct INSERT or a future second create path could
        break. Deriving it from the parent instead removes that dependency.
        """
        if current_is_system_admin.get():
            return
        cid = current_company_id.get()
        if cid is None:
            raise EntityNotAccessibleError("AssetFinancingNote", note.id or 0)
        owning_company_id = note.company_id
        if note.asset_id:
            parent = AssetService().repo.read_by_id(int(note.asset_id))
            if parent is None or parent.company_id is None:
                raise EntityNotAccessibleError("AssetFinancingNote", note.id or 0)
            owning_company_id = parent.company_id
        if owning_company_id is None or int(owning_company_id) != int(cid):
            raise EntityNotAccessibleError("AssetFinancingNote", note.id or 0)

    def create(
        self,
        *,
        tenant_id: int = None,
        asset_public_id: str,
        qbo_liability_account_id: str,
    ) -> AssetFinancingNote:
        asset = AssetService().read_by_public_id(asset_public_id)
        if not asset or not asset.id:
            raise ValueError(f"Asset with public_id '{asset_public_id}' not found")
        cid = asset.company_id
        if cid is None:
            raise ValueError("Asset is missing CompanyId")
        return self.repo.create(
            asset_id=int(asset.id),
            qbo_liability_account_id=qbo_liability_account_id,
            company_id=int(cid),
            created_by_user_id=current_user_id.get(),
        )

    def read_by_asset_public_id(self, asset_public_id: str) -> list[AssetFinancingNote]:
        asset = AssetService().read_by_public_id(asset_public_id)
        if not asset or not asset.id:
            return []
        return self.repo.read_by_asset_id(int(asset.id))

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[AssetFinancingNote]:
        note = self.repo.read_by_public_id(public_id)
        if not note:
            return None
        self._assert_company_access(note)
        if note.id:
            return self.repo.delete_by_id(int(note.id))
        return None


class AssetAccountExclusionService:
    def __init__(self, repo: Optional[AssetAccountExclusionRepository] = None):
        self.repo = repo or AssetAccountExclusionRepository()

    def _require_company_id(self) -> int:
        cid = current_company_id.get()
        if cid is None:
            raise ValueError("Active company is required for asset exclusion operations.")
        return int(cid)

    def _assert_company_access(self, row: AssetAccountExclusion) -> None:
        if current_is_system_admin.get():
            return
        cid = current_company_id.get()
        if cid is None or row.company_id is None or int(row.company_id) != int(cid):
            raise EntityNotAccessibleError("AssetAccountExclusion", row.id or 0)

    def create(
        self,
        *,
        tenant_id: int = None,
        qbo_account_id: str,
        reason: str,
    ) -> AssetAccountExclusion:
        return self.repo.create(
            qbo_account_id=qbo_account_id,
            reason=reason,
            company_id=self._require_company_id(),
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[AssetAccountExclusion]:
        return self.repo.read_by_company_id(self._require_company_id())

    def read_by_public_id(self, public_id: str) -> Optional[AssetAccountExclusion]:
        row = self.repo.read_by_public_id(public_id)
        if row:
            self._assert_company_access(row)
        return row

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[AssetAccountExclusion]:
        row = self.read_by_public_id(public_id)
        if row and row.id:
            return self.repo.delete_by_id(int(row.id))
        return None
