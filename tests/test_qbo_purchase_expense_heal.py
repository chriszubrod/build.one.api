"""Pure-logic tests for U-029 — Purchase->Expense "heal-don't-delete" mapping fix.

Applies the U-022 CustomerProject pattern to PurchaseExpenseConnector: when the
existing mapping's bound Expense reads empty, a *transient* empty-read must NEVER
delete the mapping and mint a DUPLICATE Expense (the U-024-flagged hazard).

Design (Expense has no unique NAME key, and there is no mapping-repoint sproc):
  - Re-resolve by the (reference_number, vendor) fingerprint BEFORE mutating.
  - Heal in place ONLY when the fingerprint re-binds to the SAME Expense the
    mapping already targets (a confirmed transient empty-read).
  - Otherwise record a [qbo].[ReconciliationIssue] and RAISE ValueError,
    mutating nothing (mapping preserved, no Expense created/deleted).

Mocks stand in for expense_service + repos so no DB/QBO I/O runs.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)

SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
# The connector builds its line connector via a lazy import from this module.
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)


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
    )


def _make_expense(*, reference_number="5001", expense_id=500, public_id="exp-pub-500"):
    return SimpleNamespace(
        id=expense_id,
        public_id=public_id,
        reference_number=reference_number,
        row_version="rowver==",
    )


def _make_mapping(*, mapping_id=1, expense_id=500):
    return SimpleNamespace(id=mapping_id, expense_id=expense_id)


def _build_connector():
    """Build a PurchaseExpenseConnector with fully mocked deps (no DB/QBO)."""
    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(
            mapping_repo=Mock(),
            expense_service=Mock(),
            vendor_service=Mock(),
            vendor_vendor_repo=Mock(),
            qbo_vendor_repo=Mock(),
            qbo_purchase_repo=Mock(),
            qbo_purchase_line_repo=Mock(),
            reconciliation_repo=Mock(),
        )
    # Short-circuit vendor resolution (exercised elsewhere) — these tests only care
    # about the empty-read heal/skip decision.
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._line_connector = Mock()  # empty line list => no-op anyway
    return connector


def _drive(connector, qbo_purchase):
    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        return connector.sync_from_qbo_purchase(qbo_purchase, [])


# --- (a) transient empty-read: fingerprint re-binds SAME id -> heal in place, NO duplicate ---

def test_transient_empty_read_heals_in_place_no_duplicate():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase()
    mapping = _make_mapping(expense_id=500)
    replacement = _make_expense(expense_id=500)  # same id the mapping targets

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = mapping
    connector.expense_service.read_by_id.return_value = None  # transient empty read
    connector.expense_service.read_by_reference_number_and_vendor_public_id.return_value = replacement
    connector.expense_service.update_by_public_id.return_value = replacement

    result = _drive(connector, qbo_purchase)

    assert result is replacement
    # Healed via UPDATE, not recreated — and the mapping was NOT deleted.
    connector.expense_service.update_by_public_id.assert_called_once()
    connector.expense_service.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


# --- (b) genuinely missing: fingerprint miss -> record issue + RAISE, no mutate ---

def test_genuinely_missing_records_issue_and_raises():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase()
    mapping = _make_mapping(expense_id=500)

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = mapping
    connector.expense_service.read_by_id.return_value = None
    connector.expense_service.read_by_reference_number_and_vendor_public_id.return_value = None

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive(connector, qbo_purchase)

    connector.reconciliation_repo.create.assert_called_once()
    call_kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert call_kwargs["drift_type"] == "orphaned_purchase_expense_mapping"
    assert call_kwargs["entity_type"] == "Expense"
    assert call_kwargs["severity"] == "critical"
    # No destructive action.
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.expense_service.create.assert_not_called()
    connector.expense_service.update_by_public_id.assert_not_called()


# --- (c) fingerprint matches a DIFFERENT Expense id -> never rebind, record + RAISE ---

def test_fingerprint_different_id_records_issue_and_raises_no_rebind():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase()
    mapping = _make_mapping(expense_id=500)
    other = _make_expense(expense_id=999, public_id="exp-pub-999")  # a DIFFERENT row

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = mapping
    connector.expense_service.read_by_id.return_value = None
    connector.expense_service.read_by_reference_number_and_vendor_public_id.return_value = other

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive(connector, qbo_purchase)

    connector.reconciliation_repo.create.assert_called_once()
    # Never bound to the different row, never created, never deleted.
    connector.expense_service.update_by_public_id.assert_not_called()
    connector.expense_service.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()


# --- (d) happy path: mapping + live Expense updates normally, no fingerprint lookup ---

def test_happy_path_existing_mapping_updates_normally():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase()
    mapping = _make_mapping(expense_id=500)
    expense = _make_expense(expense_id=500)

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = mapping
    connector.expense_service.read_by_id.return_value = expense
    connector.expense_service.update_by_public_id.return_value = expense

    result = _drive(connector, qbo_purchase)

    assert result is expense
    connector.expense_service.update_by_public_id.assert_called_once()
    # Live read short-circuits — no fingerprint re-resolution, no issue, no delete.
    connector.expense_service.read_by_reference_number_and_vendor_public_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.expense_service.create.assert_not_called()


# --- (e) heal path preserves a human-edited reference_number (U-024 shared helper) ---

def test_transient_heal_preserves_human_edited_reference_number():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase(qbo_id="77", doc_number="5001")
    # Same id as the mapping target, but its stored ref was human-corrected.
    replacement = _make_expense(expense_id=500, reference_number="INV-9987")

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = _make_mapping(expense_id=500)
    connector.expense_service.read_by_id.return_value = None
    connector.expense_service.read_by_reference_number_and_vendor_public_id.return_value = replacement
    connector.expense_service.update_by_public_id.return_value = replacement

    _drive(connector, qbo_purchase)

    passed = connector.expense_service.update_by_public_id.call_args.kwargs["reference_number"]
    assert passed == "INV-9987"  # manual edit survives the heal, not clobbered to "5001"


# --- (f) reconciliation-issue recording is failure-isolated: a failed insert must NOT
#         suppress the ValueError, and must NOT trigger any destructive fallback ---

def test_reconciliation_insert_failure_does_not_suppress_raise():
    connector = _build_connector()
    qbo_purchase = _make_qbo_purchase()

    connector.mapping_repo.read_by_qbo_purchase_id.return_value = _make_mapping(expense_id=500)
    connector.expense_service.read_by_id.return_value = None
    connector.expense_service.read_by_reference_number_and_vendor_public_id.return_value = None
    # The reconciliation write itself blows up — the sync must still raise, not swallow.
    connector.reconciliation_repo.create.side_effect = RuntimeError("recon insert down")

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        _drive(connector, qbo_purchase)

    connector.reconciliation_repo.create.assert_called_once()
    connector.mapping_repo.delete_by_id.assert_not_called()
    connector.expense_service.create.assert_not_called()
