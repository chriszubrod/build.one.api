"""U-797c5a: the shared QBO expense-line helper must leave the Bill line byte-identical.

GOLDEN was captured from the Bill connector's `_build_qbo_line` BEFORE the lift into
`integrations/intuit/qbo/base/line_push.py` (fakes only, no DB/QBO). Every case here
must keep producing exactly that output after the Bill connector calls the helper.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.cost_code_resolver import HoldingCodeRefused
from integrations.intuit.qbo.base.line_push import build_item_expense_line_plan
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.external.schemas import QboReferenceType
from entities.bill_credit_line_item.business.model import BillCreditLineItem


def _bill_plan(line_item, line_num, realm_id, **kwargs):
    # Bill's accessors: unit price is `.rate`, markup is `.markup` (BillLineItem).
    return build_item_expense_line_plan(
        line_item, line_num, realm_id,
        unit_price_of=lambda li: li.rate,
        markup_of=lambda li: li.markup,
        **kwargs,
    )

GOLDEN = json.loads(r'''{
 "billable_billed": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "HasBeenBilled",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 2
  }
 },
 "billable_customer": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "Billable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 1
  }
 },
 "billable_no_project": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "NotBillable",
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 1
  }
 },
 "billable_null_default": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "Billable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 1
  }
 },
 "billable_project_no_customer": {
  "message": "BillLineItem 11: billable line on project_id=9 has no QBO CustomerRef mapping. Ensure the Project has a QBO identity stamped (dbo.Project.QboId/RealmId, e.g. by syncing it from QBO) before syncing this bill.",
  "raises": "ValueError"
 },
 "holding_item": {
  "message": "422: holding",
  "raises": "HoldingCodeRefused"
 },
 "markup_percent": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "Billable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "MarkupInfo": {
     "Percent": 10.0
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 1
  }
 },
 "no_amount_no_qty": {
  "ok": {
   "Amount": "0",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "Billable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    }
   },
   "LineNum": 1
  }
 },
 "no_amount_qty_rate": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "Billable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 1
  }
 },
 "no_scc": {
  "message": "BillLineItem 11 has no sub_cost_code_id. All line items require a SubCostCode for QBO sync.",
  "raises": "ValueError"
 },
 "non_billable": {
  "ok": {
   "Amount": "21.00",
   "Description": "Desc",
   "DetailType": "ItemBasedExpenseLineDetail",
   "ItemBasedExpenseLineDetail": {
    "BillableStatus": "NotBillable",
    "CustomerRef": {
     "name": "Proj A",
     "value": "CUST-3"
    },
    "ItemRef": {
     "name": "Lumber",
     "value": "ITEM-7"
    },
    "Qty": "2",
    "UnitPrice": "10.50"
   },
   "LineNum": 3
  }
 },
 "unmapped_item": {
  "message": "BillLineItem 11: no QBO Item mapping for sub_cost_code_id=5. Map the SubCostCode to a QBO Item before syncing.",
  "raises": "ValueError"
 }
}
''')

ITEM = QboReferenceType(value="ITEM-7", name="Lumber")
CUST = QboReferenceType(value="CUST-3", name="Proj A")


def _line(**overrides):
    base = dict(
        id=11, sub_cost_code_id=5, project_id=9, description="Desc",
        quantity=Decimal("2"), rate=Decimal("10.50"), amount=Decimal("21.00"),
        is_billable=True, is_billed=None, markup=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ok_item(scc, realm=None):
    return ITEM


def _ok_cust(pid):
    return CUST


def _no_cust(pid):
    return None


def _holding(scc, realm=None):
    raise HoldingCodeRefused("holding")


def _unmapped(scc, realm=None):
    return None


def _bill_connector(item_fn, cust_fn):
    connector = object.__new__(BillBillConnector)
    connector._get_qbo_item_ref = item_fn
    connector._get_qbo_customer_ref = cust_fn
    return connector


BILL_CASES = {
    "billable_customer": (_bill_connector(_ok_item, _ok_cust), _line(), 1),
    "billable_billed": (_bill_connector(_ok_item, _ok_cust), _line(is_billed=True), 2),
    "non_billable": (_bill_connector(_ok_item, _ok_cust), _line(is_billable=False), 3),
    "billable_null_default": (_bill_connector(_ok_item, _ok_cust), _line(is_billable=None), 1),
    "billable_no_project": (_bill_connector(_ok_item, _ok_cust), _line(project_id=None), 1),
    "billable_project_no_customer": (_bill_connector(_ok_item, _no_cust), _line(), 1),
    "markup_percent": (_bill_connector(_ok_item, _ok_cust), _line(markup=Decimal("0.10")), 1),
    "no_amount_qty_rate": (_bill_connector(_ok_item, _ok_cust), _line(amount=None), 1),
    "no_amount_no_qty": (_bill_connector(_ok_item, _ok_cust), _line(amount=None, quantity=None, rate=None), 1),
    "unmapped_item": (_bill_connector(_unmapped, _ok_cust), _line(), 1),
    "no_scc": (_bill_connector(_ok_item, _ok_cust), _line(sub_cost_code_id=None), 1),
    "holding_item": (_bill_connector(_holding, _ok_cust), _line(), 1),
}


def _outcome(fn):
    try:
        return {"ok": fn().model_dump(by_alias=True, exclude_none=True)}
    except Exception as error:  # noqa: BLE001 - golden records the class and message
        return {"raises": type(error).__name__, "message": str(error)}


def _json_roundtrip(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


@pytest.mark.parametrize("case", sorted(BILL_CASES))
def test_bill_line_output_matches_pre_lift_golden(case):
    connector, item, line_num = BILL_CASES[case]
    actual = _outcome(lambda: connector._build_qbo_line(item, line_num, "realm-1"))
    assert _json_roundtrip(actual) == GOLDEN[case]


def test_golden_covers_every_bill_case():
    assert set(GOLDEN) == set(BILL_CASES)


def test_shared_helper_reproduces_bill_plan_fields():
    """The helper alone (no connector) yields the same billable/customer/amount decisions."""
    plan = _bill_plan(
        _line(), 1, "realm-1",
        resolve_item_ref=_ok_item, resolve_customer_ref=_ok_cust,
    )
    assert plan.billable_status == "Billable"
    assert plan.customer_ref is CUST
    assert plan.item_ref is ITEM
    assert plan.amount == Decimal("21.00")
    assert plan.qty == Decimal("2")
    assert plan.markup_info is None


def test_shared_helper_fail_loud_message_names_the_document_label():
    with pytest.raises(ValueError) as excinfo:
        _bill_plan(
            _line(), 1, "realm-1",
            resolve_item_ref=_ok_item, resolve_customer_ref=_no_cust,
            line_label="BillCreditLineItem", document_label="credit",
        )
    message = str(excinfo.value)
    assert "BillCreditLineItem 11" in message
    assert "before syncing this credit." in message


def test_shared_helper_defaults_keep_bill_wording():
    with pytest.raises(ValueError) as excinfo:
        _bill_plan(
            _line(), 1, "realm-1",
            resolve_item_ref=_ok_item, resolve_customer_ref=_no_cust,
        )
    message = str(excinfo.value)
    assert message.startswith("BillLineItem 11: billable line on project_id=9")
    assert "before syncing this bill." in message


def test_shared_helper_holding_code_propagates_unchanged():
    with pytest.raises(HoldingCodeRefused):
        _bill_plan(
            _line(), 1, "realm-1",
            resolve_item_ref=_holding, resolve_customer_ref=_ok_cust,
        )


def test_credit_line_built_from_a_real_bill_credit_line_item_uses_unit_price():
    """P0-1: BillCreditLineItem has unit_price and no rate/markup; the accessor must be explicit."""
    credit_line = BillCreditLineItem(
        id=71, public_id="bcli-71", row_version=None, created_datetime=None, modified_datetime=None,
        bill_credit_id=7, sub_cost_code_id=5, project_id=9, description="Lumber",
        quantity=Decimal("2"), unit_price=Decimal("10.50"), amount=Decimal("21.00"),
        is_billable=True, is_billed=None, billable_amount=None, is_draft=False,
    )
    plan = build_item_expense_line_plan(
        credit_line, 1, "realm-1",
        resolve_item_ref=_ok_item, resolve_customer_ref=_ok_cust,
        unit_price_of=lambda li: li.unit_price,
        markup_of=lambda li: None,
        line_label="BillCreditLineItem", document_label="credit",
    )
    assert plan.unit_price == Decimal("10.50")
    assert plan.amount == Decimal("21.00")
    assert plan.markup_info is None
