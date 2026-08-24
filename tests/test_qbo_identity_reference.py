"""Pure-logic tests for U-238c dbo-native QBO identity on reference entities."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.address.business.model import Address
from integrations.intuit.qbo.base.identity_drift import REFERENCE_ENTITY_SPECS, classify_qbo_identity_drift
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.term.connector.payment_term.business.service import TermPaymentTermConnector
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import VendorCreditBillCreditConnector
from scripts.backfill_qbo_identity_reference import (
    main as backfill_main,
    parse_physical_address_parent_qbo_id,
    resolve_parent_realm_id,
)


@pytest.mark.parametrize(
    "dbo_qbo,dbo_realm,has_mapping,staging_qbo,staging_realm,expected",
    [
        (None, None, False, None, None, "match"),
        (None, None, True, "1", "realm", "pending_backfill"),
        ("99", "realm", False, None, None, "orphan_dbo_value"),
        ("1", "realm", True, "1", "realm", "match"),
        ("1", "realm", True, "2", "realm", "drift"),
    ],
)
def test_classify_qbo_identity_drift_reference_fields(
    dbo_qbo, dbo_realm, has_mapping, staging_qbo, staging_realm, expected
):
    assert classify_qbo_identity_drift(
        dbo_qbo_id=dbo_qbo,
        dbo_realm_id=dbo_realm,
        dbo_sync_token=None,
        has_mapping=has_mapping,
        staging_qbo_id=staging_qbo,
        staging_realm_id=staging_realm,
        staging_sync_token=None,
        has_sync_token=False,
    ) == expected


EXPECTED_REFERENCE_SPECS = {
    "vendor": {
        "label": "Vendor",
        "mapping_table": "VendorVendor",
        "staging_table": "Vendor",
        "dbo_fk_col": "VendorId",
        "staging_fk_col": "QboVendorId",
        "has_sync_token": False,
        "sproc": "SetVendorQboIdentity",
    },
    "customer": {
        "label": "Customer",
        "mapping_table": "CustomerCustomer",
        "staging_table": "Customer",
        "dbo_fk_col": "CustomerId",
        "staging_fk_col": "QboCustomerId",
        "has_sync_token": False,
        "sproc": "SetCustomerQboIdentity",
    },
    "cost_code": {
        "label": "CostCode",
        "mapping_table": "ItemCostCode",
        "staging_table": "Item",
        "dbo_fk_col": "CostCodeId",
        "staging_fk_col": "QboItemId",
        "has_sync_token": False,
        "sproc": "SetCostCodeQboIdentity",
    },
    "sub_cost_code": {
        "label": "SubCostCode",
        "mapping_table": "ItemSubCostCode",
        "staging_table": "Item",
        "dbo_fk_col": "SubCostCodeId",
        "staging_fk_col": "QboItemId",
        "has_sync_token": False,
        "sproc": "SetSubCostCodeQboIdentity",
    },
    "payment_term": {
        "label": "PaymentTerm",
        "mapping_table": "TermPaymentTerm",
        "staging_table": "Term",
        "dbo_fk_col": "PaymentTermId",
        "staging_fk_col": "QboTermId",
        "has_sync_token": False,
        "sproc": "SetPaymentTermQboIdentity",
    },
    "address": {
        "label": "Address",
        "mapping_table": "PhysicalAddressAddress",
        "staging_table": "PhysicalAddress",
        "dbo_fk_col": "AddressId",
        "staging_fk_col": "QboPhysicalAddressId",
        "has_sync_token": False,
        "sproc": "SetAddressQboIdentity",
    },
    "attachment": {
        "label": "Attachment",
        "mapping_table": "AttachableAttachment",
        "staging_table": "Attachable",
        "dbo_fk_col": "AttachmentId",
        "staging_fk_col": "QboAttachableId",
        "has_sync_token": False,
        "sproc": "SetAttachmentQboIdentity",
    },
    "bill_credit": {
        "label": "BillCredit",
        "mapping_table": "VendorCreditBillCredit",
        "staging_table": "VendorCredit",
        "dbo_fk_col": "BillCreditId",
        "staging_fk_col": "QboVendorCreditId",
        "has_sync_token": False,
        "sproc": "SetBillCreditQboIdentity",
    },
}


@pytest.mark.parametrize("key,expected", EXPECTED_REFERENCE_SPECS.items())
def test_reference_entity_specs_topology(key, expected):
    spec = next(s for s in REFERENCE_ENTITY_SPECS if s.key == key)
    assert spec.key == key
    assert spec.label == expected["label"]
    assert spec.mapping_table == expected["mapping_table"]
    assert spec.staging_table == expected["staging_table"]
    assert spec.dbo_fk_col == expected["dbo_fk_col"]
    assert spec.staging_fk_col == expected["staging_fk_col"]
    assert spec.has_sync_token == expected["has_sync_token"]
    assert spec.sproc == expected["sproc"]


@pytest.mark.parametrize(
    "qbo_id,expected",
    [
        ("42_bill", "42"),
        ("99_ship", "99"),
        ("plain", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_physical_address_parent_qbo_id(qbo_id, expected):
    assert parse_physical_address_parent_qbo_id(qbo_id) == expected


@pytest.mark.parametrize(
    "realm_ids,expected_realm,expected_status",
    [
        (frozenset({"r1"}), "r1", "matched"),
        (frozenset(), None, "unmatched"),
        (frozenset({"r1", "r2"}), None, "ambiguous"),
    ],
)
def test_resolve_parent_realm_id(realm_ids, expected_realm, expected_status):
    realm, status = resolve_parent_realm_id(realm_ids)
    assert realm == expected_realm
    assert status == expected_status


@pytest.mark.parametrize(
    "repo_path,sproc,extra_params",
    [
        (
            "entities.vendor.persistence.repo.VendorRepository",
            "SetVendorQboIdentity",
            {"Active": None},
        ),
        (
            "entities.bill_credit.persistence.repo.BillCreditRepository",
            "SetBillCreditQboIdentity",
            {},
        ),
        (
            "entities.payment_term.persistence.repo.PaymentTermRepository",
            "SetPaymentTermQboIdentity",
            {"Active": None},
        ),
        (
            "entities.sub_cost_code.persistence.repo.SubCostCodeRepository",
            "SetSubCostCodeQboIdentity",
            {"Active": None},
        ),
    ],
)
def test_set_qbo_identity_calls_sproc(repo_path, sproc, extra_params):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Id=1)

    with patch(f"{module_path}.get_connection") as mock_conn_ctx, patch(
        f"{module_path}.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.set_qbo_identity(id=42, qbo_id="qbo-1", realm_id="realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == sproc
    assert mock_call.call_args.kwargs["params"] == {
        "Id": 42,
        "QboId": "qbo-1",
        "RealmId": "realm-1",
        **extra_params,
    }


# U-310/U-313: the Customer AND Vendor families' `create_mapping` dual-writes
# are GONE -- CustomerCustomerConnector/VendorVendorConnector no longer touch
# qbo.CustomerCustomer/qbo.VendorVendor at all (each stamps its dbo-native
# identity inside its own `_stamp_*_identity` under the candidate's own app
# lock instead). Their replacement contract -- "the identity stamp happens,
# and a failure to stamp never leaves a half-bound row" -- is covered in
# tests/test_u276_customer_project_qbo_identity_repoint.py's Section 2 and
# tests/test_u290_vendor_qbo_identity_repoint.py's Section 2, which is where
# each family's dbo-only tests now live. PaymentTerm below still has its
# mapping table (not in Wave 5).


def _make_payment_term_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_payment_term_id.return_value = None
    mapping_repo.read_by_qbo_term_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=1)
    payment_term_service = Mock()
    payment_term_service.repo = Mock()
    # U-282: default the direct dbo-identity fast path to a miss, matching the two
    # sibling builders already patched in test_u219_qbo_reference_pulls.py /
    # test_u275_qbo_active_mirror.py — this file's own tests only exercise
    # create_mapping() directly (not sync_from_qbo_term), so it's a no-op today, but
    # keeps the builder consistent if a future test here calls sync_from_qbo_term.
    payment_term_service.read_by_qbo_identity.return_value = None
    connector = TermPaymentTermConnector(
        mapping_repo=mapping_repo, payment_term_service=payment_term_service
    )
    return connector, mapping_repo, payment_term_service.repo


def test_payment_term_create_mapping_dual_writes_identity():
    connector, mapping_repo, repo = _make_payment_term_connector()
    connector.create_mapping(
        payment_term_id=13,
        qbo_term_id=24,
        qbo_id="T-1",
        realm_id="realm-pt",
    )
    repo.set_qbo_identity.assert_called_once_with(
        id=13, qbo_id="T-1", realm_id="realm-pt", active=None
    )
    mapping_repo.create.assert_called_once_with(payment_term_id=13, qbo_term_id=24)


def test_payment_term_create_mapping_identity_failure_propagates():
    connector, mapping_repo, repo = _make_payment_term_connector()
    repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.create_mapping(
            payment_term_id=13,
            qbo_term_id=24,
            qbo_id="T-1",
            realm_id="realm-pt",
        )
    mapping_repo.create.assert_not_called()


def _make_address_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_address_id.return_value = None
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(
        id=1, address_id=1, qbo_physical_address_id=100
    )
    address_service = Mock()
    address_service.repo = Mock()
    qbo_physical_address_service = Mock()
    qbo_physical_address_service.repo = Mock()
    connector = PhysicalAddressAddressConnector(
        mapping_repo=mapping_repo,
        address_service=address_service,
        qbo_physical_address_service=qbo_physical_address_service,
    )
    return connector, mapping_repo, address_service, qbo_physical_address_service


def test_address_create_mapping_dual_writes_identity():
    connector, mapping_repo, address_service, _ = _make_address_connector()
    connector.create_mapping(
        address_id=14,
        qbo_physical_address_id=25,
        qbo_id="A-1",
        realm_id="realm-addr",
    )
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=14, qbo_id="A-1", realm_id="realm-addr"
    )
    mapping_repo.create.assert_called_once_with(address_id=14, qbo_physical_address_id=25)


def test_address_create_mapping_identity_failure_propagates():
    connector, mapping_repo, address_service, _ = _make_address_connector()
    address_service.repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.create_mapping(
            address_id=14,
            qbo_physical_address_id=25,
            qbo_id="A-1",
            realm_id="realm-addr",
        )
    mapping_repo.create.assert_not_called()


def _make_bill_credit_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=1)
    bill_credit_service = Mock()
    bill_credit_service.repo = Mock()
    # U-278: no prior dbo-native identity yet — these tests exercise the CREATE path,
    # which is exactly what a real never-before-synced BillCredit would report.
    bill_credit_service.read_by_qbo_identity.return_value = None
    connector = VendorCreditBillCreditConnector(
        mapping_repo=mapping_repo, bill_credit_service=bill_credit_service
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, bill_credit_service.repo, bill_credit_service


def test_bill_credit_create_path_dual_writes_identity_before_mapping():
    connector, mapping_repo, repo, bill_credit_service = _make_bill_credit_connector()
    bill_credit_service.create.return_value = SimpleNamespace(id=16, public_id="bc-pub")
    qbo_vc = SimpleNamespace(
        id=30,
        qbo_id="VC-1",
        realm_id="realm-bc",
        vendor_ref_value="1",
        doc_number="CN-1",
        txn_date="2026-01-01",
        total_amt="10.00",
        private_note="note",
    )
    call_order = []
    repo.set_qbo_identity.side_effect = lambda **kwargs: call_order.append("stamp")
    mapping_repo.create.side_effect = lambda **kwargs: call_order.append("mapping") or SimpleNamespace(
        id=1
    )

    connector.sync_from_qbo_vendor_credit(qbo_vc, qbo_lines=[SimpleNamespace()])

    repo.set_qbo_identity.assert_called_once_with(id=16, qbo_id="VC-1", realm_id="realm-bc")
    mapping_repo.create.assert_called_once_with(qbo_vendor_credit_id=30, bill_credit_id=16)
    assert call_order == ["stamp", "mapping"]


def test_bill_credit_create_path_identity_failure_propagates_before_mapping():
    connector, mapping_repo, repo, bill_credit_service = _make_bill_credit_connector()
    bill_credit_service.create.return_value = SimpleNamespace(id=16, public_id="bc-pub")
    repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    qbo_vc = SimpleNamespace(
        id=30,
        qbo_id="VC-1",
        realm_id="realm-bc",
        vendor_ref_value="1",
        doc_number="CN-1",
        txn_date="2026-01-01",
        total_amt="10.00",
        private_note="note",
    )
    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, qbo_lines=[SimpleNamespace()])
    mapping_repo.create.assert_not_called()


def test_address_sync_does_not_overwrite_identity_on_shared_street_city_cannot_remap():
    """Two QboPhysicalAddress rows share street/city; second sync must not restamp the first."""
    mapping_repo = Mock()
    address_service = Mock()
    address_service.repo = Mock()
    qbo_physical_address_service = Mock()
    qbo_repo = qbo_physical_address_service.repo

    connector = PhysicalAddressAddressConnector(
        mapping_repo=mapping_repo,
        address_service=address_service,
        qbo_physical_address_service=qbo_physical_address_service,
    )

    shared_address = Address(
        id="1",
        public_id="addr-pub",
        row_version=None,
        created_datetime=None,
        modified_datetime="2026-01-01 00:00:00",
        street_one="123 Main",
        street_two="",
        city="Austin",
        state="TX",
        zip="78701",
        country=None,
    )
    updated_address = Address(
        id="1",
        public_id="addr-pub",
        row_version=None,
        created_datetime=None,
        modified_datetime="2026-01-01 00:00:00",
        street_one="123 Main",
        street_two="",
        city="Austin",
        state="TX",
        zip="78701",
        country=None,
    )

    qbo_bill = QboPhysicalAddress(
        id=100,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime="2026-01-02 00:00:00",
        qbo_id="42_bill",
        realm_id="realm-1",
        line1="123 Main",
        line2="",
        city="Austin",
        country=None,
        country_sub_division_code="TX",
        postal_code="78701",
    )
    qbo_ship = QboPhysicalAddress(
        id=200,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime="2026-01-03 00:00:00",
        qbo_id="42_ship",
        realm_id="realm-1",
        line1="123 Main",
        line2="",
        city="Austin",
        country=None,
        country_sub_division_code="TX",
        postal_code="78701",
    )

    def read_qbo_by_id(qbo_id):
        return {100: qbo_bill, 200: qbo_ship}[qbo_id]

    qbo_repo.read_by_id.side_effect = read_qbo_by_id

    # U-277 fast path: neither "42_bill" nor "42_ship" has ever been stamped on
    # a dbo.Address yet in this scenario, so the direct dbo-identity lookup
    # must miss for both syncs — exercising the pre-existing mapping-table /
    # street-city path this test is actually about, not the new fast path.
    address_service.read_by_qbo_identity.return_value = None

    # First sync: no mapping, create address + mapping via create_mapping.
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    mapping_repo.read_by_address_id.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    address_service.create.return_value = shared_address
    address_service.repo.update_by_id.return_value = updated_address

    connector.sync_from_qbo_to_address(100)

    assert address_service.repo.set_qbo_identity.call_count == 1
    address_service.repo.set_qbo_identity.assert_called_with(
        id=1, qbo_id="42_bill", realm_id="realm-1"
    )

    # Second sync: street/city match finds existing address mapped to the first QBO row.
    mapping_repo.read_by_qbo_physical_address_id.return_value = None
    mapping_repo.read_by_address_id.return_value = SimpleNamespace(
        id=1, address_id=1, qbo_physical_address_id=100
    )
    address_service.read_by_street_one_and_city.return_value = shared_address
    address_service.read_by_id.return_value = shared_address
    address_service.repo.set_qbo_identity.reset_mock()

    connector.sync_from_qbo_to_address(200)

    address_service.repo.set_qbo_identity.assert_not_called()


@patch("scripts.backfill_qbo_identity_reference.assert_cli_system_admin")
@patch("scripts.backfill_qbo_identity_reference.backfill_entity", return_value=False)
def test_backfill_main_returns_nonzero_on_entity_verification_failure(mock_backfill, mock_admin):
    with patch("sys.argv", ["backfill_qbo_identity_reference.py", "--entity", "vendor"]):
        assert backfill_main() == 1
    mock_backfill.assert_called_once()


@patch("scripts.backfill_qbo_identity_reference.assert_cli_system_admin")
@patch("scripts.backfill_qbo_identity_reference.backfill_entity", return_value=True)
@patch("scripts.backfill_qbo_identity_reference._run_fanout_checks", return_value=False)
def test_backfill_main_returns_nonzero_on_fanout_failure(mock_fanout, mock_backfill, mock_admin):
    with patch("sys.argv", ["backfill_qbo_identity_reference.py"]):
        assert backfill_main() == 1
