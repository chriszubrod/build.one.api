"""Bill router Decimal payload guards for zero amounts.

Decimal(0) is falsy in Python, so a truthy guard (``if body.total_amount``)
silently drops a genuine $0.00 from the TriggerContext payload. Downstream
``service.py`` (~1030) preserves-on-None and ``repo.py`` (~430) re-passes the
STALE stored value, which ``dbo.bill.sql`` (~310) then unconditionally writes
back — so the $0 is discarded and the prior total is retained (not SQL NULL
unless the prior value was already NULL).

These are pure-logic tests: they patch ProcessEngine at the router module and
assert on the real TriggerContext the router built. That is the production
handoff contract — ``ProcessEngine.execute_synchronous`` forwards
``**context.payload`` straight through to the service layer.
"""

import asyncio
from decimal import Decimal
from unittest.mock import patch

from entities.bill.api.router import create_bill_router, update_bill_by_public_id_router
from entities.bill.api.schemas import BillCreate, BillUpdate

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}


def _make_capture():
    """Return (captured, side_effect) — the side effect records the context."""
    captured = {}

    def _capture(context):
        captured["ctx"] = context
        return {"success": True, "data": {"public_id": "bill-x"}}

    return captured, _capture


def _update_bill_and_capture(**overrides) -> dict:
    """Drive the bill UPDATE router and return the payload it built."""
    captured, _capture = _make_capture()

    with patch("entities.bill.api.router.ProcessEngine") as mock_engine_cls:
        mock_engine_cls.return_value.execute_synchronous.side_effect = _capture
        body = BillUpdate(
            row_version="rv",
            vendor_public_id="v-1",
            bill_date="2026-08-02",
            due_date="2026-08-02",
            bill_number="INV-1",
            **overrides,
        )
        asyncio.run(
            update_bill_by_public_id_router(
                public_id="bill-x",
                body=body,
                current_user=_CURRENT_USER,
            )
        )
    return captured["ctx"].payload


def _create_bill_and_capture(**overrides) -> dict:
    """Drive the bill CREATE router and return the payload it built."""
    captured, _capture = _make_capture()

    with patch("entities.bill.api.router.ProcessEngine") as mock_engine_cls, patch(
        "entities.bill.api.router._run_complete_bill"
    ), patch("entities.bill.api.router.resolve_user_id", return_value=17):
        mock_engine_cls.return_value.execute_synchronous.side_effect = _capture
        body = BillCreate(
            vendor_public_id="v-1",
            bill_date="2026-08-02",
            due_date="2026-08-02",
            bill_number="INV-1",
            attachment_public_id="a-1",
            **overrides,
        )
        asyncio.run(
            create_bill_router(
                body=body,
                current_user=_CURRENT_USER,
            )
        )
    return captured["ctx"].payload


def test_update_zero_total_amount_reaches_payload_as_decimal_zero():
    """THE guard: a genuine $0.00 must survive the UPDATE coercion.

    Reverting router.py's `is not None` to a truthy check makes this RED.
    """
    payload = _update_bill_and_capture(total_amount=Decimal("0"))
    assert payload["total_amount"] is not None
    assert isinstance(payload["total_amount"], Decimal)
    assert payload["total_amount"] == Decimal("0")


def test_update_omitted_total_amount_stays_none():
    """Omit-semantics unchanged: absent total still means preserve-on-None."""
    payload = _update_bill_and_capture()
    assert payload["total_amount"] is None


def test_update_nonzero_total_amount_passes_through():
    payload = _update_bill_and_capture(total_amount=Decimal("4500.00"))
    assert payload["total_amount"] == Decimal("4500.00")


def test_create_zero_total_amount_reaches_payload_as_decimal_zero():
    """Parity pin on the already-correct CREATE path."""
    payload = _create_bill_and_capture(total_amount=Decimal("0"))
    assert payload["total_amount"] is not None
    assert isinstance(payload["total_amount"], Decimal)
    assert payload["total_amount"] == Decimal("0")


def test_create_zero_line_markup_reaches_payload_as_decimal_zero():
    """Pins the line_* family — markup 0 ("no markup") is a routine value."""
    payload = _create_bill_and_capture(
        total_amount=Decimal("0"),
        line_markup=Decimal("0"),
    )
    assert payload["line_markup"] is not None
    assert isinstance(payload["line_markup"], Decimal)
    assert payload["line_markup"] == Decimal("0")
