"""
U-490 — Asset register of record (pure-logic / source pins; no live DB).
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.asset.business.model import Asset
from entities.asset.business.service import AssetService, _serialize_divergence_payload
from entities.asset.persistence.repo import AssetRepository, _decimal_from_db
from shared.access import EntityNotAccessibleError
from shared.authz import set_authz_context

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_SQL = REPO_ROOT / "entities/asset/sql/dbo.asset.sql"


def _asset_table_column_names() -> list[str]:
    text = ASSET_SQL.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE \[dbo\]\.\[Asset\]\s*\((.*?)\);",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match, "dbo.Asset CREATE TABLE block not found"
    body = match.group(1)
    cols = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r"\[(\w+)\]", line)
        if m:
            cols.append(m.group(1))
    return cols


FORBIDDEN_COL_RE = re.compile(
    r"(cost|deprec|book|value|balance|amount|price)",
    re.IGNORECASE,
)


def test_asset_table_has_no_financial_columns():
    """dbo.Asset must not store money — live reads come from qbo.Account only."""
    for col in _asset_table_column_names():
        assert not FORBIDDEN_COL_RE.search(col), f"forbidden financial column name: {col}"


def test_qbo_account_columns_are_nvarchar50():
    text = ASSET_SQL.read_text(encoding="utf-8")
    assert "[QboFixedAssetAccountId] NVARCHAR(50)" in text
    assert "[QboAccumDepAccountId] NVARCHAR(50)" in text
    assert "[QboLiabilityAccountId] NVARCHAR(50)" in text
    assert "[QboAccountId] NVARCHAR(50)" in text


def test_qbo_account_sproc_params_are_nvarchar_not_int():
    """Column declarations alone are not enough — an INT @param reintroduces the
    keyspace bug at the boundary even when the column is NVARCHAR, because the
    value is coerced before it ever reaches the column."""
    text = ASSET_SQL.read_text(encoding="utf-8")
    for param in (
        "@QboFixedAssetAccountId",
        "@QboAccumDepAccountId",
        "@QboLiabilityAccountId",
        "@QboAccountId",
    ):
        # Only DECLARATION sites: the param name followed by a real SQL type
        # keyword. Matching any bare word would also capture body references such
        # as "@Param IS NULL" inside the CASE-WHEN update pattern.
        decls = re.findall(
            rf"{re.escape(param)}\s+(NVARCHAR|VARCHAR|NCHAR|CHAR|INT|BIGINT|SMALLINT|TINYINT|DECIMAL|NUMERIC)\b",
            text,
            re.IGNORECASE,
        )
        assert decls, f"no declaration found for {param}"
        for dtype in decls:
            assert dtype.upper() == "NVARCHAR", f"{param} declared {dtype}, must be NVARCHAR(50)"


def test_qbo_account_ids_are_str_not_int_in_python():
    """The Python layer must carry QboId as str; an int annotation would coerce
    a non-numeric QBO id and silently corrupt the reference."""
    model_src = (Path(__file__).resolve().parent.parent / "entities" / "asset" / "business" / "model.py").read_text(
        encoding="utf-8"
    )
    for field in (
        "qbo_fixed_asset_account_id",
        "qbo_accum_dep_account_id",
        "qbo_liability_account_id",
        "qbo_account_id",
    ):
        for m in re.finditer(rf"{field}\s*:\s*([^\n=]+)", model_src):
            annotation = m.group(1)
            assert "int" not in annotation.lower(), f"{field} annotated {annotation!r}; must be str"


def test_qbo_account_joins_use_qbo_id_not_staging_pk_and_match_realm():
    text = ASSET_SQL.read_text(encoding="utf-8")
    assert "ReadAssetWithQboByPublicId" in text
    with_qbo = text.split("ReadAssetWithQboByPublicId")[1].split("GO")[0]
    assert re.search(r"fa\.\[QboId\]\s*=\s*a\.\[QboFixedAssetAccountId\]", with_qbo)
    assert re.search(r"accum\.\[QboId\]\s*=\s*a\.\[QboAccumDepAccountId\]", with_qbo)
    assert "fa.[RealmId] = co.[RealmId]" in with_qbo
    assert "accum.[RealmId] = co.[RealmId]" in with_qbo
    assert "fa.[Id]" not in with_qbo
    assert "accum.[Id]" not in with_qbo

    div = text.split("ReadAssetDivergenceCheck")[1].split("GO")[0]
    assert "qa.[QboId]" in div
    assert "TRY_CAST(qa.[QboId] AS INT)" not in div
    assert re.search(r"qa\.\[QboId\] = a\.\[QboFixedAssetAccountId\]", div)

    # Assert the realm predicate PER RESULT SET, not across the whole sproc body.
    # A body-wide `"...RealmId..." in div` passes even when one result set has lost
    # its realm scoping, because a sibling set still contains the same substring —
    # so the weaker form would wave through exactly the regression it exists to catch.
    # Each set is delimited by its own ORDER BY terminator.
    segments = re.split(r"ORDER BY [^\n;]+;", div)
    sets = [s for s in segments if "qbo.[Account]" in s or "qbo.Account" in s]
    assert len(sets) == 3, f"expected 3 account-joining result sets, found {len(sets)}"
    for i, seg in enumerate(sets, start=1):
        assert "[RealmId] = co.[RealmId]" in seg, (
            f"divergence result set {i} joins qbo.Account without a RealmId predicate; "
            "a second realm's account with the same QboId would resolve and report the wrong balance"
        )


def test_pooled_accounts_many_to_one_no_unique_on_qbo_fixed_asset_account_id():
    text = ASSET_SQL.read_text(encoding="utf-8")
    assert "UQ_Asset_QboFixedAssetAccountId" not in text
    assert not re.search(
        r"UNIQUE\s+INDEX\s+\w+\s+ON\s+dbo\.\[Asset\]\s*\(\[QboFixedAssetAccountId\]\)",
        text,
        flags=re.IGNORECASE,
    )


def test_exclusion_reason_check_rejects_free_text():
    text = ASSET_SQL.read_text(encoding="utf-8")
    assert "CK_AssetAccountExclusion_Reason" in text
    assert "'leasehold-improvement'" in text
    assert "'parent-rollup-account'" in text


def test_decimal_zero_balance_is_retained_not_dropped():
    assert _decimal_from_db(0) == Decimal("0")
    assert _decimal_from_db(0) is not None
    assert bool(_decimal_from_db(0)) is False  # document why truthiness is unsafe

    row = SimpleNamespace(
        Id=1,
        PublicId="00000000-0000-0000-0000-000000000001",
        RowVersion=b"\x00\x01",
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime=None,
        Name="Test",
        AssetType="equipment",
        Make=None,
        Model=None,
        ModelYear=None,
        SerialNumber=None,
        Status="active",
        AcquisitionDate=None,
        DisposalDate=None,
        QboFixedAssetAccountId="99",
        QboAccumDepAccountId=None,
        CompanyId=1,
        CreatedByUserId=17,
        FixedAssetAccountName="FA",
        FixedAssetAccountBalance=Decimal("0"),
        AccumDepAccountName=None,
        AccumDepAccountBalance=None,
    )
    enriched = AssetRepository()._asset_with_qbo_from_db(row)
    assert enriched is not None
    assert enriched.fixed_asset_account_balance == Decimal("0")


def test_rbac_refuses_cross_company_by_id_read():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    asset = Asset(id=5, public_id="p", company_id=2)
    with pytest.raises(EntityNotAccessibleError):
        AssetService()._assert_company_access(asset)


def test_divergence_check_returns_three_sets():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    mock_repo = MagicMock()
    mock_repo.read_divergence_check.return_value = {
        "unmapped_qbo_fixed_asset_accounts": [
            {"qbo_account_qbo_id": "101", "account_name": "Truck", "account_balance": Decimal("1.00")}
        ],
        "assets_with_orphan_account_ref": [
            {"asset_public_id": "a1", "asset_name": "Orphan", "qbo_fixed_asset_account_id": "999"}
        ],
        "active_assets_with_zero_fixed_asset_balance": [
            {
                "asset_public_id": "a2",
                "asset_name": "Fully depreciated",
                "fixed_asset_account_balance": Decimal("0"),
            }
        ],
    }
    svc = AssetService(repo=mock_repo)
    result = svc.read_divergence_check()
    assert len(result["unmapped_qbo_fixed_asset_accounts"]) == 1
    assert len(result["assets_with_orphan_account_ref"]) == 1
    assert len(result["active_assets_with_zero_fixed_asset_balance"]) == 1
    assert result["active_assets_with_zero_fixed_asset_balance"][0]["fixed_asset_account_balance"] == "0"
    mock_repo.read_divergence_check.assert_called_once_with(1)


def test_serialize_divergence_preserves_zero_balance_string():
    raw = {
        "active_assets_with_zero_fixed_asset_balance": [
            {"fixed_asset_account_balance": Decimal("0")}
        ]
    }
    out = _serialize_divergence_payload(raw)
    assert out["active_assets_with_zero_fixed_asset_balance"][0]["fixed_asset_account_balance"] == "0"


def test_read_divergence_check_sproc_emits_three_top_level_result_sets():
    text = ASSET_SQL.read_text(encoding="utf-8")
    body = text.split("ReadAssetDivergenceCheck")[1].split("END;")[0]
    assert body.count("ORDER BY qa.[Name] ASC;") == 1
    assert body.count("ORDER BY a.[Name] ASC;") == 2
    assert "TRY_CAST" not in body


def test_assets_module_constant_matches_sql_seed():
    from shared.rbac_constants import Modules

    assert Modules.ASSETS == "Assets"
    assert "INSERT INTO dbo.[Module]" in ASSET_SQL.read_text(encoding="utf-8")
    assert "'Assets'" in ASSET_SQL.read_text(encoding="utf-8")
    assert "'/asset/list'" in ASSET_SQL.read_text(encoding="utf-8")


@patch("entities.asset.persistence.repo.get_connection")
def test_repo_divergence_check_consumes_three_result_sets(mock_get_connection):
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [SimpleNamespace(QboAccountQboId="1", AccountName="A", AccountBalance=0)],
        [SimpleNamespace(AssetPublicId="p", AssetName="B", QboFixedAssetAccountId="1")],
        [SimpleNamespace(AssetPublicId="p2", AssetName="C", FixedAssetAccountBalance=0)],
    ]
    cursor.nextset.side_effect = [True, True]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn

    result = AssetRepository().read_divergence_check(1)
    assert len(result["unmapped_qbo_fixed_asset_accounts"]) == 1
    assert len(result["assets_with_orphan_account_ref"]) == 1
    assert len(result["active_assets_with_zero_fixed_asset_balance"]) == 1
    assert result["active_assets_with_zero_fixed_asset_balance"][0]["fixed_asset_account_balance"] == Decimal("0")


def test_instant_workflow_registries_include_all_asset_entities():
    from core.workflow.business.definitions.instant import SYNCHRONOUS_TASKS
    from core.workflow.business.instant import PROCESS_REGISTRY

    for name in (
        "asset",
        "asset_financing_note",
        "asset_account_exclusion",
        "asset_attachment",
    ):
        assert name in SYNCHRONOUS_TASKS
        assert name in PROCESS_REGISTRY


@patch("entities.asset_attachment.persistence.repo.AssetAttachmentRepository.read_by_public_id")
@patch("entities.asset.persistence.repo.AssetRepository.read_by_id")
def test_asset_attachment_delete_refuses_cross_company(
    mock_read_asset_by_id,
    mock_read_link_by_public_id,
):
    from entities.asset_attachment.business.model import AssetAttachment
    from entities.asset_attachment.business.service import AssetAttachmentService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    link = AssetAttachment(id=10, public_id="link-1", asset_id=5)
    mock_read_link_by_public_id.return_value = link
    mock_read_asset_by_id.return_value = Asset(id=5, public_id="asset-1", company_id=2)

    with pytest.raises(EntityNotAccessibleError):
        AssetAttachmentService().delete_by_public_id("link-1")


def test_asset_update_raises_on_stale_row_version():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    mock_repo = MagicMock()
    existing = Asset(
        id=1,
        public_id="p1",
        company_id=1,
        row_version="old",
        name="Truck",
        asset_type="vehicle",
        status="active",
    )
    mock_repo.read_by_public_id.return_value = existing
    mock_repo.update_by_id.return_value = None
    svc = AssetService(repo=mock_repo)

    with pytest.raises(ValueError, match="Concurrency conflict"):
        svc.update_by_public_id("p1", row_version="stale", name="Updated")


def test_asset_financing_note_has_no_company_id_column():
    """The child must derive its owning company from its parent asset, never carry its own.

    An earlier revision stamped a CompanyId on this table while
    `CreateAssetFinancingNote` took @AssetId and @CompanyId independently, so nothing
    made them agree -- an authoritative-looking column that authorization could not
    trust. It was removed before first deploy. Re-adding it would silently reintroduce
    a second, divergent source for the tenant boundary.
    """
    text = ASSET_SQL.read_text(encoding="utf-8")
    start = text.index("CREATE TABLE [dbo].[AssetFinancingNote]")
    body = text[start : text.index(");", start)]
    assert "[CompanyId]" not in body, "dbo.AssetFinancingNote must not carry its own CompanyId"

    # ...and no sproc may accept or project one for it either.
    for sproc in (
        "CreateAssetFinancingNote",
        "ReadAssetFinancingNotesByAssetId",
        "ReadAssetFinancingNoteByPublicId",
        "DeleteAssetFinancingNoteById",
    ):
        s = text.index(f"CREATE OR ALTER PROCEDURE {sproc}")
        seg = text[s : text.index("\nGO", s)]
        assert "CompanyId" not in seg, f"{sproc} still references CompanyId"
