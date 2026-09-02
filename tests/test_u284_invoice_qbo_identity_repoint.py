"""Pure-logic tests for U-284 (Phase-4): repoint the `invoice` family's header
identity resolution off qbo.Invoice / qbo.InvoiceInvoice onto dbo.Invoice's
native QboId/RealmId (U-238a), via the shared base/identity_fastpath.py helper
(U-287) — no per-family copy of the state machine. Mirrors
tests/test_u283_bill_qbo_identity_repoint.py's shape.

Scope, per Gate-1 (approved 2026-08-21): HEADER identity resolution +
ComputeInvoiceDrawMatrix / cost_coded_lines_for_invoice draw-matrix consumers
ONLY. The invoice LINE connector (InvoiceLineItemConnector,
qbo.InvoiceLineItemInvoiceLine mapping) is explicitly OUT of scope — deferred
to a new cross-family unit (U-293) since line-item identity has never been
repointed for any family (bill/invoice/expense/bill_credit), only write-side
dual-write (U-238b) exists. Nothing here touches that connector.

Covers:
  1. InvoiceRepository.read_by_qbo_identity (sproc call shape) + InvoiceService's
     RBAC-scoped passthrough (assert_can_access_project, matching read_by_id).
  2. InvoiceInvoiceConnector.sync_from_qbo_invoice's fast path — rewritten for
     the dbo-only shape (U-356 retired qbo.InvoiceInvoice; there is no
     CONSISTENT/MISSING/CONFLICT mapping-table state machine left): a direct
     dbo.Invoice identity HIT updates + re-stamps SyncToken + syncs lines and
     never creates; a MISS resolves/creates a candidate and stamps it (covered
     in depth by tests/test_u356_invoice_mapping_retire.py).
  3. The shared `_write_qbo_fields` closure's ROWVERSION-race guard
     (update_by_public_id returning None -> RuntimeError via the shared
     helper's raise_concurrent_write_race, never a bare crash or a swallowed
     None).
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
    InvoiceInvoiceConnector,
)

# Matches the sibling QBO connector test files' own convention (e.g.
# test_u302_invoice_rollback_race.py) — makes the bare `from conftest import ...`
# below resolve when this file is collected standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import mock_qbo_app_lock_granted as _granted_lock  # U-304 lock grant


def _make_qbo_invoice(**overrides):
    defaults = dict(
        id=8,
        qbo_id="INV-QBO",
        realm_id="realm-z",
        customer_ref_value="cust1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        due_date="2026-08-31",
        private_note="memo",
        total_amt=100,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo/service-level sproc call shape ---


def test_invoice_repo_read_by_qbo_identity_calls_sproc():
    from entities.invoice.persistence.repo import InvoiceRepository

    repo = InvoiceRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.invoice.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.invoice.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("INV-QBO", "realm-z")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadInvoiceByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {
        "QboId": "INV-QBO",
        "RealmId": "realm-z",
    }


def test_invoice_service_read_by_qbo_identity_enforces_project_rbac():
    """Mirrors BillService's equivalent test — must NOT bypass RBAC scoping.
    Invoice has no per-row UserCanAccessInvoice UDF (unlike Bill); RBAC is
    enforced at the service layer via assert_can_access_project, exactly like
    read_by_id/read_by_public_id."""
    from entities.invoice.business.service import InvoiceService

    repo = Mock()
    sentinel = SimpleNamespace(id=1, project_id=42)
    repo.read_by_qbo_identity.return_value = sentinel
    service = InvoiceService(repo=repo)

    with patch("entities.invoice.business.service.assert_can_access_project") as mock_assert:
        result = service.read_by_qbo_identity("INV-1", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("INV-1", "realm-1")
    mock_assert.assert_called_once_with(42)
    assert result is sentinel


def test_invoice_service_read_by_qbo_identity_none_short_circuits_rbac():
    from entities.invoice.business.service import InvoiceService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = None
    service = InvoiceService(repo=repo)

    with patch("entities.invoice.business.service.assert_can_access_project") as mock_assert:
        result = service.read_by_qbo_identity("INV-1", "realm-1")

    assert result is None
    mock_assert.assert_not_called()


# --- Section 2: InvoiceInvoiceConnector fast path (dbo-only, U-356) ---


def _build_invoice_connector():
    invoice_service = Mock()
    invoice_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = InvoiceInvoiceConnector(
        invoice_service=invoice_service,
        reconciliation_repo=reconciliation_repo,
    )
    # Out of scope for these tests — project resolution and line-item sync are
    # exercised elsewhere; stub them so header-identity behavior is isolated.
    connector._get_project_public_id = Mock(return_value="proj-pub-1")
    connector._sync_line_items = Mock()
    return connector, invoice_service, reconciliation_repo


def test_fast_path_direct_hit_updates_restamps_and_syncs_lines_without_creating():
    connector, invoice_service, reconciliation_repo = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=7, public_id="inv-pub-7", invoice_number="INV-100")
    invoice_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice(qbo_invoice, [])

    assert result is updated
    invoice_service.read_by_qbo_identity.assert_called_once_with("INV-QBO", "realm-z")
    invoice_service.update_by_public_id.assert_called_once()
    invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id="INV-QBO", realm_id="realm-z", sync_token="3"
    )
    connector._sync_line_items.assert_called_once_with(7, "inv-pub-7", [], "realm-z")
    invoice_service.create.assert_not_called()
    reconciliation_repo.create.assert_not_called()


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", _granted_lock)
def test_fast_path_miss_creates_and_stamps_under_the_create_lock():
    """No dbo-identity match (qbo_id unstamped or unknown) -> a genuine miss,
    resolved under run_identity_fastpath_dbo_only's create lock: no adopt
    candidate here, so a fresh Invoice is created and stamped."""
    connector, invoice_service, _ = _build_invoice_connector()
    connector.project_service = Mock()
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=42)
    qbo_invoice = _make_qbo_invoice()
    invoice_service.read_by_qbo_identity.return_value = None  # both reads: miss
    invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
    created = SimpleNamespace(id=9, public_id="inv-pub-9")
    invoice_service.create.return_value = created
    refreshed = SimpleNamespace(id=9, public_id="inv-pub-9", qbo_id="INV-QBO", realm_id="realm-z")
    invoice_service.read_by_id.return_value = refreshed

    result = connector.sync_from_qbo_invoice(qbo_invoice, [])

    assert result is refreshed
    invoice_service.create.assert_called_once()
    assert invoice_service.create.call_args.kwargs["invoice_number"] == "INV-100"
    invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=9, qbo_id="INV-QBO", realm_id="realm-z", sync_token="3"
    )
    invoice_service.update_by_public_id.assert_not_called()


def test_write_qbo_fields_rowversion_race_raises_runtime_error():
    """A concurrent writer racing the UPDATE (update_by_public_id returns None)
    must raise RuntimeError via raise_concurrent_write_race — never a bare
    AttributeError crash and never a plain ValueError (U-291's ruling: a
    ValueError would be classified as a permanent skip and advance the
    watermark past a transient race)."""
    connector, invoice_service, _ = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    invoice_service.update_by_public_id.return_value = None  # race

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    invoice_service.repo.set_qbo_identity.assert_not_called()
    connector._sync_line_items.assert_not_called()


def test_falsy_qbo_id_is_a_hard_runtime_error_not_a_silent_none():
    connector, invoice_service, _ = _build_invoice_connector()
    with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice(qbo_id=None), [])
    invoice_service.read_by_qbo_identity.assert_not_called()
