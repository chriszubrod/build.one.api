"""U-334 — genuine QBO invoice-number collision records a durable ReconciliationIssue.

When sync_from_qbo_invoice finds a local Invoice for the QBO-derived number but that
invoice is already mapped to a DIFFERENT QboInvoice staging row, the connector logs a
warning and falls through to a suffixed CREATE (unchanged control flow). U-334 adds a
record_mapping_issue call alongside that warning so the collision is visible in
qbo.ReconciliationIssue.

Phantom-duplicate PREVENTION (fingerprint re-adopt, mapping-lost heal) is U-034 and is
not re-tested here — see tests/test_qbo_invoice_number_preserve_adopt.py.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_qbo_invoice_number_preserve_adopt import (
    _build_connector,
    _make_invoice,
    _make_qbo_invoice,
)


def _setup_genuine_collision(connector, qbo_invoice, existing_local, created):
    """Shared genuine-collision fixture: number mapped to a different QboInvoice."""
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = existing_local

    def _read_by_invoice_id(invoice_id):
        if invoice_id == existing_local.id:
            return SimpleNamespace(
                id=7, invoice_id=existing_local.id, qbo_invoice_id=9999,
            )
        return None

    connector.mapping_repo.read_by_invoice_id.side_effect = _read_by_invoice_id
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)
    return created


def test_genuine_collision_records_reconciliation_issue_and_still_creates():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    existing_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")
    _setup_genuine_collision(connector, qbo_invoice, existing_local, created)
    connector.invoice_service.create.return_value = created

    mock_qbo_repo_cls = Mock()
    mock_qbo_repo_cls.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="COLLIDING-QBO-ID")

    with patch(
        "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
        mock_qbo_repo_cls,
    ), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue:
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    mock_qbo_repo_cls.return_value.read_by_id.assert_called_once_with(9999)

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
    assert "9999" in details
    assert str(qbo_invoice.id) in details

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_genuine_collision_falls_through_to_suffixed_create_on_duplicate():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    existing_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
    )
    created = _make_invoice(invoice_number="INV-100-2", inv_id=1058, public_id="inv-pub-1058")
    _setup_genuine_collision(connector, qbo_invoice, existing_local, created)

    mock_qbo_repo_cls = Mock()
    mock_qbo_repo_cls.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="COLLIDING-QBO-ID")

    connector.invoice_service.create.side_effect = [
        ValueError("Invoice with number 'INV-100' already exists"),
        created,
    ]

    with patch(
        "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
        mock_qbo_repo_cls,
    ), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue:
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    record_issue.assert_called_once()
    assert connector.invoice_service.create.call_count == 2
    assert connector.invoice_service.create.call_args_list[1].kwargs["invoice_number"] == "INV-100-2"
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_genuine_collision_lookup_failure_still_falls_through_to_create():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    existing_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")
    _setup_genuine_collision(connector, qbo_invoice, existing_local, created)
    connector.invoice_service.create.return_value = created

    mock_qbo_repo_cls = Mock()
    mock_qbo_repo_cls.return_value.read_by_id.side_effect = RuntimeError("transient db error")

    with patch(
        "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
        mock_qbo_repo_cls,
    ), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.record_mapping_issue",
    ) as record_issue:
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    record_issue.assert_called_once()
    assert record_issue.call_args.kwargs["qbo_id"] is None
    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()
