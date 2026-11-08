"""U-551b-f1-f8: a repoint keeps the Qty/UnitPrice the first write established."""
from decimal import Decimal
from unittest.mock import MagicMock

from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

NEW_ITEM = QboReferenceType(value="item-42", name="Framing Labor")


def _connector():
    c = PurchaseExpenseConnector(expense_service=MagicMock(), vendor_service=MagicMock(),
                                 vendor_vendor_repo=MagicMock(), qbo_vendor_repo=MagicMock())
    c._get_qbo_customer_ref = MagicMock(return_value=None)
    return c


def _item_target(qty, price):
    return {
        "Id": "1", "Amount": "25.00", "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "item-77", "name": "Old"}, "Qty": qty, "UnitPrice": price},
    }


def _apply(target, quantity, rate, amount):
    _connector()._apply_recode_mutation_to_raw_line(
        target, item_ref=NEW_ITEM, project_id=None, description=None,
        quantity=quantity, rate=rate, amount=amount, is_billable=None, realm_id="r",
    )
    return target["ItemBasedExpenseLineDetail"]


def test_unreconciled_economics_keep_old_qty_unitprice():
    # Kills: dropping the Qty/UnitPrice carry-forward from the old ItemBased detail.
    t = _item_target(3, 10)
    d = _apply(t, Decimal("2"), Decimal("7"), Decimal("25"))  # 2*7 != 25
    assert d["Qty"] == 3 and d["UnitPrice"] == 10
    assert t["Amount"] == "25.00"


def test_null_qty_rate_keep_old_qty_unitprice():
    # Kills: carry applied only when the target has qty/rate.
    d = _apply(_item_target(3, 10), None, None, Decimal("25"))
    assert d["Qty"] == 3 and d["UnitPrice"] == 10


def test_reconciled_target_overwrites_old_qty_unitprice():
    # Kills: carry-forward placed AFTER the reconcile stamp (old would beat the target's).
    t = _item_target(5, 6)
    d = _apply(t, Decimal("3"), Decimal("10"), Decimal("30"))
    assert d["Qty"] == 3.0 and d["UnitPrice"] == 10.0
    assert t["Amount"] == "25.00"


def test_account_based_old_detail_unreconciled_gets_no_qty_unitprice():
    # Kills: inventing Qty/UnitPrice (e.g. from the target) when nothing reconciles on a first write.
    t = {"Id": "1", "Amount": "25.00", "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "58999"}}}
    d = _apply(t, Decimal("2"), Decimal("7"), Decimal("25"))
    assert "Qty" not in d and "UnitPrice" not in d
    assert t["Amount"] == "25.00"
