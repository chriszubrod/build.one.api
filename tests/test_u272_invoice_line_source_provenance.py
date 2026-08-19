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


