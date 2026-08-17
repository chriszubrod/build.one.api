"""Pure-logic tests for VendorVendor heal-don't-delete mapping fixes (U-214 / audit P1-08 + P1-09)."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.base.field_ownership import (
    BOTH_EDITABLE,
    for_entity,
    preserve_human_edited_name,
)
from integrations.intuit.qbo.vendor.connector.vendor.business.model import VendorVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import (
    ADDRESS_TYPE_BILLING,
    VendorVendorConnector,
)
from entities.vendor_address.business.model import VendorAddress


def _make_qbo_vendor(
    *,
    vendor_id=1,
    qbo_id="QBO-200",
    display_name="Acme Supply Co",
    realm_id="realm-1",
    bill_addr_id=50,
):
    return SimpleNamespace(
        id=vendor_id,
        qbo_id=qbo_id,
        display_name=display_name,
        realm_id=realm_id,
        bill_addr_id=bill_addr_id,
        active=None,
    )


def _make_mapping(*, mapping_id=10, vendor_id=100, qbo_vendor_id=1):
    return VendorVendor(
        id=mapping_id,
        public_id="map-pub-10",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        vendor_id=vendor_id,
        qbo_vendor_id=qbo_vendor_id,
    )


def _make_vendor(
    *,
    vendor_id=100,
    public_id="vendor-pub-100",
    name="Acme Supply Co",
):
    return SimpleNamespace(
        id=vendor_id,
        public_id=public_id,
        name=name,
    )


def _build_vendor_vendor_connector():
    mapping_repo = Mock()
    vendor_service = Mock()
    vendor_service.repo = Mock()
    vendor_address_service = Mock()
    address_connector = Mock()
    reconciliation_repo = Mock()
    connector = VendorVendorConnector(
        mapping_repo=mapping_repo,
        vendor_service=vendor_service,
        vendor_address_service=vendor_address_service,
        address_connector=address_connector,
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector


def test_heal_raises_and_records_issue_when_vendor_missing_and_no_name_match():
    """Mapping exists + read_by_id None + no name match — preserve mapping, no create."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Missing Name Vendor")
    mapping = _make_mapping(vendor_id=999)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.vendor_service.create.assert_not_called()
    connector.mapping_repo.update_by_id.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    call_kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert call_kwargs["drift_type"] == "orphaned_vendor_vendor_mapping"
    assert call_kwargs["severity"] == "critical"


def test_reconciliation_insert_failure_does_not_suppress_raise():
    """Reconciliation insert failure must not swallow the deterministic heal raise."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Missing Name Vendor")
    mapping = _make_mapping(vendor_id=999)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None
    connector.reconciliation_repo.create.side_effect = Exception("recon insert failed")

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    connector.reconciliation_repo.create.assert_called_once()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.vendor_service.create.assert_not_called()


def test_heal_repoints_mapping_when_vendor_missing_but_name_match_unbound():
    """Missing vendor + name match (unbound) repoints mapping in place and syncs addresses."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor()
    mapping = _make_mapping(vendor_id=999)
    replacement = _make_vendor(vendor_id=200)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = replacement
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.vendor_service.repo.update_by_id.side_effect = lambda v: v

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is replacement
    assert mapping.vendor_id == 200
    connector.mapping_repo.update_by_id.assert_called_once_with(mapping)
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.vendor_service.create.assert_not_called()
    connector._sync_addresses.assert_called_once()


def test_heal_raises_duplicate_when_replacement_bound_to_other_qbo_vendor():
    """Name match finds Vendor bound to a different QboVendor — record duplicate, raise."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(vendor_id=1)
    mapping = _make_mapping(vendor_id=999, qbo_vendor_id=1)
    replacement = _make_vendor(vendor_id=200)
    other_mapping = _make_mapping(mapping_id=20, vendor_id=200, qbo_vendor_id=99)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = replacement
    connector.mapping_repo.read_by_vendor_id.return_value = other_mapping

    with pytest.raises(ValueError, match="already bound to QboVendor"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    connector.mapping_repo.update_by_id.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.vendor_service.create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_vendor"


def test_heal_skips_repoint_when_name_match_same_vendor_id():
    """Name match resolves to the same vendor_id the mapping already holds — no repoint."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Same Id Vendor")
    mapping = _make_mapping(vendor_id=100)
    replacement = _make_vendor(vendor_id=100, name="Same Id Vendor")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = replacement
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.vendor_service.repo.update_by_id.side_effect = lambda v: v

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is replacement
    connector.mapping_repo.update_by_id.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.vendor_service.create.assert_not_called()
    connector._sync_addresses.assert_called_once()


def test_normal_update_preserves_curated_name_and_skips_repo_update():
    """Curated local name wins over QBO DisplayName — no update_by_id, addresses still synced."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="VOLUNTEER GLASS INC")
    mapping = _make_mapping(vendor_id=100)
    vendor = _make_vendor(vendor_id=100, name="Volunteer Glass Co")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = vendor

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result.name == "Volunteer Glass Co"
    connector.vendor_service.repo.update_by_id.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector._sync_addresses.assert_called_once()
    connector.reconciliation_repo.create.assert_not_called()


@pytest.mark.parametrize("stored_name", ["", "   "])
def test_normal_update_fills_blank_stored_name_from_qbo_display_name(stored_name):
    """Blank stored name (empty or whitespace-only) fills from QBO DisplayName via update_by_id."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Fresh From QBO")
    mapping = _make_mapping(vendor_id=100)
    vendor = _make_vendor(vendor_id=100, name=stored_name)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = vendor

    def _persist(v):
        v.name = "Fresh From QBO"
        return v

    connector.vendor_service.repo.update_by_id.side_effect = _persist

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    connector.vendor_service.repo.update_by_id.assert_called_once()
    assert result.name == "Fresh From QBO"
    connector._sync_addresses.assert_called_once()


def test_create_path_propagates_mapping_value_error():
    """create_mapping ValueError is not swallowed on the create path."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Brand New Vendor")
    created = _make_vendor(vendor_id=300, name="Brand New Vendor")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None
    connector.vendor_service.create.return_value = created
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.mapping_repo.create.side_effect = ValueError("mapping conflict")

    with pytest.raises(ValueError, match="mapping conflict"):
        connector.sync_from_qbo_vendor(qbo_vendor)


@pytest.mark.parametrize("display_name", ["Existing Local", " Existing Local "])
def test_create_path_adopts_unmapped_local_vendor_by_name(display_name):
    """No mapping — bind existing unmapped local Vendor by stripped DisplayName instead of create."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name=display_name)
    existing = _make_vendor(vendor_id=400, name="Existing Local")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = existing
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.mapping_repo.create.return_value = _make_mapping(vendor_id=400, qbo_vendor_id=1)

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is existing
    connector.vendor_service.read_by_name.assert_called_once_with("Existing Local")
    connector.vendor_service.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.mapping_repo.create.assert_called_once_with(vendor_id=400, qbo_vendor_id=1)
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 400)


def test_normal_update_blank_stored_name_no_qbo_display_name_skips_name_write():
    """Whitespace-only stored name + no QBO DisplayName must not call update_by_id with blank."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name=None)
    mapping = _make_mapping(vendor_id=100)
    vendor = _make_vendor(vendor_id=100, name="   ")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = vendor

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is vendor
    connector.vendor_service.repo.update_by_id.assert_not_called()
    connector._sync_addresses.assert_called_once()


@pytest.mark.parametrize("display_name", [None, "   "])
def test_create_path_raises_and_records_issue_when_display_name_blank_no_mapping(display_name):
    """No mapping + blank DisplayName — record issue and raise before create."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name=display_name)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None

    with pytest.raises(ValueError, match="blank DisplayName and no local mapping"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "blank_display_name_qbo_vendor"
    connector.vendor_service.create.assert_not_called()


def test_create_path_raises_duplicate_when_local_name_already_mapped():
    """No mapping — name-matched local Vendor already bound to another QboVendor."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(vendor_id=2, display_name="Dup Name")
    existing = _make_vendor(vendor_id=500, name="Dup Name")
    existing_map = _make_mapping(mapping_id=30, vendor_id=500, qbo_vendor_id=99)

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = existing
    connector.mapping_repo.read_by_vendor_id.return_value = existing_map

    with pytest.raises(ValueError, match="already bound"):
        connector.sync_from_qbo_vendor(qbo_vendor)

    connector.vendor_service.create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_vendor"


def test_create_path_happy_path_creates_vendor_mapping_and_syncs_addresses():
    """No mapping and no name match — create vendor, mapping, sync addresses."""
    connector = _build_vendor_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Totally New")
    created = _make_vendor(vendor_id=600, name="Totally New")

    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None
    connector.vendor_service.create.return_value = created
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.mapping_repo.create.return_value = _make_mapping(vendor_id=600, qbo_vendor_id=1)

    result = connector.sync_from_qbo_vendor(qbo_vendor)

    assert result is created
    connector.vendor_service.create.assert_called_once_with(
        name="Totally New",
        abbreviation=None,
        is_draft=False,
    )
    connector.mapping_repo.create.assert_called_once_with(vendor_id=600, qbo_vendor_id=1)
    connector._sync_addresses.assert_called_once_with(qbo_vendor, 600)


def test_preserve_human_edited_name_cases():
    """Unit tests for preserve_human_edited_name pure helper."""
    assert preserve_human_edited_name("Local Curated", "QBO DISPLAY") == "Local Curated"
    assert preserve_human_edited_name(None, "From QBO") == "From QBO"
    assert preserve_human_edited_name("", "From QBO") == "From QBO"
    assert preserve_human_edited_name("   ", "From QBO") == "From QBO"
    assert preserve_human_edited_name(None, None) is None


def test_for_entity_vendor_field_ownership_registry():
    """Vendor registry key resolves and name is both_editable (typo in _REGISTRY would KeyError)."""
    rules = for_entity("Vendor")
    assert rules.ownership_of("name") == BOTH_EDITABLE


def _make_vendor_address(*, va_id=1, vendor_id=100, address_id=10, address_type_id=ADDRESS_TYPE_BILLING):
    return VendorAddress(
        id=str(va_id),
        public_id="va-pub-1",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        vendor_id=str(vendor_id),
        address_id=str(address_id),
        address_type_id=str(address_type_id),
    )


def test_ensure_vendor_address_uses_read_all_by_vendor_id_not_read_by_vendor_id():
    connector = _build_vendor_vendor_connector()
    connector.vendor_address_service.read_all_by_vendor_id.return_value = []

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=ADDRESS_TYPE_BILLING)

    connector.vendor_address_service.read_by_vendor_id.assert_not_called()
    connector.vendor_address_service.read_all.assert_not_called()
    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_called_once_with(
        vendor_id="100",
        address_id="20",
        address_type_id=str(ADDRESS_TYPE_BILLING),
    )


def test_ensure_vendor_address_updates_when_existing_differs():
    connector = _build_vendor_vendor_connector()
    existing = _make_vendor_address(vendor_id=100, address_id=10)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [existing]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=ADDRESS_TYPE_BILLING)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_not_called()
    connector.vendor_address_service.repo.update_by_id.assert_called_once_with(existing)
    assert existing.address_id == "20"


def test_ensure_vendor_address_noop_when_existing_same_address():
    connector = _build_vendor_vendor_connector()
    existing = _make_vendor_address(vendor_id=100, address_id=20)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [existing]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=ADDRESS_TYPE_BILLING)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.create.assert_not_called()
    connector.vendor_address_service.repo.update_by_id.assert_not_called()


def test_ensure_vendor_address_creates_when_no_matching_type():
    connector = _build_vendor_vendor_connector()
    other_type = _make_vendor_address(vendor_id=100, address_id=10, address_type_id=99)
    connector.vendor_address_service.read_all_by_vendor_id.return_value = [other_type]

    connector._ensure_vendor_address(vendor_id=100, address_id=20, address_type_id=ADDRESS_TYPE_BILLING)

    connector.vendor_address_service.read_all_by_vendor_id.assert_called_once_with(100)
    connector.vendor_address_service.repo.update_by_id.assert_not_called()
    connector.vendor_address_service.create.assert_called_once_with(
        vendor_id="100",
        address_id="20",
        address_type_id=str(ADDRESS_TYPE_BILLING),
    )
