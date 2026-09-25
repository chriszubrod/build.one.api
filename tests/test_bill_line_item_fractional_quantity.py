"""BillLineItem.Quantity is DECIMAL end to end, not an int.

`dbo.BillLineItem.Quantity` has been `DECIMAL(18,4)` since the
`scripts/migrations/step2_decimal_quantity.sql` widening, and
`CreateBillLineItem` / `UpdateBillLineItemById` both declare
`@Quantity DECIMAL(18,4)`. The READ path already agreed: `_from_db` maps the
column through `Decimal(str(...))`. Only the WRITE path was left typed `int`,
and one of those `int`s was not inert:

    BillLineItemCreate(bill_public_id="b-1", quantity=Decimal("5.25"))
    -> ValidationError: Input should be a valid integer, got a number with a
       fractional part

so the API answered **422** to a fractional quantity and iOS/web could not
enter 5.25 hours at all. Measured on prod: zero of 22,479 `dbo.BillLineItem`
rows carry a fraction, while QBO holds 55 fractional `Qty` values.

Every fixture here uses **5.25** precisely because the wrong answer (5) and the
right answer (5.25) differ numerically — a quantity of 5 would pass either way
and prove nothing.

What does NOT change: a client sending `quantity: 5` is unaffected. The value
still lands as `5.0000` in a `DECIMAL(18,4)` column and still comes back out of
`_from_db` as `Decimal("5.0000")`, which FastAPI's encoder renders as `5.0` —
byte-identical before and after (pinned below).
"""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional, get_type_hints
from unittest.mock import MagicMock, patch

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from entities.bill_line_item.api.router import (
    create_bill_line_item_router,
    update_bill_line_item_by_public_id_router,
)
from entities.bill_line_item.api.schemas import BillLineItemCreate, BillLineItemUpdate
from entities.bill_line_item.business.model import BillLineItem
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from shared.authz import clear_authz_context, set_authz_context

_REPO = "entities.bill_line_item.persistence.repo"
_BLI_SERVICE = "entities.bill_line_item.business.service"
_BLI_ROUTER = "entities.bill_line_item.api.router"
_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}

FRACTIONAL = Decimal("5.25")
TRUNCATED = Decimal("5")  # what an int round-trip would have left behind


@pytest.fixture(autouse=True)
def _authz():
    set_authz_context(user_id=17, company_id=1, is_system_admin=False)
    yield
    clear_authz_context()


def _assert_exact_quantity(value, expected: Decimal):
    """The invariant: an exact Decimal, never int-truncated.

    Deliberately only three assertions. Two earlier ones were removed as
    tautologies — `not isinstance(value, float)` cannot fire once `isinstance(
    value, Decimal)` has passed (Decimal is not a float subclass), and
    `value != TRUNCATED or expected == TRUNCATED` is true in both branches once
    `value == expected` holds. They read as guarantees and checked nothing.
    """
    assert value is not None
    assert isinstance(value, Decimal)
    assert value == expected


# ---------------------------------------------------------------------------
# 1. The API boundary — the half of this defect that was NOT inert.
# ---------------------------------------------------------------------------


def test_create_schema_accepts_a_fractional_quantity():
    """RED before the fix: Pydantic raised `valid integer` on 5.25 -> HTTP 422."""
    body = BillLineItemCreate(bill_public_id="b-1", quantity=FRACTIONAL)
    _assert_exact_quantity(body.quantity, FRACTIONAL)


def test_update_schema_accepts_a_fractional_quantity():
    body = BillLineItemUpdate(row_version="rv", bill_public_id="b-1", quantity=FRACTIONAL)
    _assert_exact_quantity(body.quantity, FRACTIONAL)


@pytest.mark.parametrize("schema,extra", [
    (BillLineItemCreate, {}),
    (BillLineItemUpdate, {"row_version": "rv"}),
])
def test_schema_accepts_a_fractional_quantity_off_the_JSON_WIRE(schema, extra):
    """The shape FastAPI actually parses: a JSON number, not a Python Decimal.

    JSON has no decimal type, so 5.25 arrives as a float. Asserting on the
    Python-object constructor alone would leave the real request path untested.
    """
    import json

    body = schema.model_validate_json(
        json.dumps({"bill_public_id": "b-1", "quantity": 5.25, **extra})
    )
    _assert_exact_quantity(body.quantity, FRACTIONAL)


def test_schema_still_accepts_an_integer_quantity_unchanged():
    """Consumer-compat pin: `quantity: 5` keeps meaning 5. Green before AND after."""
    assert BillLineItemCreate(bill_public_id="b-1", quantity=5).quantity == 5
    assert (
        BillLineItemUpdate(row_version="rv", bill_public_id="b-1", quantity=5).quantity
        == 5
    )


def test_schema_still_rejects_a_non_numeric_quantity():
    """Widening int -> Decimal must not open the field to junk."""
    with pytest.raises(ValidationError):
        BillLineItemCreate(bill_public_id="b-1", quantity="not-a-number")


# ---------------------------------------------------------------------------
# 2. The persistence seam — what is actually bound to @Quantity DECIMAL(18,4).
# ---------------------------------------------------------------------------


@contextmanager
def _captured_proc_params(row=None):
    """Patch the repo's DB seam; yield the params dict handed to call_procedure."""
    params: dict = {}
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cursor
    with patch(f"{_REPO}.get_connection") as get_conn, patch(
        f"{_REPO}.call_procedure", side_effect=lambda **kw: params.update(kw["params"])
    ):
        get_conn.return_value.__enter__.return_value = conn
        yield params


def _returned_row(quantity="5.2500"):
    """A row shaped like what the sproc's OUTPUT clause hands back."""
    return SimpleNamespace(
        Id=1,
        PublicId="00000000-0000-0000-0000-000000000001",
        RowVersion=b"\x00" * 8,
        CreatedDatetime=None,
        ModifiedDatetime=None,
        BillId=55,
        SubCostCodeId=None,
        ProjectId=None,
        Description="d",
        Quantity=Decimal(quantity),
        Rate=None,
        Amount=None,
        IsBillable=True,
        IsBilled=False,
        Markup=None,
        Price=None,
        IsDraft=True,
        QboId=None,
        RealmId=None,
    )


def test_repo_create_binds_a_fractional_quantity_exactly():
    with _captured_proc_params(row=_returned_row()) as params:
        BillLineItemRepository().create(bill_id=55, quantity=FRACTIONAL)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_repo_update_binds_a_fractional_quantity_exactly():
    line = BillLineItem(
        id=1, public_id="bli-1", row_version=None, created_datetime=None,
        modified_datetime=None, bill_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=FRACTIONAL, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )
    with _captured_proc_params(row=_returned_row()) as params:
        BillLineItemRepository().update_by_id(line, allow_terminal_parent=True)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


@pytest.mark.parametrize("raw", [5.25, "5.25"])
def test_repo_create_never_hands_a_float_or_str_to_SQL(raw):
    """RED before the fix: `"Quantity": quantity` passed the raw value straight through.

    The same exact-decimal rule Rate/Amount/Markup/Price already hold in this
    dict — a bare float reaching pyodbc is binary-approximate money arithmetic.
    """
    with _captured_proc_params(row=_returned_row()) as params:
        BillLineItemRepository().create(bill_id=55, quantity=raw)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_repo_update_never_hands_a_float_to_SQL():
    line = BillLineItem(
        id=1, public_id="bli-1", row_version=None, created_datetime=None,
        modified_datetime=None, bill_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=5.25, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )
    with _captured_proc_params(row=_returned_row()) as params:
        BillLineItemRepository().update_by_id(line, allow_terminal_parent=True)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


@pytest.mark.parametrize("quantity", [None, Decimal("0")])
def test_repo_create_preserves_none_and_zero_quantity(quantity):
    """`Decimal(0)` is falsy — the coercion must be `is not None`, never truthy."""
    with _captured_proc_params(row=_returned_row()) as params:
        BillLineItemRepository().create(bill_id=55, quantity=quantity)
    assert params["Quantity"] == quantity
    if quantity is not None:
        assert isinstance(params["Quantity"], Decimal)


# ---------------------------------------------------------------------------
# 3. The service seam.
# ---------------------------------------------------------------------------


def _bill(status="draft"):
    return SimpleNamespace(id=55, public_id="bill-55", status=status, is_draft=True)


def _existing_line(quantity=TRUNCATED):
    return BillLineItem(
        id=1, public_id="bli-1", row_version="AAAA", created_datetime=None,
        modified_datetime=None, bill_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=quantity, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )


@contextmanager
def _bill_service_patched():
    """Both bindings of BillService the line-item service can reach.

    `create` uses the module-level import; `_assert_parent_editable` re-imports
    from the source module inside the function, so patching one name is not
    enough.
    """
    with patch(f"{_BLI_SERVICE}.BillService") as MockA, patch(
        "entities.bill.business.service.BillService"
    ) as MockB:
        for mock in (MockA, MockB):
            mock.return_value.read_by_public_id.return_value = _bill()
            mock.return_value.read_by_id.return_value = _bill()
        yield


def test_service_update_assigns_an_exact_decimal_quantity():
    """RED before the fix: `existing.quantity = quantity` stored the raw float."""
    svc = BillLineItemService(repo=MagicMock())
    existing = _existing_line()
    svc.read_by_public_id = MagicMock(return_value=existing)
    with _bill_service_patched():
        svc.update_by_public_id("bli-1", row_version="AAAA", quantity=5.25)
    written = svc.repo.update_by_id.call_args.args[0]
    _assert_exact_quantity(written.quantity, FRACTIONAL)


def test_service_update_omitting_quantity_preserves_the_stored_value():
    """Omit-semantics pin: `None` still means preserve, never clear-to-zero."""
    svc = BillLineItemService(repo=MagicMock())
    existing = _existing_line(quantity=FRACTIONAL)
    svc.read_by_public_id = MagicMock(return_value=existing)
    with _bill_service_patched():
        svc.update_by_public_id("bli-1", row_version="AAAA", description="x")
    _assert_exact_quantity(
        svc.repo.update_by_id.call_args.args[0].quantity, FRACTIONAL
    )


# ---------------------------------------------------------------------------
# 4. End to end: router body -> service -> the dict bound to the sproc.
# ---------------------------------------------------------------------------


def test_a_fractional_quantity_SURVIVES_CREATE_end_to_end():
    """5.25 in at the schema, 5.25 out at @Quantity — no int anywhere between."""
    body = BillLineItemCreate(bill_public_id="bill-55", quantity=FRACTIONAL)
    svc = BillLineItemService()
    with _bill_service_patched(), _captured_proc_params(row=_returned_row()) as params:
        svc.create(bill_public_id=body.bill_public_id, quantity=body.quantity)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_a_fractional_quantity_SURVIVES_UPDATE_end_to_end():
    body = BillLineItemUpdate(
        row_version="AAAA", bill_public_id="bill-55", quantity=FRACTIONAL
    )
    svc = BillLineItemService()
    svc.read_by_public_id = MagicMock(return_value=_existing_line())
    with _bill_service_patched(), _captured_proc_params(row=_returned_row()) as params:
        svc.update_by_public_id(
            "bli-1", row_version=body.row_version, quantity=body.quantity
        )
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_the_router_forwards_a_fractional_quantity_into_the_engine_payload():
    """ProcessEngine forwards `**context.payload` to the service, so this IS the handoff."""
    captured = {}

    def _capture(context):
        captured["ctx"] = context
        return {"success": True, "data": {"public_id": "x"}}

    body = BillLineItemCreate(bill_public_id="bill-55", quantity=FRACTIONAL)
    with patch(f"{_BLI_ROUTER}.ProcessEngine") as engine:
        engine.return_value.execute_synchronous.side_effect = _capture
        create_bill_line_item_router(body=body, current_user=_CURRENT_USER)
    _assert_exact_quantity(captured["ctx"].payload["quantity"], FRACTIONAL)

    update_body = BillLineItemUpdate(
        row_version="rv", bill_public_id="bill-55", quantity=FRACTIONAL
    )
    with patch(f"{_BLI_ROUTER}.ProcessEngine") as engine:
        engine.return_value.execute_synchronous.side_effect = _capture
        update_bill_line_item_by_public_id_router(
            public_id="bli-1", body=update_body, current_user=_CURRENT_USER
        )
    _assert_exact_quantity(captured["ctx"].payload["quantity"], FRACTIONAL)


# ---------------------------------------------------------------------------
# 5. The response side — proof this change is invisible to an existing consumer.
# ---------------------------------------------------------------------------


def test_an_integer_quantity_serialises_IDENTICALLY_before_and_after():
    """A client that sent `quantity: 5` still reads back exactly `5.0`.

    The read path was ALREADY decimal (`_from_db` line 51), so the JSON a
    consumer sees is unchanged by this unit. Pinned here because "the write
    path now sends a Decimal" is only safe if the wire value did not move.
    There is no custom response class or encoder on this app, so
    `jsonable_encoder` is what FastAPI actually applies to the returned dict.
    """
    row = _returned_row(quantity="5.0000")
    line = BillLineItemRepository()._from_db(row)
    assert jsonable_encoder(line.to_dict())["quantity"] == 5.0


def test_a_fractional_quantity_now_reaches_the_wire_as_5_25():
    row = _returned_row(quantity="5.2500")
    line = BillLineItemRepository()._from_db(row)
    assert jsonable_encoder(line.to_dict())["quantity"] == 5.25


# ---------------------------------------------------------------------------
# 6. The declared contract.
# ---------------------------------------------------------------------------


def test_the_model_declares_quantity_as_decimal_like_rate_and_amount():
    """An annotation pin, not a behavior pin — a dataclass annotation is inert
    at runtime. It is here because the `int` annotation is what invited callers
    to coerce, and it should go RED if anyone narrows it back."""
    hints = get_type_hints(BillLineItem)
    # Pin the TYPE, not equality with its siblings: narrowing all three back to
    # Optional[int] together would keep them equal and keep an equality-only
    # assertion green, catching nothing.
    assert hints["quantity"] == Optional[Decimal]
    assert hints["quantity"] == hints["rate"] == hints["amount"]


# ─── Precision bound (Codex P1, 2026-09-25) ───────────────────────────────────

@pytest.mark.parametrize("schema,extra", [
    (BillLineItemCreate, {}),
    (BillLineItemUpdate, {"row_version": "rv"}),
])
def test_quantity_is_bounded_to_the_column_precision(schema, extra):
    """The field is bounded to DECIMAL(18,4) — the column it lands in.

    Widening `int` -> `Decimal` removed the incidental ceiling the int type
    provided. Without an explicit bound the schema accepts values the column
    cannot hold: `0.00009` is silently rounded to 4dp by SQL Server (a QUIET
    wrong quantity), and a 15-digit value overflows DECIMAL(18,4) at the DB
    boundary instead of being rejected at the edge.

    The bound also caps the range where JSON float precision matters. FastAPI
    parses the body with `json.loads`, so a raw JSON number becomes a float
    before Pydantic sees it. Measured: every realistic quantity round-trips
    EXACTLY through that path — 5.25, 0.5, 2.1833, 5.016667 (sixths of an hour,
    the real shape in our own data), 0.1, 0.0001 — because `str(float)` emits
    the shortest round-tripping form. Loss begins only at 14+ integer digits,
    which `max_digits=18` now refuses outright.

    ⛔ `decimal_places=4` is deliberately NOT set, though the column is
    DECIMAL(18,4). A review asked for it to stop SQL silently rounding excess
    scale — but it REJECTS `5.016667`, and `qbo.BillLine.Qty` is DECIMAL(18,6)
    carrying exactly that: six live rows are sixths of an hour. Enforcing 4dp
    at the edge would be stricter than the system it guards and would 422 a
    value QuickBooks legitimately sends. SQL rounds those to 5.0167, a ~3.3e-5
    hour loss the column forces either way. Magnitude is bounded; scale is not.
    """
    from pydantic import ValidationError as _VE

    # in range, including the awkward real case
    for good in ("5.25", "0.5", "2.1833", "5.016667", "0.0001"):
        m = schema(**{"bill_public_id": "b-1", "quantity": Decimal(good), **extra})
        assert m.quantity == Decimal(good)

    # QBO's own 6dp shape must survive — this is why decimal_places is NOT set
    m = schema(**{"bill_public_id": "b-1", "quantity": Decimal("5.016667"), **extra})
    assert m.quantity == Decimal("5.016667")

    # the exact DECIMAL(18,4) ceiling is accepted
    m = schema(**{"bill_public_id": "b-1", "quantity": Decimal("99999999999999.9999"), **extra})
    assert m.quantity == Decimal("99999999999999.9999")

    # anything the column cannot hold is rejected AT THE EDGE, not at the DB.
    # `max_digits=18` alone does NOT do this — it counts TOTAL digits, so it
    # accepts 123456789012345 (15 integer digits), which overflows the column.
    # That was measured, and it is why `le`/`ge` are set to the real ceiling.
    for over in ("123456789012345", "1234567890123456.78", "123456789012345678"):
        with pytest.raises(_VE):
            schema(**{"bill_public_id": "b-1", "quantity": Decimal(over), **extra})

    # ⛔ max_digits carries its OWN weight and needs its own case. Every bound
    # above is large-magnitude, so `le` refuses it first — which silently
    # un-pinned `max_digits` when `le`/`ge` were added (06108842). These two are
    # small in magnitude and wide in digits: they sit INSIDE le/ge and are
    # refused only by the digit count. Delete max_digits and they go green.
    for many_digits in ("1.234567890123456789", "0.12345678901234567890"):
        with pytest.raises(_VE):
            schema(**{"bill_public_id": "b-1", "quantity": Decimal(many_digits), **extra})
