"""U-334 — genuine QBO invoice-number collision records a durable ReconciliationIssue.

When sync_from_qbo_invoice finds a local Invoice for the QBO-derived number but that
invoice already carries a DIFFERENT dbo-native QBO identity (U-356: `dbo.Invoice.
QboId/RealmId` replaced the retired qbo.InvoiceInvoice "mapped to a different
QboInvoice" read), the connector logs a warning and falls through to a suffixed
CREATE (unchanged control flow). U-334 added a record_mapping_issue call alongside
that warning so the collision is visible in qbo.ReconciliationIssue — the recorded
qbo_id is now the holder's own QboId read straight off the row (no staging hop).

Phantom-duplicate PREVENTION (fingerprint re-adopt, identity-lost heal) is U-034 and
is not re-tested here — see tests/test_qbo_invoice_number_preserve_adopt.py.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_qbo_invoice_number_preserve_adopt import (
    _build_connector,
    _make_invoice,
    _make_qbo_invoice,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _setup_genuine_collision(connector, existing_local, created):
    """Shared genuine-collision fixture: number matches a local Invoice that
    already carries a different QBO identity."""
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = existing_local
    connector.invoice_service.read_by_id.side_effect = (
        lambda invoice_id: existing_local if invoice_id == existing_local.id else created
    )


def test_genuine_collision_records_reconciliation_issue_and_still_creates():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    existing_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
        qbo_id="COLLIDING-QBO-ID", realm_id="realm-1",
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")
    _setup_genuine_collision(connector, existing_local, created)
    connector.invoice_service.create.return_value = created

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue:
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    record_issue.assert_called_once()
    assert record_issue.call_args.args[0] is connector.reconciliation_repo
    kwargs = record_issue.call_args.kwargs
    assert kwargs["drift_type"] == "duplicate_qbo_invoice_number"
    assert kwargs["entity_type"] == "Invoice"
    assert kwargs["entity_public_id"] == str(existing_local.public_id)
    assert kwargs["qbo_id"] == "COLLIDING-QBO-ID"
    assert kwargs["realm_id"] == qbo_invoice.realm_id
    assert kwargs["severity"] == "critical"

    details = kwargs["details"]
    assert "INV-100" in details
    assert str(existing_local.id) in details
    assert "COLLIDING-QBO-ID" in details
    assert str(qbo_invoice.id) in details

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_genuine_collision_falls_through_to_suffixed_create_on_duplicate():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    existing_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
        qbo_id="COLLIDING-QBO-ID", realm_id="realm-1",
    )
    created = _make_invoice(invoice_number="INV-100-2", inv_id=1058, public_id="inv-pub-1058")
    _setup_genuine_collision(connector, existing_local, created)

    connector.invoice_service.create.side_effect = [
        ValueError("Invoice with number 'INV-100' already exists"),
        created,
    ]

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue:
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    record_issue.assert_called_once()
    assert connector.invoice_service.create.call_count == 2
    assert connector.invoice_service.create.call_args_list[1].kwargs["invoice_number"] == "INV-100-2"
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_same_identity_on_number_match_is_not_a_collision():
    """The number-matched row already carrying THIS exact (qbo_id, realm_id) is
    a benign re-resolve (only reachable when the fast path's direct read
    missed it, e.g. a realm-normalization edge) — never recorded as a
    duplicate-number collision."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    same = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
        qbo_id="975", realm_id="realm-1",
    )
    _setup_genuine_collision(connector, same, same)
    connector._header_fingerprint_matches = lambda *a: True
    connector._has_qbo_line_provenance = lambda *a: True
    connector.invoice_service.update_by_public_id.return_value = same

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue, patch(
        "entities.invoice_line_item.business.service.InvoiceLineItemService"
    ) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = []
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    record_issue.assert_not_called()
    connector.invoice_service.create.assert_not_called()
