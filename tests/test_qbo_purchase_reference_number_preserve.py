"""Pure-logic tests for U-024 / KI-42 — QBO purchase->Expense re-sync must not
clobber a manually-corrected reference_number (vendor invoice number).

On the UPDATE (dbo-only fast-path HIT) branch of PurchaseExpenseConnector.
sync_from_qbo_purchase, the stored expense.reference_number is PRESERVED
unless it is empty/None OR the QBO-<qbo_id> placeholder (which still upgrades
to a real doc_number). The CREATE (MISS) path is unchanged (always set from
the QBO-derived value). Mocks stand in for the expense_service + repos so no
DB/QBO I/O runs.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
# The connector builds its line connector via a lazy import from this module.
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)

# U-354: the MISS/create branch runs under run_identity_fastpath_dbo_only's own
# create lock — grant it for every test in this pure-logic module.
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_purchase(*, qbo_id="77", doc_number="5001", realm_id="realm-1"):
    return SimpleNamespace(
        id=901,
        qbo_id=qbo_id,
        realm_id=realm_id,
        entity_ref_value="qbo-vendor-1",
        doc_number=doc_number,
        txn_date="2026-07-01",
        private_note="card spend",
        total_amt=100,
        credit=False,
        sync_token="3",
    )


def _make_expense(*, reference_number, expense_id=500, public_id="exp-pub-500"):
    return SimpleNamespace(
        id=expense_id,
        public_id=public_id,
        reference_number=reference_number,
        row_version="rowver==",
    )


def _build_connector():
    """Build a PurchaseExpenseConnector with fully mocked deps (no DB/QBO)."""
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )

    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(
            expense_service=Mock(),
            vendor_service=Mock(),
            vendor_vendor_repo=Mock(),
            qbo_vendor_repo=Mock(),
        )
    connector.expense_service.repo = Mock()
    # Short-circuit vendor resolution (exercised elsewhere) — this suite only
    # cares about the reference_number preserve/upgrade decision.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._line_connector = Mock()  # empty line list => no-op anyway
    return connector


def _run_update(connector, qbo_purchase, stored_expense):
    """Drive the dbo-only fast path's HIT (UPDATE) branch and return the
    reference_number passed to update_by_public_id."""
    connector.expense_service.read_by_qbo_identity.return_value = stored_expense
    connector.expense_service.update_by_public_id.return_value = stored_expense

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        connector.sync_from_qbo_purchase(qbo_purchase, [])

    connector.expense_service.update_by_public_id.assert_called_once()
    connector.expense_service.create.assert_not_called()
    return connector.expense_service.update_by_public_id.call_args.kwargs["reference_number"]


# --- (a) manual value is PRESERVED (differs from doc_number, not the placeholder) ---

def test_update_preserves_manual_reference_number():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number="5001")
    stored = _make_expense(reference_number="INV-9987")  # human-corrected

    passed = _run_update(connector, qbo_purchase, stored)

    assert passed == "INV-9987"  # NOT "5001" (doc_number) — manual edit survives the tick


# --- (b) QBO-<id> placeholder is UPGRADED to the real doc_number ---

def test_update_upgrades_placeholder_to_doc_number():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number="5001")
    stored = _make_expense(reference_number="QBO-77")  # pulled before QBO had a doc_number

    passed = _run_update(connector, qbo_purchase, stored)

    assert passed == "5001"  # placeholder gives way to the real doc_number


# --- (c) empty / None stored value is SET from the QBO-derived value ---

@pytest.mark.parametrize("stored_ref", [None, ""])
def test_update_sets_from_doc_number_when_stored_empty(stored_ref):
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number="5001")
    stored = _make_expense(reference_number=stored_ref)

    passed = _run_update(connector, qbo_purchase, stored)

    assert passed == "5001"


# --- (d) CREATE (MISS) path is unchanged: set from the QBO-derived value ---

def test_create_sets_reference_number_from_doc_number():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number="5001")

    connector.expense_service.read_by_qbo_identity.return_value = None  # genuine miss
    created = _make_expense(reference_number="5001", expense_id=600, public_id="exp-pub-600")
    connector.expense_service.create.return_value = created
    connector.expense_service.read_by_id.return_value = created

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        connector.sync_from_qbo_purchase(qbo_purchase, [])

    connector.expense_service.create.assert_called_once()
    connector.expense_service.update_by_public_id.assert_not_called()
    assert connector.expense_service.create.call_args.kwargs["reference_number"] == "5001"


# --- (e) doc_number None + manual stored value: still PRESERVED (no QBO-<id> clobber) ---

def test_update_preserves_manual_value_when_doc_number_none():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number=None)  # QBO-derived => "QBO-77"
    stored = _make_expense(reference_number="INV-9987")  # human-corrected

    passed = _run_update(connector, qbo_purchase, stored)

    assert passed == "INV-9987"  # NOT "QBO-77" — the placeholder never overwrites a manual value
