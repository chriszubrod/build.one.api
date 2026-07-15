"""Per-connector preserve/upgrade tests for U-027 — the "rule of three".

The purchase→Expense connector already preserves a human-corrected
reference_number on re-pull (U-024, tested in
test_qbo_purchase_reference_number_preserve.py). U-027 applies the SAME shared
base decision (base.field_ownership.preserve_human_edited_ref) on the UPDATE
path of the two siblings that are safe to change:

  * Bill.bill_number       (integrations/intuit/qbo/bill/connector/bill)
  * BillCredit.credit_number (integrations/intuit/qbo/vendorcredit/connector/bill_credit)

Invoice.invoice_number was DEFERRED (its lost-mapping adopt path keys on the
QBO-derived number; preserving a divergent local number reintroduces the
phantom-duplicate bug) — see the note at the bottom of this file + TODO.md.

Each connector is driven with fully mocked services/repos so no DB or QBO I/O
runs; line syncing is stubbed (these tests only exercise the number decision).
For every sibling we assert the four documented cases:
  (a) a manual edit is PRESERVED,
  (b) the QBO-<id> placeholder UPGRADES to the real doc_number,
  (c) an empty/None stored value is SET from the QBO-derived value,
  (d) the CREATE path is UNCHANGED (always the QBO-derived value).
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


def _make_qbo_bill(*, qbo_id="88", doc_number="B-5001"):
    return SimpleNamespace(
        id=801,
        qbo_id=qbo_id,
        vendor_ref_value="qbo-vendor-1",
        doc_number=doc_number,
        txn_date="2026-07-01",
        due_date="2026-07-01",
        private_note="note",
        total_amt=100,
    )


def _make_bill(*, bill_number, bill_id=700, public_id="bill-pub-700"):
    return SimpleNamespace(
        id=bill_id,
        public_id=public_id,
        bill_number=bill_number,
        row_version="rowver==",
    )


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
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()  # isolate the number decision
    return connector


def _run_bill_update(connector, qbo_bill, stored_bill):
    connector.mapping_repo.read_by_qbo_bill_id.return_value = SimpleNamespace(
        id=1, bill_id=stored_bill.id
    )
    connector.bill_service.read_by_id.return_value = stored_bill
    connector.bill_service.update_by_public_id.return_value = stored_bill

    with patch(f"{BILL_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_bill(qbo_bill, [])

    connector.bill_service.update_by_public_id.assert_called_once()
    connector.bill_service.create.assert_not_called()
    return connector.bill_service.update_by_public_id.call_args.kwargs["bill_number"]


def test_bill_update_preserves_manual_number():
    connector = _build_bill_connector()
    passed = _run_bill_update(
        connector, _make_qbo_bill(qbo_id="88", doc_number="B-5001"),
        _make_bill(bill_number="INV-9987"),
    )
    assert passed == "INV-9987"


def test_bill_update_upgrades_placeholder():
    connector = _build_bill_connector()
    passed = _run_bill_update(
        connector, _make_qbo_bill(qbo_id="88", doc_number="B-5001"),
        _make_bill(bill_number="QBO-88"),
    )
    assert passed == "B-5001"


@pytest.mark.parametrize("stored", [None, ""])
def test_bill_update_sets_from_doc_number_when_empty(stored):
    connector = _build_bill_connector()
    passed = _run_bill_update(
        connector, _make_qbo_bill(qbo_id="88", doc_number="B-5001"),
        _make_bill(bill_number=stored),
    )
    assert passed == "B-5001"


def test_bill_update_preserves_manual_when_doc_number_none():
    connector = _build_bill_connector()
    passed = _run_bill_update(
        connector, _make_qbo_bill(qbo_id="88", doc_number=None),  # incoming => "QBO-88"
        _make_bill(bill_number="INV-9987"),
    )
    assert passed == "INV-9987"


def test_bill_create_sets_number_from_doc_number():
    connector = _build_bill_connector()
    qbo_bill = _make_qbo_bill(qbo_id="88", doc_number="B-5001")
    connector.mapping_repo.read_by_qbo_bill_id.return_value = None  # no mapping => CREATE
    connector.mapping_repo.read_by_bill_id.return_value = None
    connector.bill_service.create.return_value = _make_bill(
        bill_number="B-5001", bill_id=701, public_id="bill-pub-701"
    )
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    with patch(f"{BILL_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_bill(qbo_bill, [])

    connector.bill_service.create.assert_called_once()
    connector.bill_service.update_by_public_id.assert_not_called()
    assert connector.bill_service.create.call_args.kwargs["bill_number"] == "B-5001"


# ===========================================================================
# VendorCredit — vendorcredit/connector/bill_credit/business/service.py
# ===========================================================================

from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)

VC_SERVICE = "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service"


def _make_qbo_vc(*, qbo_id="99", doc_number="VC-300"):
    return SimpleNamespace(
        id=301,
        qbo_id=qbo_id,
        vendor_ref_value="qbo-vendor-1",
        doc_number=doc_number,
        txn_date="2026-07-01",
        private_note="note",
        total_amt=50,
    )


def _make_bill_credit(*, credit_number, bc_id=400, public_id="bc-pub-400"):
    return SimpleNamespace(
        id=bc_id,
        public_id=public_id,
        credit_number=credit_number,
        row_version="rowver==",
    )


def _build_vc_connector():
    connector = VendorCreditBillCreditConnector()
    connector.mapping_repo = Mock()
    connector.bill_credit_service = Mock()
    connector.bill_credit_line_item_service = Mock()
    connector.vendor_service = Mock()
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()  # isolate the number decision
    return connector


def _run_vc_update(connector, qbo_vc, stored_bc):
    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = SimpleNamespace(
        id=1, bill_credit_id=stored_bc.id
    )
    connector.bill_credit_service.read_by_id.return_value = stored_bc
    connector.bill_credit_service.update_by_public_id.return_value = stored_bc

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    connector.bill_credit_service.update_by_public_id.assert_called_once()
    connector.bill_credit_service.create.assert_not_called()
    return connector.bill_credit_service.update_by_public_id.call_args.kwargs["credit_number"]


def test_vc_update_preserves_manual_number():
    connector = _build_vc_connector()
    passed = _run_vc_update(
        connector, _make_qbo_vc(qbo_id="99", doc_number="VC-300"),
        _make_bill_credit(credit_number="CM-42"),
    )
    assert passed == "CM-42"


def test_vc_update_upgrades_placeholder():
    connector = _build_vc_connector()
    passed = _run_vc_update(
        connector, _make_qbo_vc(qbo_id="99", doc_number="VC-300"),
        _make_bill_credit(credit_number="QBO-99"),
    )
    assert passed == "VC-300"


@pytest.mark.parametrize("stored", [None, ""])
def test_vc_update_sets_from_doc_number_when_empty(stored):
    connector = _build_vc_connector()
    passed = _run_vc_update(
        connector, _make_qbo_vc(qbo_id="99", doc_number="VC-300"),
        _make_bill_credit(credit_number=stored),
    )
    assert passed == "VC-300"


def test_vc_update_preserves_manual_when_doc_number_none():
    connector = _build_vc_connector()
    passed = _run_vc_update(
        connector, _make_qbo_vc(qbo_id="99", doc_number=None),  # incoming => "QBO-99"
        _make_bill_credit(credit_number="CM-42"),
    )
    assert passed == "CM-42"


def test_vc_create_sets_number_from_doc_number():
    connector = _build_vc_connector()
    qbo_vc = _make_qbo_vc(qbo_id="99", doc_number="VC-300")
    connector.mapping_repo.read_by_qbo_vendor_credit_id.return_value = None  # CREATE
    connector.bill_credit_service.create.return_value = _make_bill_credit(
        credit_number="VC-300", bc_id=401, public_id="bc-pub-401"
    )
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    with patch(f"{VC_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, [])

    connector.bill_credit_service.create.assert_called_once()
    connector.bill_credit_service.update_by_public_id.assert_not_called()
    assert connector.bill_credit_service.create.call_args.kwargs["credit_number"] == "VC-300"


# ===========================================================================
# Invoice — DEFERRED (U-027).
# The invoice_number preserve was intentionally NOT shipped: preserving a
# human-corrected number lets the local value diverge from the QBO-derived
# number, which the lost-mapping gap-detect/adopt path
# (read_by_invoice_number_and_project_id) keys on to re-adopt an existing
# invoice. Divergence there reintroduces the documented "46 phantom -N
# invoices" bug. Correctness review (U-027 Pass 1) CONFIRMED this conflict.
# Tracked as an open follow-up in TODO.md (adopt lookup must try the preserved
# number first, or a broader redesign). No Invoice connector test here.
# ===========================================================================
