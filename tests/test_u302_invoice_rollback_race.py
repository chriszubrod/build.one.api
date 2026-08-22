"""Pure-logic tests for U-302 — InvoiceInvoiceConnector's create-path rollback tail
gets the same dbo-native uniqueness recheck U-298 gave PurchaseExpenseConnector.

Nothing serializes concurrent sync_from_qbo_invoice calls for the same QboInvoice
(no sp_getapplock at this level). Before this fix, ANY create_mapping failure —
including the benign case where a concurrent racer syncing the exact same
QboInvoice won the mapping insert first — unconditionally deleted the just-created
Invoice. That silently destroys the racer's now-valid, already-mapped (and possibly
already line-synced) financial record with no FK to stop it.

Mocks stand in for invoice_service + mapping_repo; no DB/QBO I/O.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
    InvoiceInvoiceConnector,
)
from shared.database import DatabaseConstraintError
from shared.db_constraints import UNIQUE_VIOLATION

# Matches the sibling QBO connector test files' own convention (e.g.
# test_qbo_zombie_rollback.py) — makes the bare `from conftest import ...` below
# resolve when this file is collected standalone, not only as part of the full
# suite (where an earlier-alphabetical file's own insert happens to cover it).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import stub_qbo_identity_fastpath_miss


def _make_qbo_invoice(**overrides):
    defaults = dict(
        id=901,
        qbo_id="INV-77",
        realm_id="realm-1",
        customer_ref_value="qbo-customer-1",
        doc_number="5001",
        txn_date="2026-08-01",
        due_date="2026-08-15",
        private_note="draw",
        total_amt=100,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ONE_LINE = [SimpleNamespace(id=1)]


def _build_connector():
    mapping_repo = Mock()
    invoice_service = Mock()
    invoice_service.repo = Mock()
    project_service = Mock()
    reconciliation_repo = Mock()
    connector = InvoiceInvoiceConnector(
        mapping_repo=mapping_repo,
        invoice_service=invoice_service,
        project_service=project_service,
        reconciliation_repo=reconciliation_repo,
    )
    connector._get_project_public_id = Mock(return_value="project-pub-1")
    connector._sync_line_items = Mock()
    # No mapping-lost adopt candidate — force the plain CREATE path.
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
    return connector, mapping_repo, invoice_service, project_service, reconciliation_repo


def _wire_create_path_misses(mapping_repo, invoice_service, project_service):
    """Common wiring so the connector reaches the CREATE path: fast path misses,
    no existing mapping, no gap-detect/adopt candidate."""
    stub_qbo_identity_fastpath_miss(invoice_service)  # top fast path: miss
    mapping_repo.read_by_qbo_invoice_id.return_value = None  # legacy "check for mapping": miss
    project_service.read_by_public_id.return_value = SimpleNamespace(id=42)
    invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None  # gap-detect: miss


def test_create_mapping_race_returns_racers_result_without_rollback():
    """Confirmed-shape P1 (mirrors U-298's Gate-1 hunt finding): a concurrent
    racer's identity-fastpath recheck can win the mapping insert in the window
    between create_mapping's own set_qbo_identity stamp and its
    mapping_repo.create call. The resulting unique-constraint collision must NOT
    roll back (delete) the now validly mapped Invoice — it must recognize the
    racer won and return its result instead of destroying a legitimately-
    completed financial record."""
    connector, mapping_repo, invoice_service, project_service, _ = _build_connector()
    qbo_invoice = _make_qbo_invoice()
    _wire_create_path_misses(mapping_repo, invoice_service, project_service)

    created = SimpleNamespace(id=77, public_id="pub-77")
    invoice_service.create.return_value = created

    # create_mapping()'s own 1:1 guard (1st call) passes -> nothing exists yet.
    # The except-block's re-check (2nd call) finds the racer's mapping.
    racer_mapping = SimpleNamespace(id=5, invoice_id=77, qbo_invoice_id=qbo_invoice.id)
    mapping_repo.read_by_invoice_id.side_effect = [None, racer_mapping]
    # The raw insert collides with the racer's own insert of the identical pair.
    # InvoiceInvoiceRepository.create() wraps EVERY failure via map_database_error
    # (shared/database.py) — a genuine unique-key collision surfaces as
    # DatabaseConstraintError, never a bare ValueError. The connector's except
    # clause is `except (ValueError, DatabaseConstraintError)`, so the mock must
    # raise the type production code actually produces, or this test would still
    # pass even if DatabaseConstraintError were dropped from that tuple.
    mapping_repo.create.side_effect = DatabaseConstraintError(UNIQUE_VIOLATION, "UNIQUE constraint violation")

    current_state = SimpleNamespace(id=77, public_id="pub-77", invoice_number="5001")
    invoice_service.read_by_id.return_value = current_state

    result = connector.sync_from_qbo_invoice(qbo_invoice, _ONE_LINE)

    assert result is current_state
    invoice_service.delete_by_public_id.assert_not_called()  # NOT rolled back


def test_create_mapping_conflict_records_issue_then_still_rolls_back():
    """The rollback-guard's re-check must also handle CONFLICT — a mapping now
    exists but disagrees (points this Invoice at a DIFFERENT QboInvoice). Unlike
    the benign CONSISTENT race, this is NOT a resolved race: record the same
    reconciliation issue every other conflict path in this file records, then
    still roll back this genuine orphan."""
    connector, mapping_repo, invoice_service, project_service, reconciliation_repo = _build_connector()
    qbo_invoice = _make_qbo_invoice()
    _wire_create_path_misses(mapping_repo, invoice_service, project_service)

    created = SimpleNamespace(id=77, public_id="pub-77")
    invoice_service.create.return_value = created

    # create_mapping()'s own 1:1 guard (1st call) passes. The except-block's
    # re-check (2nd call) finds a mapping for THIS invoice pointing at a
    # DIFFERENT qbo_invoice_id -> CONFLICT, not CONSISTENT.
    conflicting_mapping = SimpleNamespace(id=6, invoice_id=77, qbo_invoice_id=555)
    mapping_repo.read_by_invoice_id.side_effect = [None, conflicting_mapping]
    mapping_repo.create.side_effect = DatabaseConstraintError(UNIQUE_VIOLATION, "UNIQUE constraint violation")

    # Unlike PurchaseExpenseConnector, InvoiceInvoiceConnector re-raises the
    # ORIGINAL exception bare (not wrapped) after rolling back — see the
    # trailing `raise` in the except block.
    with pytest.raises(DatabaseConstraintError, match="duplicates an existing record"):
        connector.sync_from_qbo_invoice(qbo_invoice, _ONE_LINE)

    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "invoice_identity_conflict"
    invoice_service.delete_by_public_id.assert_called_once_with("pub-77")  # still rolled back


def test_create_mapping_failure_missing_state_still_rolls_back():
    """No race at all (MISSING on both sides of the recheck): the original
    rollback behavior is unchanged — a genuine mapping-create failure with no
    racer anywhere still deletes the orphan header and re-raises."""
    connector, mapping_repo, invoice_service, project_service, reconciliation_repo = _build_connector()
    qbo_invoice = _make_qbo_invoice()
    _wire_create_path_misses(mapping_repo, invoice_service, project_service)

    created = SimpleNamespace(id=77, public_id="pub-77")
    invoice_service.create.return_value = created

    # Both the 1:1 guard call and the except-block's recheck miss on every side —
    # a genuine constraint violation (e.g. a stale leftover mapping row) with no
    # live racer to resolve to. Same realistic exception type as the two tests
    # above (see their comment) — InvoiceInvoiceRepository.create() never raises
    # a bare ValueError in production.
    mapping_repo.read_by_invoice_id.return_value = None
    mapping_repo.create.side_effect = DatabaseConstraintError(UNIQUE_VIOLATION, "genuine constraint violation")

    with pytest.raises(DatabaseConstraintError, match="duplicates an existing record"):
        connector.sync_from_qbo_invoice(qbo_invoice, _ONE_LINE)

    reconciliation_repo.create.assert_not_called()  # no conflict issue for a plain MISSING
    invoice_service.delete_by_public_id.assert_called_once_with("pub-77")  # rolled back, same as before the fix
