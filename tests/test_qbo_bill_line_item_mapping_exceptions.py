"""
Regression tests for BillLineItemConnector.sync_from_qbo_bill_line's create_mapping()
exception handling (U-228 Pass-1 hunt).

dbo.BillLineItem carries no uniqueness constraint of any kind (unlike dbo.Bill, which is
protected by UQ_Bill_VendorId_BillNumber_BillDate), so a concurrent-pull race that loses the
qbo.BillLineItemBillLine mapping insert must propagate — not be silently swallowed — so the
caller's per-line loop in BillBillConnector._sync_line_items can turn it into a RuntimeError
that either triggers rollback_orphan_header (new-bill CREATE path) or holds the watermark
(existing-bill UPDATE path). Only a plain ValueError (the pre-check "already mapped" case) is
the sanctioned swallow-and-continue outcome.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shared.database import DatabaseConstraintError, map_database_error
from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector


def _unique_violation() -> DatabaseConstraintError:
    """A realistic race-loser error, built the same way production code produces one."""
    raw = (
        "('23000', \"[23000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
        "Violation of UNIQUE KEY constraint 'UQ_BillLineItemBillLine_QboBillLineId'. "
        "Cannot insert duplicate key in object 'qbo.BillLineItemBillLine'. (2627)\")"
    )
    error = map_database_error(Exception(raw))
    assert isinstance(error, DatabaseConstraintError), f"fixture drifted: got {type(error)}"
    return error


def _make_qbo_bill_line(*, line_id=1, amount=Decimal("108.82")):
    return SimpleNamespace(
        id=line_id,
        description="Service Charge",
        amount=amount,
        qty=None,
        unit_price=None,
        markup_percent=None,
        billable_status="NotBillable",
        item_ref_value=None,
        customer_ref_value=None,
    )


def _build_connector():
    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_line_id.return_value = None
    # create_mapping()'s own pre-checks — no existing mapping either side.
    mapping_repo.read_by_bill_line_item_id.return_value = None
    mapping_repo.read_by_qbo_bill_line_id.return_value = None

    bill_service = Mock()
    bill_service.read_by_id.return_value = SimpleNamespace(id=100, public_id="bill-pub-1")

    bill_line_item_service = Mock()
    bill_line_item_service.read_by_bill_id.return_value = []  # no unmapped lines (Shape B miss)
    bill_line_item_service.create.return_value = SimpleNamespace(id=200, public_id="bli-pub-1")

    connector = BillLineItemConnector(
        mapping_repo=mapping_repo,
        bill_line_item_service=bill_line_item_service,
        bill_service=bill_service,
        bill_bill_repo=Mock(),
        qbo_item_repo=Mock(),
        qbo_bill_line_repo=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
        project_service=Mock(),
    )
    return connector, mapping_repo


def test_sync_from_qbo_bill_line_value_error_from_create_mapping_is_swallowed():
    """The sanctioned case: a plain ValueError (pre-check 'already mapped') logs and returns."""
    connector, mapping_repo = _build_connector()
    mapping_repo.create.side_effect = ValueError("QboBillLine 1 is already mapped to BillLineItem 999")

    result = connector.sync_from_qbo_bill_line(100, _make_qbo_bill_line())

    assert result.id == 200  # returns the (now-unmapped) line_item; does not raise


def test_sync_from_qbo_bill_line_database_constraint_error_from_create_mapping_propagates():
    """
    Regression for U-228 fix-round: a concurrent-pull race that loses the mapping INSERT must
    raise, not be swallowed — dbo.BillLineItem has no unique constraint to fall back on, so a
    swallowed race here would leave a permanent, undetectable duplicate line item.
    """
    connector, mapping_repo = _build_connector()
    mapping_repo.create.side_effect = _unique_violation()

    with pytest.raises(DatabaseConstraintError):
        connector.sync_from_qbo_bill_line(100, _make_qbo_bill_line())
