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
  2. InvoiceInvoiceConnector.sync_from_qbo_invoice's fast path: consistent hit
     (update + SyncToken re-stamp, no mapping-table write), missing hit
     (self-heals a missing mapping row via mapping_repo.create directly, not
     via connector.create_mapping), conflict (hard stop — raises, records the
     issue, never writes to the conflicted Invoice), miss (falls back to the
     pre-existing mapping-table path unchanged, reusing the same
     `_apply_invoice_fields` closure the legacy branch now also calls).
  3. The shared `_apply_invoice_fields` closure's ROWVERSION-race guard
     (update_by_public_id returning None -> RuntimeError via
     raise_concurrent_write_race, never a bare crash or a swallowed None).
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


# --- Section 2: InvoiceInvoiceConnector fast path ---


def _build_invoice_connector():
    mapping_repo = Mock()
    invoice_service = Mock()
    invoice_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = InvoiceInvoiceConnector(
        mapping_repo=mapping_repo,
        invoice_service=invoice_service,
        reconciliation_repo=reconciliation_repo,
    )
    # Out of scope for these tests — project resolution and line-item sync are
    # exercised elsewhere; stub them so header-identity behavior is isolated.
    connector._get_project_public_id = Mock(return_value="proj-pub-1")
    connector._sync_line_items = Mock()
    return connector, mapping_repo, invoice_service, reconciliation_repo


def test_fast_path_consistent_hit_updates_and_restamps_without_mapping_write():
    connector, mapping_repo, invoice_service, _ = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    # Consistent: mapping table agrees with the dbo-identity match.
    mapping_repo.read_by_invoice_id.return_value = SimpleNamespace(
        id=1, invoice_id=7, qbo_invoice_id=8
    )
    mapping_repo.read_by_qbo_invoice_id.return_value = SimpleNamespace(
        id=1, invoice_id=7, qbo_invoice_id=8
    )
    updated = SimpleNamespace(id=7, public_id="inv-pub-7", invoice_number="INV-100")
    invoice_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice(qbo_invoice, [])

    assert result is updated
    invoice_service.update_by_public_id.assert_called_once()
    invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id="INV-QBO", realm_id="realm-z", sync_token="3"
    )
    # Fast path never writes a mapping row on a consistent hit.
    mapping_repo.create.assert_not_called()
    connector._sync_line_items.assert_called_once()


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", _granted_lock)
def test_fast_path_missing_self_heals_mapping_via_repo_create_directly():
    """MISSING state (dbo carries identity, no mapping row on either side) must
    self-heal by creating the mapping row directly via mapping_repo.create — NOT
    via connector.create_mapping, which would redundantly re-stamp identity."""
    connector, mapping_repo, invoice_service, _ = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    mapping_repo.read_by_invoice_id.return_value = None
    mapping_repo.read_by_qbo_invoice_id.return_value = None
    updated = SimpleNamespace(id=7, public_id="inv-pub-7", invoice_number="INV-100")
    invoice_service.update_by_public_id.return_value = updated
    connector.create_mapping = Mock()

    result = connector.sync_from_qbo_invoice(qbo_invoice, [])

    assert result is updated
    mapping_repo.create.assert_called_once_with(invoice_id=7, qbo_invoice_id=8)
    connector.create_mapping.assert_not_called()


def test_fast_path_conflict_hard_stops_never_writes_conflicted_invoice():
    connector, mapping_repo, invoice_service, reconciliation_repo = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    # Conflict: dbo.Invoice 7 carries this identity, but the mapping table
    # binds QboInvoice 8 to a DIFFERENT Invoice (9).
    mapping_repo.read_by_invoice_id.return_value = None
    mapping_repo.read_by_qbo_invoice_id.return_value = SimpleNamespace(
        id=2, invoice_id=9, qbo_invoice_id=8
    )

    with pytest.raises(ValueError, match="InvoiceInvoice identity conflict"):
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    invoice_service.update_by_public_id.assert_not_called()
    invoice_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "invoice_identity_conflict"


def test_fast_path_miss_falls_back_to_legacy_mapping_path_unchanged():
    """No dbo-identity match (qbo_id unstamped or unknown) -> falls straight
    through to the pre-existing mapping-table UPDATE path, still routed through
    the shared _apply_invoice_fields closure."""
    connector, mapping_repo, invoice_service, _ = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    invoice_service.read_by_qbo_identity.return_value = None  # fast path miss

    mapping = SimpleNamespace(id=1, invoice_id=7, qbo_invoice_id=8)
    mapping_repo.read_by_qbo_invoice_id.return_value = mapping
    existing = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_id.return_value = existing
    updated = SimpleNamespace(id=7, public_id="inv-pub-7", invoice_number="INV-100")
    invoice_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice(qbo_invoice, [])

    assert result is updated
    invoice_service.read_by_qbo_identity.assert_called_once_with("INV-QBO", "realm-z")
    invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id="INV-QBO", realm_id="realm-z", sync_token="3"
    )


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", _granted_lock)
def test_apply_invoice_fields_raises_runtime_error_on_rowversion_race():
    """A concurrent writer racing the UPDATE (update_by_public_id returns None)
    must raise RuntimeError via raise_concurrent_write_race — never a bare
    AttributeError crash and never a plain ValueError (U-291's ruling: a
    ValueError would be classified as a permanent skip and advance the
    watermark past a transient race)."""
    connector, mapping_repo, invoice_service, _ = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice()
    direct = SimpleNamespace(
        id=7, public_id="inv-pub-7", row_version="rv", invoice_number="INV-100"
    )
    invoice_service.read_by_qbo_identity.return_value = direct
    mapping_repo.read_by_invoice_id.return_value = None
    mapping_repo.read_by_qbo_invoice_id.return_value = None
    invoice_service.update_by_public_id.return_value = None  # race

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    invoice_service.repo.set_qbo_identity.assert_not_called()


def test_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice(id=8, qbo_id="INV-QBO", realm_id="realm-z")
    qbo_side = SimpleNamespace(id=2, invoice_id=9, qbo_invoice_id=8)
    local_side = SimpleNamespace(id=3, invoice_id=7, qbo_invoice_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_invoice=qbo_invoice, dbo_invoice_id=7,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "invoice_identity_conflict"
    assert "Invoice 9 (mapping 2)" in kwargs["details"]
    assert "DIFFERENT QboInvoice 5" in kwargs["details"]


def test_raise_identity_mapping_conflict_issue_qbo_side_only():
    connector, _, _, reconciliation_repo = _build_invoice_connector()
    qbo_invoice = _make_qbo_invoice(id=8, qbo_id="INV-QBO", realm_id="realm-z")
    qbo_side = SimpleNamespace(id=2, invoice_id=9, qbo_invoice_id=8)

    connector._raise_identity_mapping_conflict_issue(
        qbo_invoice=qbo_invoice, dbo_invoice_id=7,
        local_side_mapping=None, qbo_side_mapping=qbo_side,
    )

    kwargs = reconciliation_repo.create.call_args.kwargs
    assert "Invoice 9 (mapping 2)" in kwargs["details"]
    assert "local-side" not in kwargs["details"]
