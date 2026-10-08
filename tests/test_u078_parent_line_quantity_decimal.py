"""The three doors the U-503d/e quantity widening did not reach (U-078).
The line-item layers were already Decimal (test_*_line_item_fractional_quantity.py):

  1. The one-shot parent create paths: `BillCreate.line_quantity` /
     `ExpenseCreate.line_quantity` and the matching `BillService.create` /
     `ExpenseService.create` signatures. A fractional inline quantity 422'd
     before the fixed line-item create was ever reached.
  2. The agent tool surfaces (`entities/{bill,expense}/intelligence/tools.py`).
     They POST to the widened endpoints, so an agent could not write 2.5.
     The tools are a JSON consumer, so they take float (matching their rate and
     amount siblings), not Decimal.
  3. A truthy guard in the QBO bill push that turned Decimal("0") into None.

Every fixture uses 2.5 / 5.25 rather than 5, because a quantity of 5 would pass
both before and after the fix and prove nothing.
"""

import json
from decimal import Decimal
from typing import Optional, get_type_hints
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from entities.bill.api.schemas import BillCreate
from entities.bill.business.service import BillService
from entities.bill.intelligence.tools import (
    CreateBillArgs,
    UpdateBillLineItemArgs,
    _BillLineItemSpec,
)
from entities.expense.api.schemas import ExpenseCreate
from entities.expense.business.service import ExpenseService
from entities.expense.intelligence.tools import (
    CreateExpenseArgs,
    UpdateExpenseLineItemArgs,
    _ExpenseLineItemSpec,
)
from integrations.intuit.qbo.bill.connector.bill.business.service import (
    BillBillConnector,
)
from integrations.intuit.qbo.bill.external.schemas import QboReferenceType

FRACTIONAL = Decimal("5.25")
SUB_CODE_QTY = 2.5


def _required_placeholders(model) -> dict:
    return {name: "x" for name, f in model.model_fields.items() if f.is_required()}


# ---------------------------------------------------------------------------
# 1. The parent create schemas — the inline summary line.
# ---------------------------------------------------------------------------


BILL_KW = dict(vendor_public_id="v-1", bill_date="2026-10-01", due_date="2026-11-01",
               bill_number="B-1", attachment_public_id="a-1")
EXPENSE_KW = dict(vendor_public_id="v-1", expense_date="2026-10-01", reference_number="R-1",
                  attachment_public_id="a-1")
PARENTS = [pytest.param(BillCreate, BILL_KW, id="BillCreate"),
           pytest.param(ExpenseCreate, EXPENSE_KW, id="ExpenseCreate")]


@pytest.mark.parametrize("schema,kw", PARENTS)
def test_parent_create_accepts_a_fractional_line_quantity(schema, kw):
    """RED before the fix: `line_quantity: Optional[int]` 422'd 5.25."""
    body = schema(**kw, line_quantity=FRACTIONAL)
    assert body.line_quantity == FRACTIONAL
    assert isinstance(body.line_quantity, Decimal)


@pytest.mark.parametrize("schema,kw", PARENTS)
def test_parent_create_accepts_a_fractional_line_quantity_off_the_JSON_WIRE(schema, kw):
    """FastAPI parses a JSON number, so 5.25 arrives as a float, not a Decimal."""
    body = schema.model_validate_json(json.dumps({**kw, "line_quantity": 5.25}))
    assert body.line_quantity == FRACTIONAL


@pytest.mark.parametrize("schema,kw", PARENTS)
def test_parent_create_integer_line_quantity_still_validates(schema, kw):
    """Consumer-compat pin: an integer client is unaffected. Green before AND after.

    `jsonable_encoder` renders a whole-number Decimal as an int (exponent >= 0),
    so `5` still goes out as `5`. Pinned on the JSON text, because `5 == 5.0`
    in Python and would not catch a change in rendering.
    """
    body = schema(**kw, line_quantity=5)
    assert body.line_quantity == 5
    assert json.dumps(jsonable_encoder(body.line_quantity)) == "5"


# ---------------------------------------------------------------------------
# 2. The parent service signatures.
# ---------------------------------------------------------------------------


def test_bill_service_create_declares_line_quantity_as_optional_decimal():
    """Annotation pin: a bare `int` here is what let callers coerce the value."""
    assert get_type_hints(BillService.create)["line_quantity"] == Optional[Decimal]


def test_expense_service_create_declares_line_quantity_as_optional_decimal():
    assert get_type_hints(ExpenseService.create)["line_quantity"] == Optional[Decimal]


# ---------------------------------------------------------------------------
# 3. The agent tool surfaces. They POST JSON, so float, like rate and amount.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,field",
    [
        (CreateBillArgs, "line_quantity"),
        (_BillLineItemSpec, "quantity"),
        (UpdateBillLineItemArgs, "quantity"),
        (CreateExpenseArgs, "line_quantity"),
        (_ExpenseLineItemSpec, "quantity"),
        (UpdateExpenseLineItemArgs, "quantity"),
    ],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_agent_tool_model_accepts_a_fractional_quantity(model, field):
    """RED before the fix: the agent could not write a fractional quantity.

    Pydantic v2 refused 2.5 for an int field rather than truncating it, so this
    was a capability gap, not data loss.
    """
    args = {**_required_placeholders(model), field: SUB_CODE_QTY}
    parsed = model(**args)
    assert getattr(parsed, field) == SUB_CODE_QTY


# ---------------------------------------------------------------------------
# 4. The QBO bill push guard.
# ---------------------------------------------------------------------------


def _qbo_line_item(quantity):
    return SimpleNamespace(
        id=1, sub_cost_code_id=7, project_id=None, description="d",
        quantity=quantity, rate=None, amount=Decimal("0"), markup=None,
        is_billable=True, is_billed=False,
    )


@pytest.mark.parametrize("quantity", [None, Decimal("5.25"), Decimal("0")])
def test_qbo_bill_line_carries_the_quantity_through_the_builder(quantity):
    """Exercises the real `_build_qbo_line` with only its two DB-backed lookups stubbed.

    The truthy guard `if line_item.quantity` turned Decimal("0") into None. That
    is metadata only, since `amount` is sent explicitly, so the fix is about
    a legitimate zero not being silently dropped, not about money.
    """
    connector = BillBillConnector.__new__(BillBillConnector)
    item_ref = QboReferenceType(value="1", name="Item")
    with patch.object(connector, "_get_qbo_item_ref", return_value=item_ref), patch.object(
        connector, "_get_qbo_customer_ref", return_value=None
    ):
        line = connector._build_qbo_line(_qbo_line_item(quantity), line_num=1, realm_id="r")
    qty = line.item_based_expense_line_detail.qty
    assert qty == quantity
    if quantity is not None:
        assert isinstance(qty, Decimal)
