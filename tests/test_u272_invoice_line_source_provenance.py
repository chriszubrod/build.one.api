"""Pure-logic tests for U-272 dbo-native invoice source-link provenance."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
    _stamp_source_provenance_or_warn,
)


# ---------------------------------------------------------------------------
# Repository set_source_provenance -> sproc dispatch
# ---------------------------------------------------------------------------


def test_set_source_provenance_calls_sproc():
    repo = InvoiceLineItemRepository()
    cursor = MagicMock()

    with patch(
        "entities.invoice_line_item.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch(
        "entities.invoice_line_item.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.set_source_provenance(
            invoice_line_item_id=42,
            line_num=1,
            qbo_amount=Decimal("100.00"),
            qbo_description="Service",
            service_date="2026-07-15",
            linked_txn_type="ReimburseCharge",
            linked_txn_id="RC-1",
            item_ref_value="ITEM-1",
        )

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "UpsertInvoiceLineItemSourceProvenance"
    assert mock_call.call_args.kwargs["params"] == {
        "InvoiceLineItemId": 42,
        "LineNum": 1,
        "QboAmount": Decimal("100.00"),
        "QboDescription": "Service",
        "ServiceDate": "2026-07-15",
        "LinkedTxnType": "ReimburseCharge",
        "LinkedTxnId": "RC-1",
        "ItemRefValue": "ITEM-1",
    }


def test_set_source_provenance_raises_on_db_error():
    from shared.database import DatabaseError

    repo = InvoiceLineItemRepository()

    with patch(
        "entities.invoice_line_item.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch(
        "entities.invoice_line_item.persistence.repo.call_procedure",
        side_effect=RuntimeError("boom"),
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = MagicMock()
        try:
            repo.set_source_provenance(
                invoice_line_item_id=42,
                line_num=None,
                qbo_amount=None,
                qbo_description=None,
                service_date=None,
                linked_txn_type=None,
                linked_txn_id=None,
                item_ref_value=None,
            )
        except DatabaseError:
            pass
        else:
            raise AssertionError("expected set_source_provenance to raise a mapped DatabaseError")


# ---------------------------------------------------------------------------
# _stamp_source_provenance_or_warn: best-effort, never raises
# ---------------------------------------------------------------------------


def test_stamp_source_provenance_or_warn_forwards_all_fields():
    repo = MagicMock()
    qbo_line = SimpleNamespace(
        line_num=3,
        amount=Decimal("250.50"),
        description="Trim labor",
        service_date="2026-08-01",
        linked_txn_type="Bill",
        linked_txn_id="BILL-9",
        item_ref_value="ITEM-9",
    )

    _stamp_source_provenance_or_warn(
        repo,
        qbo_invoice_line=qbo_line,
        invoice_line_item_id=7,
        context="test context",
    )

    repo.set_source_provenance.assert_called_once_with(
        invoice_line_item_id=7,
        line_num=3,
        qbo_amount=Decimal("250.50"),
        qbo_description="Trim labor",
        service_date="2026-08-01",
        linked_txn_type="Bill",
        linked_txn_id="BILL-9",
        item_ref_value="ITEM-9",
    )


def test_stamp_source_provenance_or_warn_swallows_and_logs(caplog):
    repo = MagicMock()
    repo.set_source_provenance.side_effect = RuntimeError("db down")
    qbo_line = SimpleNamespace(
        line_num=1,
        amount=Decimal("1"),
        description="x",
        service_date=None,
        linked_txn_type=None,
        linked_txn_id=None,
        item_ref_value=None,
    )

    with caplog.at_level("WARNING"):
        _stamp_source_provenance_or_warn(
            repo,
            qbo_invoice_line=qbo_line,
            invoice_line_item_id=7,
            context="ctx",
        )

    assert any(
        "could not stamp dbo source provenance" in record.message for record in caplog.records
    )


# ---------------------------------------------------------------------------
# U-362b/U-362c: read_by_linked_txn -> sproc dispatch, returns the FULL
# sibling set (U-362c: a collision is the COMMON case, not refused any more)
# ---------------------------------------------------------------------------


def _provenance_row(**overrides):
    defaults = dict(
        Id=99, PublicId="pub-99", RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
        CreatedDatetime="2026-07-15 00:00:00", ModifiedDatetime=None,
        InvoiceId=19146, SourceType="BillLineItem",
        BillLineItemId=42, ExpenseLineItemId=None, BillCreditLineItemId=None,
        EmployeeLaborLineItemId=None, SubCostCodeId=None, Description="Materials",
        Quantity=None, Rate=None, Amount=Decimal("500.00"), Markup=None, Price=None,
        IsDraft=False, QboId=None, RealmId=None,
        ProvLineNum=1, ProvQboAmount=Decimal("500.00"), ProvQboDescription="Materials",
        ProvServiceDate="2026-07-15",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_read_by_linked_txn_calls_sproc_and_returns_the_sibling_list():
    repo = InvoiceLineItemRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = [_provenance_row()]

    with patch(
        "entities.invoice_line_item.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch(
        "entities.invoice_line_item.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_by_linked_txn(19146, "ReimburseCharge", "RC-500")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadInvoiceLineItemByInvoiceIdAndLinkedTxn"
    assert mock_call.call_args.kwargs["params"] == {
        "InvoiceId": 19146, "LinkedTxnType": "ReimburseCharge", "LinkedTxnId": "RC-500",
    }
    assert len(result) == 1
    assert result[0].line_item.id == 99
    assert result[0].line_item.source_type == "BillLineItem"
    assert result[0].line_num == 1
    assert result[0].qbo_amount == Decimal("500.00")
    assert result[0].qbo_description == "Materials"
    assert result[0].service_date == "2026-07-15"


def test_read_by_linked_txn_returns_empty_list_when_no_match():
    repo = InvoiceLineItemRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    with patch(
        "entities.invoice_line_item.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch("entities.invoice_line_item.persistence.repo.call_procedure"):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_by_linked_txn(19146, "ReimburseCharge", "RC-500")

    assert result == []


def test_read_by_linked_txn_returns_every_sibling_on_a_collision():
    """U-362c: the recognition key (InvoiceId, LinkedTxnType, LinkedTxnId) is
    NOT DB-enforced unique, and a collision is the COMMON case — every sibling
    invoice line drawn from ONE multi-line source Bill/Expense shares it
    (LinkedTxnId is the source TRANSACTION id, no per-line TxnLineId). The
    repo layer used to `fetchone()` and refuse (return None) here, which
    treated the common case as "not found" and fell through to a phantom
    Manual duplicate. It must now return the FULL set, in whatever order the
    sproc gives them — the connector's tie-break (find_stale_identity_orphan)
    picks the right one, not this method."""
    repo = InvoiceLineItemRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        _provenance_row(Id=99, Amount=Decimal("500.00"), ProvQboAmount=Decimal("500.00"), ProvLineNum=1),
        _provenance_row(Id=150, Amount=Decimal("300.00"), ProvQboAmount=Decimal("300.00"), ProvLineNum=2),
    ]

    with patch(
        "entities.invoice_line_item.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch(
        "entities.invoice_line_item.persistence.repo.call_procedure"
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_by_linked_txn(19146, "ReimburseCharge", "RC-500")

    assert [sib.line_item.id for sib in result] == [99, 150]
    assert [sib.qbo_amount for sib in result] == [Decimal("500.00"), Decimal("300.00")]


