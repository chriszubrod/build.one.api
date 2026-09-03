"""U-239 — QBO push retry idempotency after partial local persistence."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
    connector.bill_line_item_service = MagicMock()
    connector.vendor_service = MagicMock()
    connector.qbo_bill_repo = MagicMock()
    connector.qbo_bill_line_repo = MagicMock()
    connector.bill_service = MagicMock()
    connector.reconciliation_repo = MagicMock()
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


def test_sync_to_qbo_bill_already_pushed_short_circuits_to_existing_local_mirror():
    """U-355: dbo.Bill.QboId is the sole "already pushed" signal now. A
    verified identity + an existing qbo.Bill staging row short-circuits to
    that row, never touching QBO or rebuilding lines — the whole vendor-ref/
    line-build/QboBillClient machinery below is unreachable on this path."""
    connector = _make_bill_connector()
    bill, _line_item = _make_bill_and_line_item()
    bill.qbo_id = QBO_BILL_ID
    bill.realm_id = REALM_ID

    existing_local_bill = _make_existing_local_bill()
    connector.bill_service.read_by_qbo_identity.return_value = SimpleNamespace(id=bill.id)
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_bill

    result = connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    assert result is existing_local_bill
    connector.bill_service.read_by_qbo_identity.assert_called_once_with(QBO_BILL_ID, REALM_ID)
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_BILL_ID, REALM_ID
    )
    connector.qbo_bill_repo.create.assert_not_called()
    connector.bill_service.repo.set_qbo_identity.assert_not_called()


def test_sync_to_qbo_bill_refuses_when_identity_no_longer_verifies():
    """bill.qbo_id is set, but a fresh dbo-only re-read resolves to a
    DIFFERENT Bill (a stale/reassigned identity) — refuse rather than push
    under disputed identity."""
    connector = _make_bill_connector()
    bill, _line_item = _make_bill_and_line_item()
    bill.qbo_id = QBO_BILL_ID
    bill.realm_id = REALM_ID

    connector.bill_service.read_by_qbo_identity.return_value = SimpleNamespace(id=999)

    try:
        connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no longer resolves back to it" in str(exc)

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    connector.qbo_bill_repo.create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "bill_identity_conflict"


def test_sync_to_qbo_bill_refuses_when_verified_identity_has_no_local_staging_row():
    """bill.qbo_id verifies (still resolves back to this same Bill), but no
    local qbo.Bill staging row exists for it — a genuine data-integrity
    anomaly (the stamp and the staging-cache write happen together), not the
    ordinary never-pushed case. Refuse rather than risk pushing a duplicate."""
    connector = _make_bill_connector()
    bill, _line_item = _make_bill_and_line_item()
    bill.qbo_id = QBO_BILL_ID
    bill.realm_id = REALM_ID

    connector.bill_service.read_by_qbo_identity.return_value = SimpleNamespace(id=bill.id)
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = None

    try:
        connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no local qbo.Bill staging row exists" in str(exc)

    connector.qbo_bill_repo.create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "bill_staging_row_missing"


def test_sync_to_qbo_bill_first_create_stores_local_mirror_and_lines():
    connector = _make_bill_connector()
    bill, line_item = _make_bill_and_line_item()
    bill.qbo_id = None  # U-355: falsy -> skip the already-pushed short-circuit
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
    # U-363: the push-path line stamp verifies via a re-read (same shape as
    # scripts/reconcile_project.py's repair function) — stub it to reflect a
    # landed stamp so this test exercises the success path, not the
    # verification-failure branch a bare MagicMock would otherwise fall into.
    connector.bill_line_item_service.repo.read_by_id.return_value = SimpleNamespace(
        id=line_item.id, public_id="line-pub", qbo_id=created_bill.line[0].id, realm_id=REALM_ID,
    )

    prereqs = _patch_bill_sync_prereqs(connector, line_item)
    with prereqs[0], prereqs[1], prereqs[2], prereqs[3], patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls, patch.object(
        connector, "_store_qbo_bill_line", return_value=created_local_line
    ) as store_line_mock:
        client_cls.return_value.__enter__.return_value.create_bill.return_value = created_bill

        result = connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    assert result is created_local_bill
    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(
        QBO_BILL_ID, REALM_ID
    )
    connector.qbo_bill_repo.create.assert_called_once()
    store_line_mock.assert_called_once_with(LOCAL_QBO_BILL_ID, created_bill.line[0])
    # U-363: no more qbo.BillLineItemBillLine mapping row — the matching
    # BillLineItem's dbo-native identity is stamped directly by line_num match.
    connector.bill_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=line_item.id, qbo_id=created_bill.line[0].id, realm_id=REALM_ID,
    )
    connector.bill_service.repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id=QBO_BILL_ID, realm_id=REALM_ID, sync_token="0"
    )
    connector.reconciliation_repo.create.assert_not_called()  # stamp verified landed


def test_sync_to_qbo_bill_first_create_records_issue_when_line_stamp_does_not_land():
    """U-363 altitude-review fix: the push-path line stamp is a void write that
    can silently no-op (SetBillLineItemQboIdentity's atomic-pair guard) — the
    Bill is genuinely already live in QBO by this point, so a failed stamp
    must be flagged (ReconciliationIssue), not silently swallowed the way the
    old create_mapping()'s bare try/except would have let it slide."""
    connector = _make_bill_connector()
    bill, line_item = _make_bill_and_line_item()
    bill.qbo_id = None
    created_bill = _make_created_qbo_bill()

    created_local_bill = SimpleNamespace(
        id=LOCAL_QBO_BILL_ID, qbo_id=QBO_BILL_ID, realm_id=REALM_ID, sync_token="0",
    )
    created_local_line = SimpleNamespace(id=LOCAL_QBO_BILL_LINE_ID)

    connector.qbo_bill_repo.read_by_qbo_id_and_realm_id.return_value = None
    connector.qbo_bill_repo.create.return_value = created_local_bill
    connector.qbo_bill_line_repo.read_by_qbo_bill_id.return_value = []
    # The stamp call itself doesn't raise, but the re-read shows the row still
    # unstamped — the atomic-pair guard silently declined (e.g. realm_id came
    # back empty from _get_ap_account_ref's own realm resolution).
    connector.bill_line_item_service.repo.read_by_id.return_value = SimpleNamespace(
        id=line_item.id, public_id="line-pub", qbo_id=None, realm_id=None,
    )

    prereqs = _patch_bill_sync_prereqs(connector, line_item)
    with prereqs[0], prereqs[1], prereqs[2], prereqs[3], patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls, patch.object(
        connector, "_store_qbo_bill_line", return_value=created_local_line
    ):
        client_cls.return_value.__enter__.return_value.create_bill.return_value = created_bill

        result = connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    assert result is created_local_bill  # the push itself still succeeds/returns
    connector.reconciliation_repo.create.assert_called_once()
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bli_line_push_stamp_failed"
    assert kwargs["entity_type"] == "BillLineItem"
    assert kwargs["qbo_id"] == created_bill.line[0].id
    assert "still reads QboId IS NULL" in kwargs["details"]


def _make_invoice_connector(*, current_qbo_id=None, current_realm_id=None, verify_hit_id=None):
    """U-356: the push path resolves "already pushed" off dbo.Invoice.QboId/RealmId
    (re-read by id, then verify_identity_dbo_only) — no qbo.InvoiceInvoice mapping
    repo. `current_qbo_id`/`current_realm_id` model the by-id row's identity;
    `verify_hit_id` models what a fresh read_by_qbo_identity resolves to."""
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector

    connector = InvoiceInvoiceConnector.__new__(InvoiceInvoiceConnector)
    connector.invoice_service = MagicMock()
    connector.invoice_service.repo = MagicMock()
    connector.invoice_service.read_by_id.return_value = SimpleNamespace(
        id=8, public_id="44444444-4444-4444-4444-444444444444",
        qbo_id=current_qbo_id, realm_id=current_realm_id,
    )
    connector.invoice_service.read_by_qbo_identity.return_value = (
        SimpleNamespace(id=verify_hit_id) if verify_hit_id is not None else None
    )
    connector.reconciliation_repo = MagicMock()
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
    # U-356: no holder for this identity -> stamp dbo identity onto THIS invoice.
    connector.invoice_service.read_by_qbo_identity.assert_called_once_with(QBO_INVOICE_ID, REALM_ID)
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=8, qbo_id=QBO_INVOICE_ID, realm_id=REALM_ID, sync_token="0"
    )


def test_sync_to_qbo_invoice_retry_raises_when_mirror_already_mapped_to_different_invoice():
    connector = _make_invoice_connector()
    invoice, line_item = _make_invoice_and_line_item()
    created_invoice = _make_created_qbo_invoice()

    existing_local_invoice = _make_existing_local_invoice()
    # U-356: a DIFFERENT dbo.Invoice already carries (created QboId, realm).
    connector.invoice_service.read_by_qbo_identity.return_value = SimpleNamespace(id=888)

    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_invoice

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
            assert "888" in str(exc)
            assert str(invoice.id) in str(exc)

    qbo_invoice_repo.create.assert_not_called()
    connector.invoice_service.repo.set_qbo_identity.assert_not_called()


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
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=8, qbo_id=QBO_INVOICE_ID, realm_id=REALM_ID, sync_token="0"
    )


# --- U-356: the already-pushed (UPDATE) leg, dbo-native + verified ------------


def test_sync_to_qbo_invoice_already_pushed_verified_takes_update_path():
    """dbo.Invoice carries a verified identity whose qbo.Invoice staging row
    resolves -> UPDATE the QBO invoice in place; never create, never re-stamp."""
    connector = _make_invoice_connector(
        current_qbo_id=QBO_INVOICE_ID, current_realm_id=REALM_ID, verify_hit_id=8,
    )
    invoice, line_item = _make_invoice_and_line_item()
    existing_local_invoice = SimpleNamespace(
        id=LOCAL_QBO_INVOICE_ID, qbo_id=QBO_INVOICE_ID, realm_id=REALM_ID,
        sync_token="0", row_version_bytes=b"rv",
    )
    updated = _make_created_qbo_invoice()

    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = existing_local_invoice
    refreshed = SimpleNamespace(id=LOCAL_QBO_INVOICE_ID)
    qbo_invoice_repo.read_by_id.return_value = refreshed

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0], prereqs[1], prereqs[2] as ili_svc_cls, prereqs[3],
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
              return_value=qbo_invoice_repo),
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository"),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        client = client_cls.return_value.__enter__.return_value
        client.get_invoice.return_value = SimpleNamespace(sync_token="7")
        client.update_invoice.return_value = updated

        result = connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    assert result is refreshed
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.assert_called_once_with(QBO_INVOICE_ID, REALM_ID)
    client.get_invoice.assert_called_once_with(QBO_INVOICE_ID)
    client.update_invoice.assert_called_once()
    client.create_invoice.assert_not_called()
    qbo_invoice_repo.update_by_qbo_id.assert_called_once()
    connector.invoice_service.repo.set_qbo_identity.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_sync_to_qbo_invoice_refuses_when_invoice_deleted_between_reads():
    """The by-id re-read misses (deleted after the outbox handler's by-public-id
    read): refuse, never fall through to CREATE a QBO Invoice for a missing row."""
    connector = _make_invoice_connector()
    connector.invoice_service.read_by_id.return_value = None
    invoice, line_item = _make_invoice_and_line_item()

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0], prereqs[1], prereqs[2] as ili_svc_cls, prereqs[3],
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository"),
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository"),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        with pytest.raises(ValueError, match="no longer exists locally"):
            connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    client_cls.return_value.__enter__.return_value.create_invoice.assert_not_called()
    connector.invoice_service.repo.set_qbo_identity.assert_not_called()


def test_sync_to_qbo_invoice_refuses_unverifiable_identity_and_records_issue():
    """dbo.Invoice.QboId is set but a fresh dbo-only read resolves to a DIFFERENT
    Invoice (reassigned/stolen) -> record invoice_identity_conflict + raise,
    before any QBO call."""
    connector = _make_invoice_connector(
        current_qbo_id=QBO_INVOICE_ID, current_realm_id=REALM_ID, verify_hit_id=999,
    )
    invoice, line_item = _make_invoice_and_line_item()
    qbo_invoice_repo = MagicMock()

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0], prereqs[1], prereqs[2] as ili_svc_cls, prereqs[3],
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
              return_value=qbo_invoice_repo),
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository"),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        with pytest.raises(ValueError, match="no longer resolves back"):
            connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    client_cls.return_value.__enter__.return_value.create_invoice.assert_not_called()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.assert_not_called()
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "invoice_identity_conflict"
    assert kwargs["entity_type"] == "Invoice"
    assert kwargs["qbo_id"] == QBO_INVOICE_ID
    assert kwargs["realm_id"] == REALM_ID


def test_sync_to_qbo_invoice_refuses_when_verified_identity_has_no_staging_row():
    """Verified identity but no local qbo.Invoice staging row: a data-integrity
    anomaly, not "never pushed" -> record invoice_staging_row_missing + raise
    rather than risk a duplicate Invoice in QBO."""
    connector = _make_invoice_connector(
        current_qbo_id=QBO_INVOICE_ID, current_realm_id=REALM_ID, verify_hit_id=8,
    )
    invoice, line_item = _make_invoice_and_line_item()
    qbo_invoice_repo = MagicMock()
    qbo_invoice_repo.read_by_qbo_id_and_realm_id.return_value = None

    prereqs = _patch_invoice_sync_prereqs(connector)
    with (
        prereqs[0], prereqs[1], prereqs[2] as ili_svc_cls, prereqs[3],
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
              return_value=qbo_invoice_repo),
        patch("integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceLineRepository"),
        prereqs[4] as client_cls,
    ):
        ili_svc_cls.return_value.read_by_invoice_id.return_value = [line_item]
        with pytest.raises(ValueError, match="no local qbo.Invoice staging row"):
            connector.sync_to_qbo_invoice(invoice=invoice, realm_id=REALM_ID)

    client_cls.return_value.__enter__.return_value.create_invoice.assert_not_called()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "invoice_staging_row_missing"
