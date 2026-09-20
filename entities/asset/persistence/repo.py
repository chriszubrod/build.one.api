# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Any, Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.asset.business.model import (
    Asset,
    AssetAccountExclusion,
    AssetFinancingNote,
    AssetWithQbo,
)
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _decimal_from_db(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


class AssetRepository:
    """Repository for Asset persistence operations."""

    def _asset_from_db(self, row: pyodbc.Row) -> Optional[Asset]:
        if not row:
            return None
        try:
            return Asset(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId is not None else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                asset_type=row.AssetType,
                make=row.Make,
                model=row.Model,
                model_year=row.ModelYear,
                serial_number=row.SerialNumber,
                status=row.Status,
                acquisition_date=row.AcquisitionDate,
                disposal_date=row.DisposalDate,
                qbo_fixed_asset_account_id=row.QboFixedAssetAccountId,
                qbo_accum_dep_account_id=row.QboAccumDepAccountId,
                company_id=row.CompanyId,
                created_by_user_id=row.CreatedByUserId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during asset mapping: {error}")
            raise map_database_error(error)

    def _asset_with_qbo_from_db(self, row: pyodbc.Row) -> Optional[AssetWithQbo]:
        base = self._asset_from_db(row)
        if not base:
            return None
        return AssetWithQbo(
            **base.to_dict(),
            fixed_asset_account_name=getattr(row, "FixedAssetAccountName", None),
            fixed_asset_account_balance=_decimal_from_db(
                getattr(row, "FixedAssetAccountBalance", None)
            ),
            accum_dep_account_name=getattr(row, "AccumDepAccountName", None),
            accum_dep_account_balance=_decimal_from_db(
                getattr(row, "AccumDepAccountBalance", None)
            ),
        )

    def create(
        self,
        *,
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
        company_id: int,
        created_by_user_id: Optional[int] = None,
    ) -> Asset:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateAsset",
                        params={
                            "Name": name,
                            "AssetType": asset_type,
                            "Make": make,
                            "Model": model,
                            "ModelYear": model_year,
                            "SerialNumber": serial_number,
                            "Status": status,
                            "AcquisitionDate": acquisition_date,
                            "DisposalDate": disposal_date,
                            "QboFixedAssetAccountId": qbo_fixed_asset_account_id,
                            "QboAccumDepAccountId": qbo_accum_dep_account_id,
                            "CompanyId": company_id,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("CreateAsset failed"))
                    return self._asset_from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create asset: {error}")
            raise map_database_error(error)

    def read_by_company_id(self, company_id: int) -> list[Asset]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAssetsByCompanyId",
                        params={"CompanyId": company_id},
                    )
                    rows = cursor.fetchall()
                    return [self._asset_from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read assets by company: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Asset]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAssetById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._asset_from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read asset by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Asset]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAssetByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._asset_from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read asset by public id: {error}")
            raise map_database_error(error)

    def read_with_qbo_by_public_id(self, public_id: str) -> Optional[AssetWithQbo]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAssetWithQboByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._asset_with_qbo_from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read asset with qbo by public id: {error}")
            raise map_database_error(error)

    def update_by_id(self, asset: Asset) -> Optional[Asset]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateAssetById",
                        params={
                            "Id": asset.id,
                            "RowVersion": asset.row_version_bytes,
                            "Name": asset.name,
                            "AssetType": asset.asset_type,
                            "Make": asset.make,
                            "Model": asset.model,
                            "ModelYear": asset.model_year,
                            "SerialNumber": asset.serial_number,
                            "Status": asset.status,
                            "AcquisitionDate": asset.acquisition_date,
                            "DisposalDate": asset.disposal_date,
                            "QboFixedAssetAccountId": asset.qbo_fixed_asset_account_id,
                            "QboAccumDepAccountId": asset.qbo_accum_dep_account_id,
                        },
                    )
                    row = cursor.fetchone()
                    return self._asset_from_db(row)
                except Exception:
                    raise
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during update asset: {error}")
            raise map_database_error(error)

    def delete_cascade_by_id(self, id: int) -> Optional[Asset]:
        """Delete financing notes, attachment links, and the asset in one transaction."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteAssetCascadeById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._asset_from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during cascade delete asset: {error}")
            raise map_database_error(error)

    def read_divergence_check(self, company_id: int) -> dict:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAssetDivergenceCheck",
                        params={"CompanyId": company_id},
                    )
                    unmapped = [
                        {
                            "qbo_account_qbo_id": row.QboAccountQboId,
                            "account_name": row.AccountName,
                            "account_balance": _decimal_from_db(row.AccountBalance),
                        }
                        for row in cursor.fetchall()
                        if row
                    ]
                    if not cursor.nextset():
                        raise RuntimeError("ReadAssetDivergenceCheck missing orphaned-asset set")
                    orphaned = [
                        {
                            "asset_public_id": str(row.AssetPublicId),
                            "asset_name": row.AssetName,
                            "qbo_fixed_asset_account_id": row.QboFixedAssetAccountId,
                        }
                        for row in cursor.fetchall()
                        if row
                    ]
                    if not cursor.nextset():
                        raise RuntimeError("ReadAssetDivergenceCheck missing zero-balance set")
                    zero_balance = [
                        {
                            "asset_public_id": str(row.AssetPublicId),
                            "asset_name": row.AssetName,
                            "fixed_asset_account_balance": _decimal_from_db(
                                row.FixedAssetAccountBalance
                            ),
                        }
                        for row in cursor.fetchall()
                        if row
                    ]
                    return {
                        "unmapped_qbo_fixed_asset_accounts": unmapped,
                        "assets_with_orphan_account_ref": orphaned,
                        "active_assets_with_zero_fixed_asset_balance": zero_balance,
                    }
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during asset divergence check: {error}")
            raise map_database_error(error)


class AssetFinancingNoteRepository:
    def _from_db(self, row: pyodbc.Row) -> Optional[AssetFinancingNote]:
        if not row:
            return None
        return AssetFinancingNote(
            id=row.Id,
            public_id=str(row.PublicId) if row.PublicId is not None else None,
            row_version=base64.b64encode(row.RowVersion).decode("ascii"),
            created_datetime=row.CreatedDatetime,
            modified_datetime=row.ModifiedDatetime,
            asset_id=row.AssetId,
            qbo_liability_account_id=row.QboLiabilityAccountId,
            company_id=row.CompanyId,
            created_by_user_id=row.CreatedByUserId,
        )

    def create(
        self,
        *,
        asset_id: int,
        qbo_liability_account_id: str,
        company_id: int,
        created_by_user_id: Optional[int] = None,
    ) -> AssetFinancingNote:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="CreateAssetFinancingNote",
                    params={
                        "AssetId": asset_id,
                        "QboLiabilityAccountId": qbo_liability_account_id,
                        "CompanyId": company_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateAssetFinancingNote failed"))
                return self._from_db(row)
            finally:
                cursor.close()

    def read_by_asset_id(self, asset_id: int) -> list[AssetFinancingNote]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetFinancingNotesByAssetId",
                    params={"AssetId": asset_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
            finally:
                cursor.close()

    def read_by_public_id(self, public_id: str) -> Optional[AssetFinancingNote]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetFinancingNoteByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
            finally:
                cursor.close()

    def delete_by_id(self, id: int) -> Optional[AssetFinancingNote]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="DeleteAssetFinancingNoteById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
            finally:
                cursor.close()

class AssetAccountExclusionRepository:
    def _from_db(self, row: pyodbc.Row) -> Optional[AssetAccountExclusion]:
        if not row:
            return None
        return AssetAccountExclusion(
            id=row.Id,
            public_id=str(row.PublicId) if row.PublicId is not None else None,
            row_version=base64.b64encode(row.RowVersion).decode("ascii"),
            created_datetime=row.CreatedDatetime,
            modified_datetime=row.ModifiedDatetime,
            qbo_account_id=row.QboAccountId,
            reason=row.Reason,
            company_id=row.CompanyId,
            created_by_user_id=row.CreatedByUserId,
        )

    def create(
        self,
        *,
        qbo_account_id: str,
        reason: str,
        company_id: int,
        created_by_user_id: Optional[int] = None,
    ) -> AssetAccountExclusion:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="CreateAssetAccountExclusion",
                    params={
                        "QboAccountId": qbo_account_id,
                        "Reason": reason,
                        "CompanyId": company_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateAssetAccountExclusion failed"))
                return self._from_db(row)
            finally:
                cursor.close()

    def read_by_company_id(self, company_id: int) -> list[AssetAccountExclusion]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetAccountExclusionsByCompanyId",
                    params={"CompanyId": company_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
            finally:
                cursor.close()

    def read_by_public_id(self, public_id: str) -> Optional[AssetAccountExclusion]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetAccountExclusionByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
            finally:
                cursor.close()

    def delete_by_id(self, id: int) -> Optional[AssetAccountExclusion]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="DeleteAssetAccountExclusionById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
            finally:
                cursor.close()
