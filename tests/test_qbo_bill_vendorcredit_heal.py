"""Pure-logic tests for U-031 — Bill + VendorCredit "heal-don't-delete" mapping fix.

Completes the "rule of three": applies the U-029 Purchase->Expense heal pattern
(itself the U-022 CustomerProject pattern) to the two remaining pull connectors
whose empty-read branch could mint a duplicate:

  * Bill        (integrations/intuit/qbo/bill/connector/bill) — the else-branch
                previously `mapping_repo.delete_by_id(...)` + fell through to CREATE.
  * BillCredit  (integrations/intuit/qbo/vendorcredit/connector/bill_credit) — the
                empty-read did NOT delete the mapping but silently FELL THROUGH to
                "Step 3: Create new BillCredit" (orphan-mapping + duplicate-create).

Design (neither entity has a unique NAME key, and neither mapping repo has a
repoint sproc — this unit forbids SQL migrations):
  - Re-resolve by the natural (number, vendor) fingerprint BEFORE mutating.
  - Heal in place ONLY when the fingerprint re-binds to the SAME entity id the
    mapping already targets (a confirmed transient empty-read); the UPDATE routes
    the number through preserve_human_edited_ref exactly like the happy path.
  - Otherwise record a critical [qbo].[ReconciliationIssue] and RAISE ValueError,
    mutating nothing (mapping preserved, nothing created/deleted). The same-id gate
    makes a wrong/duplicate row from a non-TOP-1 fingerprint proc safe.

Mocks stand in for services + repos so no DB/QBO I/O runs; line syncing is stubbed.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


# ===========================================================================
# Bill — bill/connector/bill/business/service.py
# ===========================================================================

from integrations.intuit.qbo.bill.connector.bill.business.service import (
    BillBillConnector,
)

BILL_SERVICE = "integrations.intuit.qbo.bill.connector.bill.business.service"


def _make_qbo_bill(*, qbo_id="88", doc_number="B-5001", realm_id="realm-1"):
    return SimpleNamespace(
        id=801,
        qbo_id=qbo_id,
        realm_id=realm_id,
        vendor_ref_value="qbo-vendor-1",
        doc_number=doc_number,
        txn_date="2026-07-01",
        due_date="2026-07-01",
        private_note="note",
        total_amt=100,
    )


def _make_bill(*, bill_number="B-5001", bill_id=700, public_id="bill-pub-700"):
    return SimpleNamespace(
        id=bill_id,
        public_id=public_id,
        bill_number=bill_number,
        row_version="rowver==",
    )


def _make_bill_mapping(*, mapping_id=1, bill_id=700):
    return SimpleNamespace(id=mapping_id, bill_id=bill_id)


def _build_bill_connector():
    connector = BillBillConnector(
        mapping_repo=Mock(),
        bill_service=Mock(),
        vendor_service=Mock(),
        vendor_vendor_repo=Mock(),
        qbo_vendor_repo=Mock(),
        qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(),
        bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
        qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(),
        qbo_term_repo=Mock(),
        reconciliation_repo=Mock(),
    )
    # Short-circuit vendor resolution + line syncing — these tests only care about
    # the empty-read heal/skip decision.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector


def _drive_bill(connector, qbo_bill):
    with patch(f"{BILL_SERVICE}.guard_lines_present"):
        return connector.sync_from_qbo_bill(qbo_bill, [])


# --- (a) transient empty-read: fingerprint re-binds SAME id -> heal, NO duplicate ---

def test_bill_transient_empty_read_heals_in_place_no_duplicate():
    connector = _build_bill_connector()
    mapping = _make_bill_mapping(bill_id=700)
    replacement = _make_bill(bill_id=700)  # same id the mapping targets

    connector.mapping_repo.read_by_qbo_bill_id.return_value = mapping
    connector.bill_service.read_by_id.return_value = None  # transient empty read
    connector.bill_service.read_by_bill_number_and_vendor_public_id.return_value = replacement
    connector.bill_service.update_by_public_id.return_value = replacement

    result = _drive_bill(connector, _make_qbo_bill())

    assert result is replacement
    connector.bill_service.update_by_public_id.assert_called_once()
    connector.bill_service.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


# --- (b) genuinely missing: fingerprint miss -> record issue + RAISE, no mutate ---

def test_bill_genuinely_missing_records_issue_and_raises():
    connector = _build_bill_connector()

    connector.mapping_repo.read_by_qbo_bill_id.return_value = _make_bill_mapping(bill_id=700)
    connector.bill_service.read_by_id.return_value = None
    connector.bill_service.read_by_bill_number_and_vendor_public_id.return_value = None

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_bill(connector, _make_qbo_bill())

    connector.reconciliation_repo.create.assert_called_once()
    kw = connector.reconciliation_repo.create.call_args.kwargs
    assert kw["drift_type"] == "orphaned_bill_bill_mapping"
    assert kw["entity_type"] == "Bill"
    assert kw["severity"] == "critical"
    # No destructive action.
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.bill_service.create.assert_not_called()
    connector.bill_service.update_by_public_id.assert_not_called()


# --- (c) fingerprint matches a DIFFERENT Bill id -> never rebind, record + RAISE ---

def test_bill_fingerprint_different_id_records_issue_and_raises_no_rebind():
    connector = _build_bill_connector()
    other = _make_bill(bill_id=999, public_id="bill-pub-999")  # a DIFFERENT row

    connector.mapping_repo.read_by_qbo_bill_id.return_value = _make_bill_mapping(bill_id=700)
    connector.bill_service.read_by_id.return_value = None
    connector.bill_service.read_by_bill_number_and_vendor_public_id.return_value = other

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_bill(connector, _make_qbo_bill())

    connector.reconciliation_repo.create.assert_called_once()
    connector.bill_service.update_by_public_id.assert_not_called()
    connector.bill_service.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()


# --- (d) happy path: mapping + live Bill updates normally, no fingerprint lookup ---

def test_bill_happy_path_existing_mapping_updates_normally():
    connector = _build_bill_connector()
    bill = _make_bill(bill_id=700)

    connector.mapping_repo.read_by_qbo_bill_id.return_value = _make_bill_mapping(bill_id=700)
    connector.bill_service.read_by_id.return_value = bill
    connector.bill_service.update_by_public_id.return_value = bill

    result = _drive_bill(connector, _make_qbo_bill())

    assert result is bill
    connector.bill_service.update_by_public_id.assert_called_once()
    # Live read short-circuits — no fingerprint re-resolution, no issue, no delete.
    connector.bill_service.read_by_bill_number_and_vendor_public_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.bill_service.create.assert_not_called()


# --- (e) heal path preserves a human-edited bill_number (U-027 shared helper) ---

def test_bill_transient_heal_preserves_human_edited_number():
    connector = _build_bill_connector()
    # Same id as the mapping target, but its stored number was human-corrected.
    replacement = _make_bill(bill_id=700, bill_number="INV-9987")

    connector.mapping_repo.read_by_qbo_bill_id.return_value = _make_bill_mapping(bill_id=700)
    connector.bill_service.read_by_id.return_value = None
    connector.bill_service.read_by_bill_number_and_vendor_public_id.return_value = replacement
    connector.bill_service.update_by_public_id.return_value = replacement

    _drive_bill(connector, _make_qbo_bill(qbo_id="88", doc_number="B-5001"))

    passed = connector.bill_service.update_by_public_id.call_args.kwargs["bill_number"]
    assert passed == "INV-9987"  # manual edit survives the heal, not clobbered to "B-5001"


# --- (f) reconciliation-issue recording is failure-isolated ---

def test_bill_reconciliation_insert_failure_does_not_suppress_raise():
    connector = _build_bill_connector()

    connector.mapping_repo.read_by_qbo_bill_id.return_value = _make_bill_mapping(bill_id=700)
    connector.bill_service.read_by_id.return_value = None
    connector.bill_service.read_by_bill_number_and_vendor_public_id.return_value = None
    connector.reconciliation_repo.create.side_effect = RuntimeError("recon insert down")

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_bill(connector, _make_qbo_bill())

    connector.reconciliation_repo.create.assert_called_once()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.bill_service.create.assert_not_called()


# ===========================================================================
# VendorCredit — vendorcredit/connector/bill_credit/business/service.py
# ===========================================================================

from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)

VC_SERVICE = "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service"


def _make_qbo_vc(*, qbo_id="99", doc_number="VC-300", realm_id="realm-1"):
    return SimpleNamespace(
        id=301,
        qbo_id=qbo_id,
        realm_id=realm_id,
        vendor_ref_value="qbo-vendor-1",
        doc_number=doc_number,
        txn_date="2026-07-01",
        private_note="note",
        total_amt=50,
    )


def _make_bill_credit(*, credit_number="VC-300", bc_id=400, public_id="bc-pub-400"):
    return SimpleNamespace(
        id=bc_id,
        public_id=public_id,
        credit_number=credit_number,
        row_version="rowver==",
    )


def _make_vc_mapping(*, mapping_id=1, bill_credit_id=400):
    return SimpleNamespace(id=mapping_id, bill_credit_id=bill_credit_id)


def _build_vc_connector():
    connector = VendorCreditBillCreditConnector(
        mapping_repo=Mock(),
        bill_credit_service=Mock(),
        bill_credit_line_item_service=Mock(),
        vendor_service=Mock(),
        reconciliation_repo=Mock(),
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector


def _drive_vc(connector, qbo_vc):
    with patch(f"{VC_SERVICE}.guard_lines_present"):
        return connector.sync_from_qbo_vendor_credit(qbo_vc, [])


# --- (a) transient empty-read: fingerprint re-binds SAME id -> heal, NO duplicate ---

def test_vc_transient_empty_read_heals_in_place_no_duplicate():
    connector = _build_vc_connector()
    replacement = _make_bill_credit(bc_id=400)  # same id the mapping targets

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = None  # transient empty read
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.return_value = replacement
    connector.bill_credit_service.update_by_public_id.return_value = replacement

    result = _drive_vc(connector, _make_qbo_vc())

    assert result is replacement
    connector.bill_credit_service.update_by_public_id.assert_called_once()
    connector.bill_credit_service.create.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


# --- (b) genuinely missing: fingerprint miss -> record issue + RAISE, no mutate ---

def test_vc_genuinely_missing_records_issue_and_raises():
    connector = _build_vc_connector()

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = None
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.return_value = None

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_vc(connector, _make_qbo_vc())

    connector.reconciliation_repo.create.assert_called_once()
    kw = connector.reconciliation_repo.create.call_args.kwargs
    assert kw["drift_type"] == "orphaned_vendorcredit_billcredit_mapping"
    assert kw["entity_type"] == "BillCredit"
    assert kw["severity"] == "critical"
    connector.bill_credit_service.create.assert_not_called()
    connector.bill_credit_service.update_by_public_id.assert_not_called()


# --- (c) fingerprint matches a DIFFERENT BillCredit id -> never rebind, record + RAISE ---

def test_vc_fingerprint_different_id_records_issue_and_raises_no_rebind():
    connector = _build_vc_connector()
    other = _make_bill_credit(bc_id=999, public_id="bc-pub-999")  # a DIFFERENT row

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = None
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.return_value = other

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_vc(connector, _make_qbo_vc())

    connector.reconciliation_repo.create.assert_called_once()
    connector.bill_credit_service.update_by_public_id.assert_not_called()
    connector.bill_credit_service.create.assert_not_called()


# --- (d) happy path: mapping + live BillCredit updates normally, no fingerprint lookup ---

def test_vc_happy_path_existing_mapping_updates_normally():
    connector = _build_vc_connector()
    bill_credit = _make_bill_credit(bc_id=400)

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = bill_credit
    connector.bill_credit_service.update_by_public_id.return_value = bill_credit

    result = _drive_vc(connector, _make_qbo_vc())

    assert result is bill_credit
    connector.bill_credit_service.update_by_public_id.assert_called_once()
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()
    connector.bill_credit_service.create.assert_not_called()


# --- (e) heal path preserves a human-edited credit_number (U-027 shared helper) ---

def test_vc_transient_heal_preserves_human_edited_number():
    connector = _build_vc_connector()
    replacement = _make_bill_credit(bc_id=400, credit_number="CM-42")  # human-corrected

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = None
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.return_value = replacement
    connector.bill_credit_service.update_by_public_id.return_value = replacement

    _drive_vc(connector, _make_qbo_vc(qbo_id="99", doc_number="VC-300"))

    passed = connector.bill_credit_service.update_by_public_id.call_args.kwargs["credit_number"]
    assert passed == "CM-42"  # manual edit survives the heal, not clobbered to "VC-300"


# --- (f) reconciliation-issue recording is failure-isolated ---

def test_vc_reconciliation_insert_failure_does_not_suppress_raise():
    connector = _build_vc_connector()

    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = _make_vc_mapping(bill_credit_id=400)
    connector.bill_credit_service.read_by_id.return_value = None
    connector.bill_credit_service.read_by_credit_number_and_vendor_public_id.return_value = None
    connector.reconciliation_repo.create.side_effect = RuntimeError("recon insert down")

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive_vc(connector, _make_qbo_vc())

    connector.reconciliation_repo.create.assert_called_once()
    connector.bill_credit_service.create.assert_not_called()
