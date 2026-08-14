"""Pure-logic tests for U-238b dbo-native QBO identity on line-item entities."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.identity_drift import LINE_ENTITY_SPECS, classify_qbo_identity_drift


# ---------------------------------------------------------------------------
# classify_qbo_identity_drift (line entities: QboId + RealmId only)
# ---------------------------------------------------------------------------


def test_line_entity_specs_count():
    assert len(LINE_ENTITY_SPECS) == 4


@pytest.mark.parametrize(
    "dbo_qbo,dbo_realm,has_mapping,staging_qbo,staging_realm,expected",
    [
        (None, None, False, None, None, "match"),
        (None, None, True, "line-1", "realm", "pending_backfill"),
        ("line-99", "realm", False, None, None, "orphan_dbo_value"),
        ("line-1", "realm", True, "line-1", "realm", "match"),
        ("line-1", "realm", True, "line-2", "realm", "drift"),
        ("line-1", "realm-a", True, "line-1", "realm-b", "drift"),
    ],
)
def test_classify_qbo_identity_drift_line_fields(
    dbo_qbo, dbo_realm, has_mapping, staging_qbo, staging_realm, expected
):
    """Line items use has_sync_token=False — same path as Project/Company headers."""
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


# ---------------------------------------------------------------------------
# Repository set_qbo_identity → sproc dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_path,sproc",
    [
        ("entities.bill_line_item.persistence.repo.BillLineItemRepository", "SetBillLineItemQboIdentity"),
        ("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository", "SetInvoiceLineItemQboIdentity"),
        ("entities.expense_line_item.persistence.repo.ExpenseLineItemRepository", "SetExpenseLineItemQboIdentity"),
        (
            "entities.bill_credit_line_item.persistence.repo.BillCreditLineItemRepository",
            "SetBillCreditLineItemQboIdentity",
        ),
    ],
)
def test_set_qbo_identity_calls_sproc(repo_path, sproc):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stolen=False)

    expected_params = {"Id": 42, "QboId": "qbo-line-1", "RealmId": "realm-1"}

    with patch(f"{repo_path.rsplit('.', 1)[0]}.get_connection") as mock_conn_ctx, patch(
        f"{repo_path.rsplit('.', 1)[0]}.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.set_qbo_identity(id=42, qbo_id="qbo-line-1", realm_id="realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == sproc
    assert mock_call.call_args.kwargs["params"] == expected_params


@pytest.mark.parametrize(
    "repo_path,entity_label",
    [
        ("entities.bill_line_item.persistence.repo.BillLineItemRepository", "BillLineItem"),
        ("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository", "InvoiceLineItem"),
        ("entities.expense_line_item.persistence.repo.ExpenseLineItemRepository", "ExpenseLineItem"),
        (
            "entities.bill_credit_line_item.persistence.repo.BillCreditLineItemRepository",
            "BillCreditLineItem",
        ),
    ],
)
def test_set_qbo_identity_stolen_logs_warning(repo_path, entity_label, caplog):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stolen=True)

    with patch(f"{repo_path.rsplit('.', 1)[0]}.get_connection") as mock_conn_ctx, patch(
        f"{repo_path.rsplit('.', 1)[0]}.call_procedure"
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        with caplog.at_level("WARNING"):
            repo.set_qbo_identity(id=7, qbo_id="line-stolen", realm_id="realm-x")

    assert any(entity_label in record.message and "stole QBO identity" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Connector dual-write (create + update paths)
# ---------------------------------------------------------------------------


def test_invoice_line_connector_create_path_dual_writes_identity():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None
    mapping_repo.read_by_invoice_line_item_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = SimpleNamespace(id=200, public_id="ili-pub-1")
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
    )
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=None)
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-REAL",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line, realm_id="realm-create")

    invoice_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-INV-LINE-REAL",
        realm_id="realm-create",
    )


def test_invoice_line_connector_update_path_dual_writes_identity():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping = SimpleNamespace(id=10, invoice_line_item_id=200)
    line_item = SimpleNamespace(id=200, public_id="ili-pub-1", row_version="rv", amount=Decimal("100"))

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = mapping

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.read_by_id.return_value = line_item
    invoice_line_item_service.update_by_public_id.return_value = line_item
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
    )
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-UPD",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line, realm_id="realm-update")

    invoice_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-INV-LINE-UPD",
        realm_id="realm-update",
    )


def test_vendor_credit_line_connector_create_path_dual_writes_identity():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_line_id.return_value = None

    bill_credit_line_item_service = MagicMock()
    line_item = SimpleNamespace(id=300)
    bill_credit_line_item_service.create.return_value = line_item
    bill_credit_line_item_service.repo = MagicMock()

    connector = VendorCreditLineItemConnector()
    connector.mapping_repo = mapping_repo
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector._get_project_public_id = MagicMock(return_value=None)
    connector._get_sub_cost_code_id = MagicMock(return_value=None)
    connector._match_unmapped_by_fingerprint = MagicMock(return_value=None)

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-VC-LINE-REAL",
        description="Credit",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )

    connector.sync_from_qbo_line(100, "bc-pub", qbo_line, realm_id="realm-create")

    bill_credit_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=300,
        qbo_id="QBO-VC-LINE-REAL",
        realm_id="realm-create",
    )


def test_vendor_credit_line_connector_update_path_dual_writes_identity():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    mapping = SimpleNamespace(id=10, bill_credit_line_item_id=300)
    existing = SimpleNamespace(id=300, public_id="bcli-pub", row_version="rv")

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_line_id.return_value = mapping

    bill_credit_line_item_service = MagicMock()
    bill_credit_line_item_service.read_by_id.return_value = existing
    bill_credit_line_item_service.update_by_public_id.return_value = existing
    bill_credit_line_item_service.repo = MagicMock()

    connector = VendorCreditLineItemConnector()
    connector.mapping_repo = mapping_repo
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector._get_project_public_id = MagicMock(return_value=None)
    connector._get_sub_cost_code_id = MagicMock(return_value=None)

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-VC-LINE-UPD",
        description="Credit",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )

    connector.sync_from_qbo_line(100, "bc-pub", qbo_line, realm_id="realm-update")

    bill_credit_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=300,
        qbo_id="QBO-VC-LINE-UPD",
        realm_id="realm-update",
    )


def test_invoice_sync_from_qbo_invoice_forwards_realm_id_to_line_connector():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    mapping = SimpleNamespace(id=1, invoice_id=7, qbo_invoice_id=8)
    invoice = SimpleNamespace(
        id=7,
        public_id="inv-pub-7",
        row_version="rv",
        invoice_number="INV-1",
    )
    qbo_invoice = SimpleNamespace(
        id=8,
        qbo_id="INV-QBO",
        realm_id="realm-forward-test",
        customer_ref_value="cust1",
        doc_number="INV-1",
        txn_date="2026-01-01",
        due_date="",
        private_note="",
        total_amt=Decimal("100"),
    )
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-FWD",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    connector = InvoiceInvoiceConnector(
        mapping_repo=MagicMock(),
        line_mapping_repo=MagicMock(),
        invoice_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=MagicMock(),
        customer_project_repo=MagicMock(),
    )
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = mapping
    connector._get_project_public_id = MagicMock(return_value="proj-pub")
    connector.invoice_service.read_by_id.return_value = invoice
    connector.invoice_service.update_by_public_id.return_value = invoice
    connector.invoice_service.repo = MagicMock()

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service.InvoiceLineItemConnector"
    ) as mock_line_connector_cls:
        connector.sync_from_qbo_invoice(qbo_invoice, [qbo_line])

    mock_line_connector_cls.return_value.sync_from_qbo_invoice_line.assert_called_once_with(
        invoice.id,
        invoice.public_id,
        qbo_line,
        "realm-forward-test",
    )
