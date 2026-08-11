"""Pure-logic tests for Item->CostCode/SubCostCode heal-don't-delete mapping fixes (U-218a)."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.base.field_ownership import BOTH_EDITABLE, for_entity
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.model import ItemCostCode
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.model import ItemSubCostCode
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector


def _make_qbo_item(**overrides):
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-I-1",
        sync_token=None,
        realm_id="realm-1",
        name="01 Permits",
        description="desc",
        active=None,
        type=None,
        parent_ref_value=None,
        parent_ref_name=None,
        level=None,
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


def _make_item_cost_code_mapping(*, mapping_id=10, entity_id=100, qbo_item_id=1):
    return ItemCostCode(
        id=mapping_id,
        public_id="map-pub-10",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        cost_code_id=entity_id,
        qbo_item_id=qbo_item_id,
    )


def _make_item_sub_cost_code_mapping(*, mapping_id=10, entity_id=100, qbo_item_id=1):
    return ItemSubCostCode(
        id=mapping_id,
        public_id="map-pub-10",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        sub_cost_code_id=entity_id,
        qbo_item_id=qbo_item_id,
    )


def _make_cost_code(*, entity_id=100, public_id="cc-pub-100", name="Permits"):
    return SimpleNamespace(
        id=entity_id,
        public_id=public_id,
        name=name,
        number="01",
        description="desc",
    )


def _make_sub_cost_code(*, entity_id=100, public_id="scc-pub-100", name="Permits", cost_code_id=10):
    return SimpleNamespace(
        id=entity_id,
        public_id=public_id,
        name=name,
        number="01",
        description="desc",
        cost_code_id=cost_code_id,
    )


def _build_cost_code_connector():
    mapping_repo = Mock()
    cost_code_service = Mock()
    cost_code_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = ItemCostCodeConnector(
        mapping_repo=mapping_repo,
        cost_code_service=cost_code_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector


def _build_sub_cost_code_connector():
    mapping_repo = Mock()
    sub_cost_code_service = Mock()
    sub_cost_code_service.repo = Mock()
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
    connector.qbo_item_repo.read_by_qbo_id.return_value = SimpleNamespace(id=99)
    connector.cost_code_mapping_repo.read_by_qbo_item_id.return_value = SimpleNamespace(cost_code_id=10)
    return connector


def _stub_no_number_match(connector, service_attr):
    """Stub connector so sync finds no SubCostCode/CostCode by number."""
    if service_attr == "sub_cost_code_service":
        connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    else:
        getattr(connector, service_attr).read_by_number.return_value = None


def _stub_number_match(connector, service_attr, replacement):
    """Stub connector so sync resolves replacement by number (parent-scoped for SubCostCode)."""
    if service_attr == "sub_cost_code_service":
        connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = [replacement]
    else:
        getattr(connector, service_attr).read_by_number.return_value = replacement


_REPOINT_CASES = (
    pytest.param(
        _build_cost_code_connector,
        "cost_code_id",
        "cost_code_service",
        _make_cost_code,
        _make_item_cost_code_mapping,
        {"parent_ref_value": None},
        200,
        id="cost_code",
    ),
    pytest.param(
        _build_sub_cost_code_connector,
        "sub_cost_code_id",
        "sub_cost_code_service",
        _make_sub_cost_code,
        _make_item_sub_cost_code_mapping,
        {"parent_ref_value": "parent-qbo-id"},
        200,
        id="sub_cost_code",
    ),
)


_CASE_ARGS = (
    pytest.param(
        _build_cost_code_connector,
        "cost_code_id",
        "cost_code_service",
        _make_cost_code,
        _make_item_cost_code_mapping,
        {"parent_ref_value": None},
        id="cost_code",
    ),
    pytest.param(
        _build_sub_cost_code_connector,
        "sub_cost_code_id",
        "sub_cost_code_service",
        _make_sub_cost_code,
        _make_item_sub_cost_code_mapping,
        {"parent_ref_value": "parent-qbo-id"},
        id="sub_cost_code",
    ),
)


@pytest.mark.parametrize(
    "connector_builder,mapping_model,entity_attr,read_by_number_attr,service_attr,entity_type,"
    "orphaned_drift,make_entity,make_mapping,qbo_item_kwargs",
    [
        (
            _build_cost_code_connector,
            ItemCostCode,
            "cost_code_id",
            "read_by_number",
            "cost_code_service",
            "CostCode",
            "orphaned_item_cost_code_mapping",
            _make_cost_code,
            _make_item_cost_code_mapping,
            {"parent_ref_value": None},
        ),
        (
            _build_sub_cost_code_connector,
            ItemSubCostCode,
            "sub_cost_code_id",
            "read_by_number",
            "sub_cost_code_service",
            "SubCostCode",
            "orphaned_item_scc_mapping",
            _make_sub_cost_code,
            _make_item_sub_cost_code_mapping,
            {"parent_ref_value": "parent-qbo-id"},
        ),
    ],
    ids=["cost_code", "sub_cost_code"],
)
def test_heal_raises_and_preserves_mapping_when_entity_missing_and_no_number_match(
    connector_builder,
    mapping_model,
    entity_attr,
    read_by_number_attr,
    service_attr,
    entity_type,
    orphaned_drift,
    make_entity,
    make_mapping,
    qbo_item_kwargs,
):
    connector = connector_builder()
    qbo_item = _make_qbo_item(**qbo_item_kwargs)
    mapping = make_mapping(entity_id=999)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    getattr(connector, service_attr).read_by_id.return_value = None
    _stub_no_number_match(connector, service_attr)

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        connector.sync_from_qbo_item(qbo_item)

    connector.mapping_repo.delete_by_id.assert_not_called()
    getattr(connector, service_attr).create.assert_not_called()
    connector.mapping_repo.update_by_id.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == orphaned_drift


@pytest.mark.parametrize(
    "connector_builder,entity_attr,service_attr,make_entity,make_mapping,qbo_item_kwargs,replacement_id",
    _REPOINT_CASES,
)
def test_heal_repoints_mapping_when_entity_missing_but_number_match_unbound(
    connector_builder,
    entity_attr,
    service_attr,
    make_entity,
    make_mapping,
    qbo_item_kwargs,
    replacement_id,
):
    connector = connector_builder()
    qbo_item = _make_qbo_item(**qbo_item_kwargs)
    mapping = make_mapping(entity_id=999)
    replacement = make_entity(entity_id=replacement_id)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    getattr(connector, service_attr).read_by_id.return_value = None
    _stub_number_match(connector, service_attr, replacement)
    getattr(connector.mapping_repo, "read_by_" + entity_attr).return_value = None
    getattr(connector, service_attr).repo.update_by_id.side_effect = lambda e: e

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is replacement
    assert getattr(mapping, entity_attr) == replacement_id
    connector.mapping_repo.update_by_id.assert_called_once_with(mapping)
    connector.mapping_repo.delete_by_id.assert_not_called()
    getattr(connector, service_attr).create.assert_not_called()


@pytest.mark.parametrize(
    "connector_builder,entity_attr,service_attr,make_entity,make_mapping,qbo_item_kwargs",
    _CASE_ARGS,
)
def test_heal_raises_duplicate_when_replacement_bound_to_other_qbo_item(
    connector_builder,
    entity_attr,
    service_attr,
    make_entity,
    make_mapping,
    qbo_item_kwargs,
):
    connector = connector_builder()
    qbo_item = _make_qbo_item(**qbo_item_kwargs)
    mapping = make_mapping(entity_id=999, qbo_item_id=1)
    replacement = make_entity(entity_id=200)
    other_mapping = make_mapping(mapping_id=20, entity_id=200, qbo_item_id=99)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    getattr(connector, service_attr).read_by_id.return_value = None
    _stub_number_match(connector, service_attr, replacement)
    getattr(connector.mapping_repo, "read_by_" + entity_attr).return_value = other_mapping

    with pytest.raises(ValueError, match="already bound to QboItem"):
        connector.sync_from_qbo_item(qbo_item)

    connector.mapping_repo.update_by_id.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    getattr(connector, service_attr).create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_item"


@pytest.mark.parametrize(
    "connector_builder,service_attr,make_entity,qbo_item_kwargs",
    [
        (_build_cost_code_connector, "cost_code_service", _make_cost_code, {"parent_ref_value": None}),
        (
            _build_sub_cost_code_connector,
            "sub_cost_code_service",
            _make_sub_cost_code,
            {"parent_ref_value": "parent-qbo-id"},
        ),
    ],
    ids=["cost_code", "sub_cost_code"],
)
def test_create_path_propagates_mapping_value_error(
    connector_builder, service_attr, make_entity, qbo_item_kwargs
):
    connector = connector_builder()
    qbo_item = _make_qbo_item(**qbo_item_kwargs)
    created = make_entity(entity_id=300)

    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    _stub_no_number_match(connector, service_attr)
    getattr(connector, service_attr).create.return_value = created
    entity_attr = "cost_code_id" if service_attr == "cost_code_service" else "sub_cost_code_id"
    getattr(connector.mapping_repo, "read_by_" + entity_attr).return_value = None
    connector.mapping_repo.create.side_effect = ValueError("mapping conflict")

    with pytest.raises(ValueError, match="mapping conflict"):
        connector.sync_from_qbo_item(qbo_item)


def test_for_entity_cost_code_and_sub_cost_code_field_ownership_registry():
    """CostCode/SubCostCode registry keys resolve and name is both_editable."""
    assert for_entity("CostCode").ownership_of("name") == BOTH_EDITABLE
    assert for_entity("SubCostCode").ownership_of("name") == BOTH_EDITABLE


def test_sub_cost_code_heal_ignores_number_match_with_wrong_parent_cost_code():
    """Heal must not re-parent a SubCostCode whose number matches but CostCodeId differs."""
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(parent_ref_value="parent-qbo-id")
    mapping = _make_item_sub_cost_code_mapping(entity_id=999)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.sub_cost_code_service.read_by_id.return_value = None
    # Wrong-parent row lives under CostCode 99; parent-scoped lookup on 10 finds nothing.
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = []

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        connector.sync_from_qbo_item(qbo_item)

    connector.mapping_repo.update_by_id.assert_not_called()
    connector.sub_cost_code_service.create.assert_not_called()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "orphaned_item_scc_mapping"


@pytest.mark.parametrize(
    "connector_builder,entity_attr,service_attr,make_entity,make_mapping,qbo_item_kwargs",
    _CASE_ARGS,
)
def test_heal_inactive_qbo_item_raises_without_repoint_or_mutating_replacement(
    connector_builder,
    entity_attr,
    service_attr,
    make_entity,
    make_mapping,
    qbo_item_kwargs,
):
    """Heal branch must refuse inactive QboItems before number lookup can hijack a live row."""
    connector = connector_builder()
    qbo_item = _make_qbo_item(active=False, **qbo_item_kwargs)
    mapping = make_mapping(entity_id=999)
    replacement = make_entity(entity_id=200)
    replacement.name = "Live Permits"
    replacement.description = "keep me"

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    getattr(connector, service_attr).read_by_id.return_value = None
    _stub_number_match(connector, service_attr, replacement)
    getattr(connector.mapping_repo, "read_by_" + entity_attr).return_value = None

    original_mapping_target = getattr(mapping, entity_attr)
    original_replacement_name = replacement.name
    original_replacement_description = replacement.description

    with pytest.raises(ValueError, match="mapping exists but its bound row is missing"):
        connector.sync_from_qbo_item(qbo_item)

    assert getattr(mapping, entity_attr) == original_mapping_target
    assert replacement.name == original_replacement_name
    assert replacement.description == original_replacement_description
    connector.mapping_repo.update_by_id.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.mapping_repo.create.assert_not_called()
    getattr(connector, service_attr).repo.update_by_id.assert_not_called()
    getattr(connector, service_attr).create.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


@pytest.mark.parametrize(
    "connector_builder,entity_attr,service_attr,make_entity,make_mapping,qbo_item_kwargs,replacement_id",
    _REPOINT_CASES,
)
def test_heal_active_qbo_item_repoints_when_replacement_resolvable(
    connector_builder,
    entity_attr,
    service_attr,
    make_entity,
    make_mapping,
    qbo_item_kwargs,
    replacement_id,
):
    """Heal branch with active=True must still repoint — guard polarity must not invert."""
    connector = connector_builder()
    qbo_item = _make_qbo_item(active=True, **qbo_item_kwargs)
    mapping = make_mapping(entity_id=999)
    replacement = make_entity(entity_id=replacement_id)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    getattr(connector, service_attr).read_by_id.return_value = None
    _stub_number_match(connector, service_attr, replacement)
    getattr(connector.mapping_repo, "read_by_" + entity_attr).return_value = None
    getattr(connector, service_attr).repo.update_by_id.side_effect = lambda e: e

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is replacement
    assert getattr(mapping, entity_attr) == replacement_id
    connector.mapping_repo.update_by_id.assert_called_once_with(mapping)
    connector.mapping_repo.delete_by_id.assert_not_called()
    getattr(connector, service_attr).create.assert_not_called()


def test_sub_cost_code_heal_finds_correct_parent_when_same_number_under_both_parents():
    """Heal must pick the SubCostCode under the synced parent, not a same-number sibling elsewhere."""
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(parent_ref_value="parent-qbo-id")
    mapping = _make_item_sub_cost_code_mapping(entity_id=999)
    correct_parent = _make_sub_cost_code(entity_id=200, cost_code_id=10)

    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.sub_cost_code_service.read_by_id.return_value = None
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = [correct_parent]
    connector.mapping_repo.read_by_sub_cost_code_id.return_value = None
    connector.sub_cost_code_service.repo.update_by_id.side_effect = lambda e: e

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is correct_parent
    assert mapping.sub_cost_code_id == 200
    connector.mapping_repo.update_by_id.assert_called_once_with(mapping)
    connector.sub_cost_code_service.create.assert_not_called()
    connector.sub_cost_code_service.read_by_number.assert_not_called()


def test_sub_cost_code_adopt_finds_correct_parent_when_same_number_under_both_parents():
    """Adopt must bind the SubCostCode under the synced parent when the number is shared globally."""
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(parent_ref_value="parent-qbo-id")
    correct_parent = _make_sub_cost_code(entity_id=200, cost_code_id=10)

    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = [correct_parent]
    connector.mapping_repo.read_by_sub_cost_code_id.return_value = None
    connector.sub_cost_code_service.repo.update_by_id.side_effect = lambda e: e
    connector.mapping_repo.create.return_value = _make_item_sub_cost_code_mapping(
        entity_id=200, qbo_item_id=qbo_item.id
    )

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is correct_parent
    connector.sub_cost_code_service.create.assert_not_called()
    connector.mapping_repo.create.assert_called_once()
    connector.sub_cost_code_service.read_by_number.assert_not_called()


def test_sub_cost_code_adopt_ignores_number_match_with_wrong_parent_cost_code():
    """Adopt must not bind a SubCostCode whose number matches but CostCodeId differs."""
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(parent_ref_value="parent-qbo-id")
    created = _make_sub_cost_code(entity_id=300, cost_code_id=10)

    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    # Wrong-parent row is not a child of CostCode 10 — parent-scoped lookup finds nothing.
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    connector.sub_cost_code_service.create.return_value = created
    connector.mapping_repo.read_by_sub_cost_code_id.return_value = None
    connector.mapping_repo.create.return_value = _make_item_sub_cost_code_mapping(
        entity_id=300, qbo_item_id=qbo_item.id
    )

    result = connector.sync_from_qbo_item(qbo_item)

    assert result is created
    connector.sub_cost_code_service.create.assert_called_once()
    connector.mapping_repo.create.assert_called_once()
