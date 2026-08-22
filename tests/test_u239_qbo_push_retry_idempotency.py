"""U-239 — QBO push retry idempotency after partial local persistence."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.bill.external.schemas import QboBill, QboBillLine, QboReferenceType
from integrations.intuit.qbo.invoice.external.schemas import (
    QboInvoice,
    QboInvoiceLine,
    QboReferenceType as InvoiceQboReferenceType,
    QboSalesItemLineDetail,
)

REALM_ID = "realm-test"
QBO_BILL_ID = "QBO-BILL-123"
QBO_INVOICE_ID = "QBO-INV-456"
LOCAL_QBO_BILL_ID = 500
LOCAL_QBO_INVOICE_ID = 600
LOCAL_QBO_BILL_LINE_ID = 701
LOCAL_QBO_INVOICE_LINE_ID = 801


def _make_bill_connector():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector.__new__(BillBillConnector)
    connector.mapping_repo = MagicMock()
    connector.mapping_repo.read_by_bill_id.return_value = None
    connector.bill_line_item_service = MagicMock()
    connector.vendor_service = MagicMock()
    connector.qbo_bill_repo = MagicMock()
    connector.qbo_bill_line_repo = MagicMock()
    connector.bill_service = MagicMock()
    connector.create_mapping = MagicMock(return_value=SimpleNamespace(id=1))
    return connector


def _make_bill_and_line_item():
    bill = MagicMock()
    bill.id = 7
    bill.public_id = "33333333-3333-3333-3333-333333333333"
    bill.bill_number = "DOC-7"
    bill.bill_date = "2026-01-15"
    bill.due_date = None
    bill.memo = None
    bill.vendor_id = 99
    bill.payment_term_id = None

    line_item = MagicMock()
    line_item.id = 101
    line_item.description = "Materials"
    line_item.amount = Decimal("100.00")
    line_item.sub_cost_code_id = 1
    line_item.project_id = 2
    line_item.is_billable = True
    line_item.is_billed = False
    return bill, line_item


def _make_existing_local_bill():
    return SimpleNamespace(
        id=LOCAL_QBO_BILL_ID,
        qbo_id=QBO_BILL_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )


def _make_created_qbo_bill():
    return QboBill(
        id=QBO_BILL_ID,
        sync_token="0",
        vendor_ref=QboReferenceType(value="v1", name="Acme"),
        line=[
            QboBillLine(
                id="line-1",
                line_num=1,
                description="Materials",
                amount=Decimal("100.00"),
                detail_type="ItemBasedExpenseLineDetail",
            )
        ],
        total_amt=Decimal("100.00"),
        balance=Decimal("100.00"),
    )


def _patch_bill_sync_prereqs(connector, line_item):
    qbo_line = QboBillLine(line_num=1, description="Materials", amount=Decimal("100.00"))
    vendor_ref = QboReferenceType(value="v1", name="Acme")
    connector.bill_line_item_service.read_by_bill_id.return_value = [line_item]
    connector.vendor_service.read_by_id.return_value = SimpleNamespace(name="Acme")
    return (
        patch.object(connector, "_get_qbo_vendor_ref", return_value=vendor_ref),
        patch.object(connector, "_build_qbo_line", return_value=qbo_line),
        patch.object(connector, "_get_ap_account_ref", return_value=QboReferenceType(value="ap1")),
        patch.object(connector, "_get_qbo_sales_term_ref", return_value=None),
    )


def test_sync_to_qbo_bill_retry_reuses_local_mirror_and_creates_mapping():
    connector = _make_bill_connector()
    bill, line_item = _make_bill_and_line_item()
    created_bill = _make_created_qbo_bill()

    existing_local_bill = _make_existing_local_bill()
    existing_local_line = SimpleNamespace(id=LOCAL_QBO_BILL_LINE_ID, qbo_line_id="line-1")

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_bill
    connector.mapping_repo.read_by_qbo_bill_id.return_value = None
    connector.qbo_bill_line_repo.read_by_qbo_bill_id.return_value = [existing_local_line]

    prereqs = _patch_bill_sync_prereqs(connector, line_item)
    with prereqs[0], prereqs[1], prereqs[2], prereqs[3] as term_ref_mock, patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls, patch.object(connector, "_store_qbo_bill_line") as store_line_mock, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.business.service.BillLineItemConnector"
    ) as line_connector_cls:
        client_cls.return_value.__enter__.return_value.create_bill.return_value = created_bill

        result = connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    # U-296: the real call-site wiring (bill.payment_term_id, realm_id) must
    # reach _get_qbo_sales_term_ref exactly -- this is the only test in the
    # suite that exercises sync_to_qbo_bill's actual call to it rather than
    # calling the resolver directly, so it's the one place a swapped/wrong
    # argument at the call site would be caught.
    term_ref_mock.assert_called_once_with(bill.payment_term_id, REALM_ID)

    assert result is existing_local_bill
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_BILL_ID, REALM_ID
    )
    connector.qbo_bill_repo.create.assert_not_called()
    connector.qbo_bill_line_repo.read_by_qbo_bill_id.assert_called_once_with(
        LOCAL_QBO_BILL_ID
    )
    store_line_mock.assert_not_called()
    connector.qbo_bill_line_repo.create.assert_not_called()
    line_connector_cls.return_value.create_mapping.assert_called_once_with(
        bill_line_item_id=101,
        qbo_bill_line_id=LOCAL_QBO_BILL_LINE_ID,
    )
    connector.create_mapping.assert_called_once_with(
        bill_id=7,
        qbo_bill_id=LOCAL_QBO_BILL_ID,
        qbo_id=QBO_BILL_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )


def test_sync_to_qbo_bill_retry_raises_when_mirror_already_mapped_to_different_bill():
    connector = _make_bill_connector()
    bill, line_item = _make_bill_and_line_item()
    created_bill = _make_created_qbo_bill()

    existing_local_bill = _make_existing_local_bill()
    conflicting_mapping = SimpleNamespace(bill_id=999)

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_bill
    connector.mapping_repo.read_by_qbo_bill_id.return_value = conflicting_mapping

    prereqs = _patch_bill_sync_prereqs(connector, line_item)
    with prereqs[0], prereqs[1], prereqs[2], prereqs[3], patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls:
        client_cls.return_value.__enter__.return_value.create_bill.return_value = created_bill

        try:
            connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "already mapped to a different Bill" in str(exc)
            assert str(conflicting_mapping.bill_id) in str(exc)
            assert str(bill.id) in str(exc)

    connector.qbo_bill_repo.create.assert_not_called()
    connector.create_mapping.assert_not_called()


def test_sync_to_qbo_bill_first_create_stores_local_mirror_and_lines():
    connector = _make_bill_connector()
    bill, line_item = _make_bill_and_line_item()
    created_bill = _make_created_qbo_bill()

    created_local_bill = SimpleNamespace(
        id=LOCAL_QBO_BILL_ID,
        qbo_id=QBO_BILL_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )
    created_local_line = SimpleNamespace(id=LOCAL_QBO_BILL_LINE_ID)

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = None
    connector.qbo_bill_repo.create.return_value = created_local_bill
    connector.qbo_bill_line_repo.read_by_qbo_bill_id.return_value = []

    prereqs = _patch_bill_sync_prereqs(connector, line_item)
    with prereqs[0], prereqs[1], prereqs[2], prereqs[3], patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls, patch.object(
        connector, "_store_qbo_bill_line", return_value=created_local_line
    ) as store_line_mock, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.business.service.BillLineItemConnector"
    ) as line_connector_cls:
        client_cls.return_value.__enter__.return_value.create_bill.return_value = created_bill

        result = connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    assert result is created_local_bill
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_BILL_ID, REALM_ID
    )
    connector.qbo_bill_repo.create.assert_called_once()
    store_line_mock.assert_called_once_with(LOCAL_QBO_BILL_ID, created_bill.line[0])
    line_connector_cls.return_value.create_mapping.assert_called_once()
    connector.create_mapping.assert_called_once()


def _make_invoice_connector():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector

    connector = InvoiceInvoiceConnector.__new__(InvoiceInvoiceConnector)
    connector.mapping_repo = MagicMock()
    connector.mapping_repo.read_by_invoice_id.return_value = None
    connector.create_mapping = MagicMock(return_value=SimpleNamespace(id=1))
    return connector


def _make_invoice_and_line_item():
    invoice = MagicMock()
    invoice.id = 8
    invoice.public_id = "44444444-4444-4444-4444-444444444444"
    invoice.invoice_number = "INV-8"
    invoice.invoice_date = "2026-01-20"
    invoice.due_date = None
    invoice.memo = None
    invoice.project_id = 12

    line_item = MagicMock()
    line_item.id = 201
    line_item.source_type = "Manual"
    return invoice, line_item


def _make_existing_local_invoice():
    return SimpleNamespace(
        id=LOCAL_QBO_INVOICE_ID,
        qbo_id=QBO_INVOICE_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )


def _make_created_qbo_invoice():
    return QboInvoice(
        id=QBO_INVOICE_ID,
        sync_token="0",
        customer_ref=InvoiceQboReferenceType(value="c1", name="Customer"),
        line=[
            QboInvoiceLine(
                id="inv-line-1",
                line_num=1,
                description="Labor",
                amount=Decimal("250.00"),
                detail_type="SalesItemLineDetail",
                sales_item_line_detail=QboSalesItemLineDetail(
                    item_ref=InvoiceQboReferenceType(value="item-1", name="Labor"),
                    qty=Decimal("1"),
                    unit_price=Decimal("250.00"),
                ),
            )
        ],
        total_amt=Decimal("250.00"),
        balance=Decimal("250.00"),
    )


def _patch_invoice_sync_prereqs(connector):
    customer_ref = InvoiceQboReferenceType(value="c1", name="Customer")
    qbo_line = QboInvoiceLine(
        line_num=1,
        description="Labor",
        amount=Decimal("250.00"),
        detail_type="SalesItemLineDetail",
        linked_txn=None,
    )
    return (
        patch.object(connector, "_get_qbo_customer_ref", return_value=customer_ref),
        patch.object(connector, "_build_reimburse_charge_lookup", return_value={}),
        patch("entities.invoice_line_item.business.service.InvoiceLineItemService"),
        patch.object(connector, "_build_qbo_invoice_line", return_value=qbo_line),
        patch("integrations.intuit.qbo.invoice.external.client.QboInvoiceClient"),
    )


def test_sync_to_qbo_invoice_retry_reuses_local_mirror_and_creates_mapping():
    connector = _make_invoice_connector()
    invoice, line_item = _make_invoice_and_line_item()
    created_invoice = _make_created_qbo_invoice()

    existing_local_invoice = _make_existing_local_invoice()
    existing_local_line = SimpleNamespace(id=LOCAL_QBO_INVOICE_LINE_ID, qbo_line_id="inv-line-1")

    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_invoice
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    qbo_invoice_line_repo = MagicMock()
    qbo_invoice_line_repo.read_by_qbo_invoice_id.return_value = [existing_local_line]

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0],
        prereqs[1],
        prereqs[2] as ili_svc_cls,
        prereqs[3],
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
            return_value=qbo_invoice_repo,
        ),
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository",
            return_value=qbo_invoice_line_repo,
        ),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        client_cls.return_value.__enter__.return_value.create_invoice.return_value = created_invoice

        result = connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    assert result is existing_local_invoice
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_INVOICE_ID, REALM_ID
    )
    qbo_invoice_repo.create.assert_not_called()
    qbo_invoice_line_repo.read_by_qbo_invoice_id.assert_called_once_with(
        LOCAL_QBO_INVOICE_ID
    )
    qbo_invoice_line_repo.create.assert_not_called()
    connector.create_mapping.assert_called_once_with(
        invoice_id=8,
        qbo_invoice_id=LOCAL_QBO_INVOICE_ID,
        qbo_id=QBO_INVOICE_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )


def test_sync_to_qbo_invoice_retry_raises_when_mirror_already_mapped_to_different_invoice():
    connector = _make_invoice_connector()
    invoice, line_item = _make_invoice_and_line_item()
    created_invoice = _make_created_qbo_invoice()

    existing_local_invoice = _make_existing_local_invoice()
    conflicting_mapping = SimpleNamespace(invoice_id=888)

    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_invoice
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = conflicting_mapping

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0],
        prereqs[1],
        prereqs[2] as ili_svc_cls,
        prereqs[3],
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
            return_value=qbo_invoice_repo,
        ),
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository",
        ),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        client_cls.return_value.__enter__.return_value.create_invoice.return_value = created_invoice

        try:
            connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "already mapped to a different Invoice" in str(exc)
            assert str(conflicting_mapping.invoice_id) in str(exc)
            assert str(invoice.id) in str(exc)

    qbo_invoice_repo.create.assert_not_called()
    connector.create_mapping.assert_not_called()


def test_sync_to_qbo_invoice_first_create_stores_local_mirror_and_lines():
    connector = _make_invoice_connector()
    invoice, line_item = _make_invoice_and_line_item()
    created_invoice = _make_created_qbo_invoice()

    created_local_invoice = SimpleNamespace(
        id=LOCAL_QBO_INVOICE_ID,
        qbo_id=QBO_INVOICE_ID,
        realm_id=REALM_ID,
        sync_token="0",
    )

    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = None
    qbo_invoice_repo.create.return_value = created_local_invoice
    qbo_invoice_line_repo = MagicMock()
    qbo_invoice_line_repo.read_by_qbo_invoice_id.return_value = []

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0],
        prereqs[1],
        prereqs[2] as ili_svc_cls,
        prereqs[3],
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
            return_value=qbo_invoice_repo,
        ),
        patch(
            "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository",
            return_value=qbo_invoice_line_repo,
        ),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        client_cls.return_value.__enter__.return_value.create_invoice.return_value = created_invoice

        result = connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    assert result is created_local_invoice
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_INVOICE_ID, REALM_ID
    )
    qbo_invoice_repo.create.assert_called_once()
    qbo_invoice_line_repo.read_by_qbo_invoice_id.assert_called_once_with(
        LOCAL_QBO_INVOICE_ID
    )
    qbo_invoice_line_repo.create.assert_called_once()
    connector.create_mapping.assert_called_once()
