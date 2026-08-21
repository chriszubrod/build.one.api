"""Pure-logic tests for U-289 (Phase-4 repoint): repoint the `item` connector
family's identity resolution off qbo.Item / qbo.Item{CostCode,SubCostCode} onto
dbo.{CostCode,SubCostCode}'s native QboId/RealmId, via the shared
base.identity_fastpath.run_identity_fastpath() helper (U-287).

Cross-family item-ref resolvers (bill_line_item/expense_line_item/
bill_credit_line_item's inbound ItemRef->SubCostCode lookups; Bill's live
push _get_qbo_item_ref) are explicitly OUT of scope — see
docs/staging_removal_phase4_5_scoping.md §2/§3b/§8 item 10. They read the
qbo.Item{Sub}CostCode mapping tables directly for a different purpose and are
unaffected by this repoint (the mapping rows are still created identically
regardless of which path — fast or legacy — resolves the entity).

Covers:
  1. CostCodeRepository/SubCostCodeRepository.read_by_qbo_identity (sproc call
     shape) + the corresponding Service passthroughs (bare, no row-level RBAC
     — CostCode/SubCostCode have none, matching Customer's shape not
     BillCredit's).
  2. ItemCostCodeConnector's direct-identity fast path: consistent hit skips
     the mapping-table hop and identity re-stamp; missing hit self-heals a
     missing mapping row; conflict (either side) RAISES and writes nothing;
     a self-heal create-race escalates to a recorded conflict; a miss falls
     through to the pre-existing mapping-table path unchanged; no qbo_id
     skips the fast path entirely.
  3. ItemSubCostCodeConnector: the same fast-path shape, PLUS (a) the parent
     CostCode resolution (ParentRef -> qbo_item_repo -> ItemCostCodeRepository)
     still runs first and its cost_code_id correctly reaches apply_fields,
     and (b) the QboActive dbo-native mirror (U-275) is refreshed on every
     fast-path hit via a QboId/RealmId-omitted set_qbo_identity call — the one
     documented deviation from the other fast-path families, since
     SubCostCode is the only family in this batch carrying an Active mirror
     that must stay current even when identity itself hasn't moved.
"""
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.service import (
    ItemCostCodeConnector,
)
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import (
    ItemSubCostCodeConnector,
)

CC_SERVICE = "integrations.intuit.qbo.item.connector.cost_code.business.service"
SCC_SERVICE = "integrations.intuit.qbo.item.connector.sub_cost_code.business.service"


def _make_qbo_item(**overrides):
    defaults = dict(
        id=100,
        public_id="qbo-item-pub-100",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="ITEM-99",
        sync_token="0",
        realm_id="realm-1",
        name="13.01 Rough Carpentry",
        description=None,
        active=True,
        type="Service",
        parent_ref_value=None,
        parent_ref_name=None,
        level=0,
        fully_qualified_name=None,
        sku=None,
        unit_price=None,
        purchase_cost=None,
        taxable=None,
        income_account_ref_value=None,
        income_account_ref_name=None,
        expense_account_ref_value=None,
        expense_account_ref_name=None,
    )
    defaults.update(overrides)
    return QboItem(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_cost_code_repo_read_by_qbo_identity_calls_sproc():
    from entities.cost_code.persistence.repo import CostCodeRepository

    repo = CostCodeRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.cost_code.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.cost_code.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("ITEM-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadCostCodeByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "ITEM-99", "RealmId": "realm-1"}


def test_sub_cost_code_repo_read_by_qbo_identity_calls_sproc():
    from entities.sub_cost_code.persistence.repo import SubCostCodeRepository

    repo = SubCostCodeRepository()
    cursor = Mock()
    cursor.fetchone.return_value = None

    with patch("entities.sub_cost_code.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.sub_cost_code.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("ITEM-10", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadSubCostCodeByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "ITEM-10", "RealmId": "realm-1"}


def test_cost_code_service_read_by_qbo_identity_is_bare_passthrough():
    """CostCode has no row-level RBAC (unlike BillCredit/Project) — the new method
    must be a bare passthrough, matching Customer's template."""
    from entities.cost_code.business.service import CostCodeService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = "sentinel"
    service = CostCodeService(repo=repo)

    result = service.read_by_qbo_identity("ITEM-99", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("ITEM-99", "realm-1")
    assert result == "sentinel"


def test_sub_cost_code_service_read_by_qbo_identity_is_bare_passthrough():
    from entities.sub_cost_code.business.service import SubCostCodeService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = "sentinel"
    service = SubCostCodeService(repo=repo)

    result = service.read_by_qbo_identity("ITEM-10", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("ITEM-10", "realm-1")
    assert result == "sentinel"


# --- Section 2: ItemCostCodeConnector fast path ---


def _build_cost_code_connector():
    mapping_repo = Mock()
    cost_code_service = Mock()
    reconciliation_repo = Mock()
    connector = ItemCostCodeConnector(
        mapping_repo=mapping_repo,
        cost_code_service=cost_code_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, cost_code_service, reconciliation_repo


def test_cost_code_fast_path_hit_consistent_skips_mapping_write_and_restamp():
    connector, mapping_repo, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    direct_hit = Mock(id=55)
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_cost_code_id.return_value = Mock(qbo_item_id=100)
    mapping_repo.read_by_qbo_item_id.return_value = Mock(cost_code_id=55)
    updated = Mock(id=55)
    cost_code_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    mapping_repo.create.assert_not_called()
    # Identity is already correct by construction on the fast path — no re-stamp.
    cost_code_service.repo.set_qbo_identity.assert_not_called()


def test_cost_code_fast_path_hit_missing_self_heals_mapping():
    connector, mapping_repo, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    direct_hit = Mock(id=55)
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_cost_code_id.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = None
    updated = Mock(id=55)
    cost_code_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    mapping_repo.create.assert_called_once_with(cost_code_id=55, qbo_item_id=100)


def test_cost_code_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    """Falling through on a conflict would update the CONFLICTING CostCode and call
    set_qbo_identity on it — SetCostCodeQboIdentity's theft-detection UPDATE applies
    against ANY row carrying that (QboId, RealmId), silently NULLing `direct`'s
    identity. Must hard-stop instead."""
    connector, mapping_repo, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    direct_hit = Mock(id=55)
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_cost_code_id.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = Mock(id=2, cost_code_id=9, qbo_item_id=100)

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_item(qbo_item)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "cost_code_identity_conflict"
    cost_code_service.repo.update_by_id.assert_not_called()
    cost_code_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_cost_code_fast_path_conflict_local_side_raises_no_duplicate_create():
    connector, mapping_repo, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    direct_hit = Mock(id=55)
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_cost_code_id.return_value = Mock(id=3, qbo_item_id=5)
    mapping_repo.read_by_qbo_item_id.return_value = None

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_item(qbo_item)

    reconciliation_repo.create.assert_called_once()
    mapping_repo.create.assert_not_called()
    cost_code_service.repo.update_by_id.assert_not_called()


def test_cost_code_fast_path_self_heal_race_escalates_to_recorded_conflict():
    connector, mapping_repo, cost_code_service, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    direct_hit = Mock(id=55)
    cost_code_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_cost_code_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_item_id.side_effect = [
        None, Mock(id=9, cost_code_id=3, qbo_item_id=100)
    ]
    updated = Mock(id=55)
    cost_code_service.repo.update_by_id.return_value = updated
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "cost_code_identity_conflict"


def test_cost_code_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = None
    cost_code_service.read_by_number.return_value = None
    mapping_repo.read_by_cost_code_id.return_value = None  # create_mapping's 1:1 guard
    created = Mock(id=77)
    cost_code_service.create.return_value = created

    result = connector.sync_from_qbo_item(qbo_item)

    cost_code_service.read_by_qbo_identity.assert_called_once_with("ITEM-99", "realm-1")
    assert result is created
    cost_code_service.create.assert_called_once()


def test_cost_code_fast_path_skipped_entirely_when_no_qbo_id():
    connector, mapping_repo, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id=None)
    mapping_repo.read_by_qbo_item_id.return_value = None
    cost_code_service.read_by_number.return_value = None
    mapping_repo.read_by_cost_code_id.return_value = None  # create_mapping's 1:1 guard
    cost_code_service.create.return_value = Mock(id=1)

    connector.sync_from_qbo_item(qbo_item)

    cost_code_service.read_by_qbo_identity.assert_not_called()


def test_cost_code_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    qbo_side = Mock(id=2, cost_code_id=9, qbo_item_id=100)
    local_side = Mock(id=3, cost_code_id=55, qbo_item_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_item=qbo_item, dbo_cost_code_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "cost_code_identity_conflict"
    assert "55" in kwargs["details"]
    assert "9" in kwargs["details"]
    assert "5" in kwargs["details"]


def test_cost_code_legacy_path_still_stamps_identity_after_apply():
    """Regression coverage: set_qbo_identity is called ONLY by the legacy
    mapping-table path (a mapping-matched row may predate identity stamping) —
    the fast path deliberately never calls it (see the 'consistent' test above)."""
    connector, mapping_repo, cost_code_service, _ = _build_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-99", realm_id="realm-1")
    cost_code_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = Mock(id=1, cost_code_id=55)
    stored = Mock(id=55)
    cost_code_service.read_by_id.return_value = stored
    cost_code_service.repo.update_by_id.return_value = stored

    connector.sync_from_qbo_item(qbo_item)

    cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="ITEM-99", realm_id="realm-1"
    )


# --- Section 3: ItemSubCostCodeConnector fast path ---


def _build_sub_cost_code_connector():
    mapping_repo = Mock()
    sub_cost_code_service = Mock()
    cost_code_mapping_repo = Mock()
    qbo_item_repo = Mock()
    reconciliation_repo = Mock()
    connector = ItemSubCostCodeConnector(
        mapping_repo=mapping_repo,
        sub_cost_code_service=sub_cost_code_service,
        cost_code_mapping_repo=cost_code_mapping_repo,
        qbo_item_repo=qbo_item_repo,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, sub_cost_code_service, cost_code_mapping_repo, qbo_item_repo, reconciliation_repo


def _wire_parent_resolution(qbo_item_repo, cost_code_mapping_repo, *, parent_qbo_item_id=50, cost_code_id=7):
    qbo_item_repo.read_by_qbo_id.return_value = Mock(id=parent_qbo_item_id)
    cost_code_mapping_repo.read_by_qbo_item_id.return_value = Mock(cost_code_id=cost_code_id)


def test_sub_cost_code_fast_path_hit_consistent_refreshes_qbo_active_not_identity():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1", active=False)
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.return_value = Mock(qbo_item_id=100)
    mapping_repo.read_by_qbo_item_id.return_value = Mock(sub_cost_code_id=55)
    updated = Mock(id=55)
    scc_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    mapping_repo.create.assert_not_called()
    # QboId/RealmId omitted (already correct by construction) — but Active is
    # refreshed every tick, since it's the one dbo-native mirror in this batch
    # that can drift without QboId/RealmId moving.
    scc_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id=None, realm_id=None, active=False
    )


def test_sub_cost_code_fast_path_hit_uses_parent_resolved_cost_code_id():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=42)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1")
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.return_value = Mock(qbo_item_id=100)
    mapping_repo.read_by_qbo_item_id.return_value = Mock(sub_cost_code_id=55)
    updated = Mock(id=55)
    scc_service.repo.update_by_id.return_value = updated

    connector.sync_from_qbo_item(qbo_item)

    written = scc_service.repo.update_by_id.call_args.args[0]
    assert written.cost_code_id == 42


def test_sub_cost_code_fast_path_hit_missing_self_heals_mapping():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1", active=True)
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = None
    updated = Mock(id=55)
    scc_service.repo.update_by_id.return_value = updated

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    mapping_repo.create.assert_called_once_with(sub_cost_code_id=55, qbo_item_id=100)


def test_sub_cost_code_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, _, _, reconciliation_repo = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1")
    qbo_side = Mock(id=2, sub_cost_code_id=9, qbo_item_id=100)
    local_side = Mock(id=3, sub_cost_code_id=55, qbo_item_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_item=qbo_item, dbo_sub_cost_code_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "sub_cost_code_identity_conflict"
    assert "55" in kwargs["details"]
    assert "9" in kwargs["details"]
    assert "5" in kwargs["details"]


def test_sub_cost_code_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, reconciliation_repo = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1")
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = Mock(id=2, sub_cost_code_id=9, qbo_item_id=100)

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_item(qbo_item)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "sub_cost_code_identity_conflict"
    scc_service.repo.update_by_id.assert_not_called()
    scc_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_sub_cost_code_fast_path_conflict_local_side_raises_no_duplicate_create():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, reconciliation_repo = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1")
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.return_value = Mock(id=3, qbo_item_id=5)
    mapping_repo.read_by_qbo_item_id.return_value = None

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_item(qbo_item)

    reconciliation_repo.create.assert_called_once()
    mapping_repo.create.assert_not_called()


def test_sub_cost_code_fast_path_self_heal_race_escalates_to_recorded_conflict():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, reconciliation_repo = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1", active=True)
    direct_hit = Mock(id=55)
    scc_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_sub_cost_code_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_item_id.side_effect = [
        None, Mock(id=9, sub_cost_code_id=3, qbo_item_id=100)
    ]
    updated = Mock(id=55)
    scc_service.repo.update_by_id.return_value = updated
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is updated
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "sub_cost_code_identity_conflict"


def test_sub_cost_code_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1")
    scc_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = None
    mapping_repo.read_by_sub_cost_code_id.return_value = None  # create_mapping's 1:1 guard
    connector._match_sub_cost_code_by_number_and_parent = Mock(return_value=None)
    created = Mock(id=77)
    scc_service.create.return_value = created

    result = connector.sync_from_qbo_item(qbo_item)

    scc_service.read_by_qbo_identity.assert_called_once_with("ITEM-10", "realm-1")
    assert result is created
    scc_service.create.assert_called_once()


def test_sub_cost_code_fast_path_skipped_entirely_when_no_qbo_id():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id=None, parent_ref_value="PARENT-1")
    mapping_repo.read_by_qbo_item_id.return_value = None
    mapping_repo.read_by_sub_cost_code_id.return_value = None  # create_mapping's 1:1 guard
    connector._match_sub_cost_code_by_number_and_parent = Mock(return_value=None)
    scc_service.create.return_value = Mock(id=1)

    connector.sync_from_qbo_item(qbo_item)

    scc_service.read_by_qbo_identity.assert_not_called()


def test_sub_cost_code_parent_resolution_still_runs_before_fast_path():
    """The parent-CostCode lookup is untouched by this repoint and must still raise
    before the fast path is even attempted when the parent mapping is missing."""
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    qbo_item_repo.read_by_qbo_id.return_value = Mock(id=50)
    cc_mapping_repo.read_by_qbo_item_id.return_value = None  # parent CostCode not mapped
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1")

    with pytest.raises(ValueError, match="not mapped to a CostCode"):
        connector.sync_from_qbo_item(qbo_item)

    scc_service.read_by_qbo_identity.assert_not_called()


def test_sub_cost_code_legacy_path_still_stamps_identity_after_apply():
    connector, mapping_repo, scc_service, cc_mapping_repo, qbo_item_repo, _ = _build_sub_cost_code_connector()
    _wire_parent_resolution(qbo_item_repo, cc_mapping_repo, cost_code_id=7)
    qbo_item = _make_qbo_item(id=100, qbo_id="ITEM-10", realm_id="realm-1", parent_ref_value="PARENT-1", active=True)
    scc_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_item_id.return_value = Mock(id=1, sub_cost_code_id=55)
    stored = Mock(id=55)
    scc_service.read_by_id.return_value = stored
    scc_service.repo.update_by_id.return_value = stored

    connector.sync_from_qbo_item(qbo_item)

    scc_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="ITEM-10", realm_id="realm-1", active=True
    )
