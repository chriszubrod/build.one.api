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


def _stringify_decimals(row: dict) -> dict:
    """Money crosses the wire as a string, never a float.

    Detects by TYPE rather than by a hard-coded field list: a list has to be
    maintained, and the failure mode when someone adds a QBO-enriched money
    field and forgets it is silent — a raw Decimal reaches jsonable_encoder and
    is rendered as a float, which is the Decimal->float corruption class this
    codebase treats as a top defect. `Decimal("0")` is falsy, so the guard is
    an isinstance check and never a truthiness test.
    """
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def _serialize_asset_with_qbo(asset: AssetWithQbo) -> dict:
    return _stringify_decimals(asset.to_dict())


def require_company_id(operation: str = "asset operations") -> int:
    cid = current_company_id.get()
    if cid is None:
        raise ValueError(f"Active company is required for {operation}.")
    return int(cid)


def assert_company_scope(entity_name: str, entity_id: Optional[int], owning_company_id: Optional[int]) -> None:
    """The tenant boundary for this entity family — ONE copy, deliberately.

    This was three near-identical private methods. That shape is how a tenancy
    hardening lands in one service and silently not the others: `shared/access.py`
    records exactly such an incident (2026-05-12), where a `current_user_id is
    None -> bypass` branch had to be removed from every copy of the same check.
    Keep this single-sourced.

    NOTE the callers pass the OWNING company, which is not always the row's own
    column — AssetFinancingNote derives it from its parent asset, because its
    own CompanyId is not schema-enforced to agree.
    """
    if current_is_system_admin.get():
        return
    cid = current_company_id.get()
    if cid is None or owning_company_id is None or int(owning_company_id) != int(cid):
        raise EntityNotAccessibleError(entity_name, entity_id or 0)


class AssetService:
    """Service for Asset register operations (read-only against QBO)."""

    def __init__(self, repo: Optional[AssetRepository] = None):
        self.repo = repo or AssetRepository()

    def _require_company_id(self) -> int:
        return require_company_id()

    def _assert_company_access(self, asset: Asset) -> None:
        assert_company_scope("Asset", asset.id, asset.company_id)

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
    return {section: [_stringify_decimals(dict(row)) for row in rows] for section, rows in raw.items()}


class AssetFinancingNoteService:
    def __init__(self, repo: Optional[AssetFinancingNoteRepository] = None):
        self.repo = repo or AssetFinancingNoteRepository()

    def _assert_company_access(self, note: AssetFinancingNote) -> None:
        """Authorize through the PARENT asset — this entity has no CompanyId.

        It deliberately carries none. An earlier revision stamped its own copy, and
        because `CreateAssetFinancingNote` took @AssetId and @CompanyId independently
        nothing made the two agree — so the column looked authoritative while being
        untrustworthy, which is strictly worse than absent. Dropping it leaves exactly
        one place the owning company can come from, and matches AssetAttachment and
        the repo's wider convention for child entities.
        """
        if current_is_system_admin.get():
            return
        if not note.asset_id:
            raise EntityNotAccessibleError("AssetFinancingNote", note.id or 0)
        parent = AssetRepository().read_by_id(int(note.asset_id))
        if parent is None:
            raise EntityNotAccessibleError("AssetFinancingNote", note.id or 0)
        assert_company_scope("AssetFinancingNote", note.id, parent.company_id)

    def create(
        self,
        *,
        tenant_id: int = None,
        asset_public_id: str,
        qbo_liability_account_id: str,
    ) -> AssetFinancingNote:
        # read_by_public_id asserts company access, so reaching here means the
        # caller owns the parent — and the note inherits that scope structurally.
        asset = AssetService().read_by_public_id(asset_public_id)
        if not asset or not asset.id:
            raise ValueError(f"Asset with public_id '{asset_public_id}' not found")
        return self.repo.create(
            asset_id=int(asset.id),
            qbo_liability_account_id=qbo_liability_account_id,
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
        return require_company_id("asset exclusion operations")

    def _assert_company_access(self, row: AssetAccountExclusion) -> None:
        assert_company_scope("AssetAccountExclusion", row.id, row.company_id)

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
