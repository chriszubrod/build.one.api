"""Router money coercion guards for zero amounts (U-196).

``Decimal(0)`` is falsy in Python. UPDATE paths that used truthy guards
(``if body.markup`` / ``if body.total_amount``) silently dropped genuine
$0.00 totals and 0% markups to ``None``. Downstream services
preserve-on-``None``, so the stale stored value was retained. Some routes
also used ``float()``, violating the exact-decimal rule.

These pure-logic tests patch ProcessEngine (or ``InvoiceService`` for the
invoice UPDATE path, which does not route through the workflow engine) at the
router module and assert on the production handoff: ``TriggerContext.payload``
or ``update_by_public_id`` kwargs. All money fields route through
``shared.api.money.to_decimal_or_none``.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.api.router import update_bill_credit_by_public_id_router
from entities.bill_credit.api.schemas import BillCreditUpdate
from entities.bill_credit_line_item.api.router import (
    update_bill_credit_line_item_by_public_id_router,
)
from entities.bill_credit_line_item.api.schemas import BillCreditLineItemUpdate
from entities.bill_line_item.api.router import (
    create_bill_line_item_router,
    update_bill_line_item_by_public_id_router,
)
from entities.bill_line_item.api.schemas import BillLineItemCreate, BillLineItemUpdate
from entities.invoice.api.router import update_invoice_by_public_id_router
from entities.invoice.api.schemas import InvoiceUpdate

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}


def _assert_money(value, expected: str):
    """The invariant every fixed surface must hold: an exact Decimal, never dropped, never a float.

    ``isinstance(value, Decimal)`` already excludes ``float`` (the two cannot share a layout), but
    the float assertion is kept explicit because deleting the ``float()`` calls is half of what
    this unit fixed and a reader should see that pinned.
    """
    assert value is not None
    assert isinstance(value, Decimal)
    assert not isinstance(value, float)
    assert value == Decimal(expected)


def _engine_payload(router_module: str, call) -> dict:
    """Run ``call`` with ProcessEngine patched at ``router_module``; return the built payload.

    ``ProcessEngine.execute_synchronous`` forwards ``**context.payload`` straight to the service
    layer, so the captured payload IS the production handoff — not a mock artifact.
    """
    captured = {}

    def _capture(context):
        captured["ctx"] = context
        return {"success": True, "data": {"public_id": "x"}}

    with patch(f"{router_module}.ProcessEngine") as mock_engine:
        mock_engine.return_value.execute_synchronous.side_effect = _capture
        call()
    return captured["ctx"].payload


_BLI_ROUTER = "entities.bill_line_item.api.router"


def _update_bill_line_item_payload(**overrides) -> dict:
    body = BillLineItemUpdate(row_version="rv", bill_public_id="b-1", **overrides)
    return _engine_payload(
        _BLI_ROUTER,
        lambda: update_bill_line_item_by_public_id_router(
            public_id="bli-x", body=body, current_user=_CURRENT_USER
        ),
    )


def _create_bill_line_item_payload(**overrides) -> dict:
    body = BillLineItemCreate(bill_public_id="b-1", **overrides)
    return _engine_payload(
        _BLI_ROUTER,
        lambda: create_bill_line_item_router(body=body, current_user=_CURRENT_USER),
    )


@pytest.mark.parametrize("field", ["markup", "rate", "amount", "price"])
def test_bill_line_item_update_zero_money_reaches_payload(field):
    """THE guard: reverting any of the four UPDATE coercions to a truthy check makes this RED.

    ``markup = 0`` ("no markup") is a routine value — dropping it keeps the old markup and the
    client price stays inflated.
    """
    payload = _update_bill_line_item_payload(**{field: Decimal("0")})
    _assert_money(payload[field], "0")


def test_bill_line_item_update_omitted_money_fields_stay_none():
    """Omit-semantics pin: absent still means preserve-on-None."""
    payload = _update_bill_line_item_payload()
    for field in ("rate", "amount", "markup", "price"):
        assert payload[field] is None


def test_bill_line_item_create_zero_markup_reaches_payload():
    """Parity pin on the already-correct CREATE path."""
    _assert_money(_create_bill_line_item_payload(markup=Decimal("0"))["markup"], "0")


def _update_bill_credit_payload(**overrides) -> dict:
    body = BillCreditUpdate(
        row_version="rv",
        vendor_public_id="v-1",
        credit_date="2026-08-02",
        credit_number="VC-1",
        **overrides,
    )
    return _engine_payload(
        "entities.bill_credit.api.router",
        lambda: update_bill_credit_by_public_id_router(
            public_id="bc-x", body=body, current_user=_CURRENT_USER
        ),
    )


@pytest.mark.parametrize("amount", ["0", "1234.56"])
def test_bill_credit_update_total_amount_is_exact_decimal(amount):
    """Pins both halves of the U-197 defect: the zero-drop and the ``float()`` violation.

    The non-zero case is not redundant — ``float(Decimal("1234.56"))`` compares unequal to the
    exact Decimal, so it catches the float() reintroduction even where zero is not involved.
    """
    _assert_money(_update_bill_credit_payload(total_amount=Decimal(amount))["total_amount"], amount)


def _update_invoice_kwargs(**overrides) -> dict:
    """The invoice UPDATE path calls InvoiceService directly — no ProcessEngine to patch.

    The mock must return a truthy object with ``.to_dict()`` or the router raises not-found.
    """
    captured = {}
    invoice = MagicMock()
    invoice.to_dict.return_value = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return invoice

    body = InvoiceUpdate(
        row_version="rv",
        project_public_id="p-1",
        invoice_date="2026-08-02",
        due_date="2026-08-02",
        invoice_number="INV-1",
        **overrides,
    )
    with patch("entities.invoice.api.router.InvoiceService") as mock_service_cls:
        mock_service_cls.return_value.update_by_public_id.side_effect = _capture
        update_invoice_by_public_id_router(
            public_id="inv-x", body=body, current_user=_CURRENT_USER
        )
    return captured


def test_invoice_update_zero_total_amount_is_exact_decimal():
    _assert_money(_update_invoice_kwargs(total_amount=Decimal("0"))["total_amount"], "0")


def test_invoice_update_omitted_total_amount_is_none():
    assert _update_invoice_kwargs()["total_amount"] is None


@pytest.mark.parametrize("field", ["amount", "billable_amount"])
def test_bill_credit_line_item_update_zero_money_is_exact_decimal(field):
    """Pins the float() deletions on this router (no zero-drop there, but the same rule)."""
    body = BillCreditLineItemUpdate(
        row_version="rv", bill_credit_public_id="bc-1", **{field: Decimal("0")}
    )
    payload = _engine_payload(
        "entities.bill_credit_line_item.api.router",
        lambda: update_bill_credit_line_item_by_public_id_router(
            public_id="bcli-x", body=body, current_user=_CURRENT_USER
        ),
    )
    _assert_money(payload[field], "0")
