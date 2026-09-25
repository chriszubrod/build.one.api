"""ExpenseLineItem.Quantity is DECIMAL end to end, not an int.

The structural twin of `tests/test_bill_line_item_fractional_quantity.py`
(U-503d, commits 34a4ccfa + 06108842). `dbo.ExpenseLineItem.Quantity` is
`DECIMAL(18,4)` — verified live against `sys.columns` (precision 18, scale 4),
not just read off the checked-in DDL — and `CreateExpenseLineItem` /
`UpdateExpenseLineItemById` both declare `@Quantity DECIMAL(18,4)`
(entities/expense_line_item/sql/dbo.expense_line_item.sql:130 and :354). The
READ path already agreed: `_from_db` (repo.py:49) maps the column through
`Decimal(str(...))`, so the `int` annotation was already a lie at runtime.
Only the WRITE path was typed `int`, and one of those `int`s was not inert:

    ExpenseLineItemCreate(expense_public_id="e-1", quantity=Decimal("5.25"))
    -> ValidationError: Input should be a valid integer, got a number with a
       fractional part

so the API answered **422** to a fractional quantity.

The expense twin was WEAKER than bill was in two places bill never had:
`ExpenseLineItemService.update_by_public_id` assigned `existing.quantity =
quantity` bare — no `Decimal(str(...))` coercion at all, unlike its own
Rate/Amount/Markup/Price siblings two lines below — and both repo binds passed
the raw value straight to pyodbc.

Every fixture here uses **5.25** precisely because the wrong answer (5) and the
right answer (5.25) differ numerically — a quantity of 5 would pass either way
and prove nothing.

Live blast radius, measured read-only 2026-09-25 before the change:
`dbo.ExpenseLineItem` holds 12,634 rows, 5,691 with a non-NULL Quantity, and
**zero** carry a fraction (min == max == 1.0000, zero negative). The QBO-side
twin `qbo.PurchaseLine` (Qty DECIMAL(18,6), 13,212 rows / 5,310 non-NULL) also
holds **zero** fractional and zero >4dp values, range 0.000000–1.000000. So
unlike the bill side — where QBO held 55 fractional Qty values against zero
locally — nothing was truncated here and no data backfill is owed. This is a
forward-looking fix: it unblocks a fractional quantity, it does not repair one.

What does NOT change: a client sending `quantity: 5` is unaffected. These
routes declare no `response_model` and the app is a bare `FastAPI()` with no
custom response class or encoder (app.py:149), so `jsonable_encoder` is what
FastAPI actually applies; the read path was already Decimal, and
`jsonable_encoder(Decimal("5.0000"))` is `5.0` before and after (pinned below).
"""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional, get_type_hints
from unittest.mock import MagicMock, patch

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from entities.expense_line_item.api.router import (
    create_expense_line_item_router,
    update_expense_line_item_by_public_id_router,
)
from entities.expense_line_item.api.schemas import (
    ExpenseLineItemCreate,
    ExpenseLineItemUpdate,
)
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from shared.authz import clear_authz_context, set_authz_context

_REPO = "entities.expense_line_item.persistence.repo"
_ELI_SERVICE = "entities.expense_line_item.business.service"
_ELI_ROUTER = "entities.expense_line_item.api.router"
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

    Deliberately only three assertions — `not isinstance(value, float)` cannot
    fire once `isinstance(value, Decimal)` has passed (Decimal is not a float
    subclass), so it would read as a guarantee and check nothing. See 06108842.
    """
    assert value is not None
    assert isinstance(value, Decimal)
    assert value == expected


# ---------------------------------------------------------------------------
# 1. The API boundary — the half of this defect that was NOT inert.
# ---------------------------------------------------------------------------


def test_create_schema_accepts_a_fractional_quantity():
    """RED before the fix: Pydantic raised `valid integer` on 5.25 -> HTTP 422."""
    body = ExpenseLineItemCreate(expense_public_id="e-1", quantity=FRACTIONAL)
    _assert_exact_quantity(body.quantity, FRACTIONAL)


def test_update_schema_accepts_a_fractional_quantity():
    body = ExpenseLineItemUpdate(
        row_version="rv", expense_public_id="e-1", quantity=FRACTIONAL
    )
    _assert_exact_quantity(body.quantity, FRACTIONAL)


@pytest.mark.parametrize("schema,extra", [
    (ExpenseLineItemCreate, {}),
    (ExpenseLineItemUpdate, {"row_version": "rv"}),
])
def test_schema_accepts_a_fractional_quantity_off_the_JSON_WIRE(schema, extra):
    """The shape FastAPI actually parses: a JSON number, not a Python Decimal.

    JSON has no decimal type, so 5.25 arrives as a float. Asserting on the
    Python-object constructor alone would leave the real request path untested.
    """
    import json

    body = schema.model_validate_json(
        json.dumps({"expense_public_id": "e-1", "quantity": 5.25, **extra})
    )
    _assert_exact_quantity(body.quantity, FRACTIONAL)


def test_schema_still_accepts_an_integer_quantity_unchanged():
    """Consumer-compat pin: `quantity: 5` keeps meaning 5. Green before AND after."""
    assert ExpenseLineItemCreate(expense_public_id="e-1", quantity=5).quantity == 5
    assert (
        ExpenseLineItemUpdate(
            row_version="rv", expense_public_id="e-1", quantity=5
        ).quantity
        == 5
    )


def test_schema_still_rejects_a_non_numeric_quantity():
    """Widening int -> Decimal must not open the field to junk."""
    with pytest.raises(ValidationError):
        ExpenseLineItemCreate(expense_public_id="e-1", quantity="not-a-number")


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
        ExpenseId=55,
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
        ExpenseLineItemRepository().create(expense_id=55, quantity=FRACTIONAL)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_repo_update_binds_a_fractional_quantity_exactly():
    line = ExpenseLineItem(
        id=1, public_id="eli-1", row_version=None, created_datetime=None,
        modified_datetime=None, expense_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=FRACTIONAL, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )
    with _captured_proc_params(row=_returned_row()) as params:
        ExpenseLineItemRepository().update_by_id(line, allow_terminal_parent=True)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


@pytest.mark.parametrize("raw", [5.25, "5.25"])
def test_repo_create_never_hands_a_float_or_str_to_SQL(raw):
    """RED before the fix: `"Quantity": quantity` passed the raw value straight through.

    The same exact-decimal rule Rate/Amount/Markup/Price already hold in this
    very dict — a bare float reaching pyodbc is binary-approximate arithmetic
    against a DECIMAL column.
    """
    with _captured_proc_params(row=_returned_row()) as params:
        ExpenseLineItemRepository().create(expense_id=55, quantity=raw)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_repo_update_never_hands_a_float_to_SQL():
    line = ExpenseLineItem(
        id=1, public_id="eli-1", row_version=None, created_datetime=None,
        modified_datetime=None, expense_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=5.25, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )
    with _captured_proc_params(row=_returned_row()) as params:
        ExpenseLineItemRepository().update_by_id(line, allow_terminal_parent=True)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


@pytest.mark.parametrize("quantity", [None, Decimal("0")])
def test_repo_create_preserves_none_and_zero_quantity(quantity):
    """`Decimal(0)` is falsy — the coercion must be `is not None`, never truthy."""
    with _captured_proc_params(row=_returned_row()) as params:
        ExpenseLineItemRepository().create(expense_id=55, quantity=quantity)
    assert params["Quantity"] == quantity
    if quantity is not None:
        assert isinstance(params["Quantity"], Decimal)


def test_repo_update_preserves_a_zero_quantity():
    """The update bind has the same falsy-zero trap as create. `qbo.PurchaseLine`
    holds live Qty values of exactly 0, so this is a real shape, not a hypothetical."""
    line = ExpenseLineItem(
        id=1, public_id="eli-1", row_version=None, created_datetime=None,
        modified_datetime=None, expense_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=Decimal("0"), rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )
    with _captured_proc_params(row=_returned_row()) as params:
        ExpenseLineItemRepository().update_by_id(line, allow_terminal_parent=True)
    assert params["Quantity"] == Decimal("0")
    assert isinstance(params["Quantity"], Decimal)


# ---------------------------------------------------------------------------
# 3. The service seam — the site the expense twin was WEAKER than bill.
# ---------------------------------------------------------------------------


def _expense(status="draft"):
    return SimpleNamespace(id=55, public_id="expense-55", status=status, is_draft=True)


def _existing_line(quantity=TRUNCATED):
    return ExpenseLineItem(
        id=1, public_id="eli-1", row_version="AAAA", created_datetime=None,
        modified_datetime=None, expense_id=55, sub_cost_code_id=None, project_id=None,
        description="d", quantity=quantity, rate=None, amount=None,
        is_billable=True, is_billed=False, markup=None, price=None, is_draft=True,
    )


@contextmanager
def _expense_service_patched():
    """Both bindings of ExpenseService the line-item service can reach.

    `create` uses the module-level import; `_assert_parent_editable` re-imports
    from the source module inside the function, so patching one name is not
    enough.
    """
    with patch(f"{_ELI_SERVICE}.ExpenseService") as MockA, patch(
        "entities.expense.business.service.ExpenseService"
    ) as MockB:
        for mock in (MockA, MockB):
            mock.return_value.read_by_public_id.return_value = _expense()
            mock.return_value.read_by_id.return_value = _expense()
        yield


def test_service_update_assigns_an_exact_decimal_quantity():
    """RED before the fix: `existing.quantity = quantity` stored the raw float.

    This is the site the bill twin had already coerced and expense had not —
    a bare assignment sitting directly above four `Decimal(str(...))` siblings.
    """
    svc = ExpenseLineItemService(repo=MagicMock())
    existing = _existing_line()
    svc.read_by_public_id = MagicMock(return_value=existing)
    with _expense_service_patched():
        svc.update_by_public_id("eli-1", row_version="AAAA", quantity=5.25)
    written = svc.repo.update_by_id.call_args.args[0]
    _assert_exact_quantity(written.quantity, FRACTIONAL)


def test_service_update_omitting_quantity_preserves_the_stored_value():
    """Omit-semantics pin: `None` still means preserve, never clear-to-zero.

    Load-bearing for the QBO pull: `preserve_stored_value` in
    integrations/intuit/qbo/purchase/connector/expense_line_item/business/
    service.py returns None as the explicit "leave it alone" sentinel.
    """
    svc = ExpenseLineItemService(repo=MagicMock())
    existing = _existing_line(quantity=FRACTIONAL)
    svc.read_by_public_id = MagicMock(return_value=existing)
    with _expense_service_patched():
        svc.update_by_public_id("eli-1", row_version="AAAA", description="x")
    _assert_exact_quantity(
        svc.repo.update_by_id.call_args.args[0].quantity, FRACTIONAL
    )


def test_service_update_preserves_a_zero_quantity():
    """`Decimal(0)` is falsy: the new coercion must be gated on `is not None`."""
    svc = ExpenseLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=_existing_line())
    with _expense_service_patched():
        svc.update_by_public_id("eli-1", row_version="AAAA", quantity=Decimal("0"))
    written = svc.repo.update_by_id.call_args.args[0]
    assert written.quantity == Decimal("0")
    assert isinstance(written.quantity, Decimal)


# ---------------------------------------------------------------------------
# 4. End to end: router body -> service -> the dict bound to the sproc.
# ---------------------------------------------------------------------------


def test_a_fractional_quantity_SURVIVES_CREATE_end_to_end():
    """5.25 in at the schema, 5.25 out at @Quantity — no int anywhere between."""
    body = ExpenseLineItemCreate(expense_public_id="expense-55", quantity=FRACTIONAL)
    svc = ExpenseLineItemService()
    with _expense_service_patched(), _captured_proc_params(
        row=_returned_row()
    ) as params:
        svc.create(expense_public_id=body.expense_public_id, quantity=body.quantity)
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_a_fractional_quantity_SURVIVES_UPDATE_end_to_end():
    body = ExpenseLineItemUpdate(
        row_version="AAAA", expense_public_id="expense-55", quantity=FRACTIONAL
    )
    svc = ExpenseLineItemService()
    svc.read_by_public_id = MagicMock(return_value=_existing_line())
    with _expense_service_patched(), _captured_proc_params(
        row=_returned_row()
    ) as params:
        svc.update_by_public_id(
            "eli-1", row_version=body.row_version, quantity=body.quantity
        )
    _assert_exact_quantity(params["Quantity"], FRACTIONAL)


def test_the_router_forwards_a_fractional_quantity_into_the_engine_payload():
    """ProcessEngine forwards `**context.payload` to the service, so this IS the handoff."""
    captured = {}

    def _capture(context):
        captured["ctx"] = context
        return {"success": True, "data": {"public_id": "x"}}

    body = ExpenseLineItemCreate(expense_public_id="expense-55", quantity=FRACTIONAL)
    with patch(f"{_ELI_ROUTER}.ProcessEngine") as engine:
        engine.return_value.execute_synchronous.side_effect = _capture
        create_expense_line_item_router(body=body, current_user=_CURRENT_USER)
    _assert_exact_quantity(captured["ctx"].payload["quantity"], FRACTIONAL)

    update_body = ExpenseLineItemUpdate(
        row_version="rv", expense_public_id="expense-55", quantity=FRACTIONAL
    )
    with patch(f"{_ELI_ROUTER}.ProcessEngine") as engine:
        engine.return_value.execute_synchronous.side_effect = _capture
        update_expense_line_item_by_public_id_router(
            public_id="eli-1", body=update_body, current_user=_CURRENT_USER
        )
    _assert_exact_quantity(captured["ctx"].payload["quantity"], FRACTIONAL)


# ---------------------------------------------------------------------------
# 5. The response side — proof this change is invisible to an existing consumer.
# ---------------------------------------------------------------------------


def test_an_integer_quantity_serialises_IDENTICALLY_before_and_after():
    """A client that sent `quantity: 5` still reads back exactly `5.0`.

    The read path was ALREADY decimal (`_from_db` repo.py:49), so the JSON a
    consumer sees is unchanged by this unit. Pinned here because "the write
    path now sends a Decimal" is only safe if the wire value did not move.
    Verified for THIS entity, not inherited from the bill twin: neither
    expense_line_item route declares a `response_model`, and the app is a bare
    `FastAPI()` with no custom response class, so `jsonable_encoder` is what
    FastAPI actually applies to the returned dict.
    """
    row = _returned_row(quantity="5.0000")
    line = ExpenseLineItemRepository()._from_db(row)
    encoded = jsonable_encoder(line.to_dict())["quantity"]
    assert encoded == 5.0
    # ⛔ `== 5.0` ALONE IS VACUOUS and cannot detect the regression this test is
    # named for: jsonable_encoder maps Decimal("5.0000") -> 5.0, int 5 -> 5, and
    # float 5.0 -> 5.0, and all three compare equal to 5.0. Reverting _from_db to
    # int(...) would keep it green. The JSON TEXT is what discriminates —
    # Decimal and float emit "5.0", an int emits "5".
    import json as _json
    assert _json.dumps(encoded) == "5.0"


def test_a_fractional_quantity_now_reaches_the_wire_as_5_25():
    row = _returned_row(quantity="5.2500")
    line = ExpenseLineItemRepository()._from_db(row)
    assert jsonable_encoder(line.to_dict())["quantity"] == 5.25


# ---------------------------------------------------------------------------
# 6. The declared contract.
# ---------------------------------------------------------------------------


def test_the_model_declares_quantity_as_decimal_like_rate_and_amount():
    """An annotation pin, not a behavior pin — a dataclass annotation is inert
    at runtime. It is here because the `int` annotation is what invited callers
    to coerce, and it should go RED if anyone narrows it back."""
    hints = get_type_hints(ExpenseLineItem)
    # Pin the TYPE, not equality with its siblings: narrowing all three back to
    # Optional[int] together would keep them equal and keep an equality-only
    # assertion green, catching nothing.
    assert hints["quantity"] == Optional[Decimal]
    assert hints["quantity"] == hints["rate"] == hints["amount"]


# ---------------------------------------------------------------------------
# 7. Precision bound.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema,extra", [
    (ExpenseLineItemCreate, {}),
    (ExpenseLineItemUpdate, {"row_version": "rv"}),
])
def test_quantity_is_bounded_to_the_column_precision(schema, extra):
    """The field is bounded to DECIMAL(18,4) — the column it lands in.

    Widening `int` -> `Decimal` removed the incidental ceiling the int type
    provided, so an explicit bound has to replace it.

    `max_digits=18` alone is NOT that bound: it counts TOTAL digits, so it
    accepts 123456789012345 (15 integer digits), which the column cannot hold —
    the real ceiling is 99999999999999.9999. `le`/`ge` pin that exact ceiling,
    so an over-wide value is refused at the edge rather than overflowing at the
    DB boundary.

    ⛔ `decimal_places=4` is deliberately NOT set, though the column is
    DECIMAL(18,4). It would REJECT `5.016667`, and the QBO staging column this
    entity is pulled from — `qbo.PurchaseLine.Qty`, verified DECIMAL(18,6) —
    can legitimately carry six decimal places; the pull hands that value
    straight to `ExpenseLineItemService.update_by_public_id`. Enforcing 4dp at
    the edge would be stricter than the system it guards. SQL rounds excess
    scale to 4dp either way. Magnitude is bounded; scale is not.

    (Measured on the expense side specifically: `qbo.PurchaseLine` currently
    holds ZERO >4dp values, unlike `qbo.BillLine`. The symmetry argument is the
    column TYPE, not today's sample — a 22k-row snapshot is not a contract.)
    """
    from pydantic import ValidationError as _VE

    # in range, including the awkward real case
    for good in ("5.25", "0.5", "2.1833", "5.016667", "0.0001"):
        m = schema(**{"expense_public_id": "e-1", "quantity": Decimal(good), **extra})
        assert m.quantity == Decimal(good)

    # QBO's own 6dp shape must survive — this is why decimal_places is NOT set
    m = schema(**{"expense_public_id": "e-1", "quantity": Decimal("5.016667"), **extra})
    assert m.quantity == Decimal("5.016667")

    # the exact DECIMAL(18,4) ceiling is accepted, at both signs
    for edge in ("99999999999999.9999", "-99999999999999.9999"):
        m = schema(**{"expense_public_id": "e-1", "quantity": Decimal(edge), **extra})
        assert m.quantity == Decimal(edge)

    # anything the column cannot hold is rejected AT THE EDGE, not at the DB.
    # `max_digits=18` alone does NOT do this — it counts TOTAL digits, so it
    # accepts 123456789012345 (15 integer digits), which overflows the column.
    for over in ("123456789012345", "1234567890123456.78", "123456789012345678",
                 "-123456789012345"):
        with pytest.raises(_VE):
            schema(**{"expense_public_id": "e-1", "quantity": Decimal(over), **extra})

    # `max_digits=18` is NOT redundant once `le`/`ge` exist, and this is the
    # case that proves it: 19 significant digits at a magnitude of ~1, which
    # sails through the ceiling and is caught only by the digit count. Without
    # this assertion, deleting `max_digits` leaves the whole file green — the
    # measured state of the bill twin after 06108842 added its le/ge. Mutation-
    # checked: removing `max_digits=18` from schemas.py goes RED here.
    for too_many_digits in ("1.234567890123456789", "0.00000000000000000001"):
        with pytest.raises(_VE):
            schema(**{
                "expense_public_id": "e-1",
                "quantity": Decimal(too_many_digits),
                **extra,
            })


# ---------------------------------------------------------------------------
# 8. complete_expense — the latent bug complete_bill HAD and this one does NOT.
# ---------------------------------------------------------------------------


def test_complete_expense_finalises_a_FRACTIONAL_quantity_line_without_error():
    """The failure mode `complete_bill` had, checked behaviourally on the twin.

    `complete_bill` built a `BillLineItemUpdate` out of the line items it had
    just read back, so a line carrying a fractional quantity raised a
    ValidationError that was swallowed into `line_item_errors` — leaving that
    line `is_draft=True` on a completed bill. `complete_expense` calls the
    service with keyword arguments and never constructs
    `ExpenseLineItemUpdate`, so the same shape must complete cleanly.

    Asserted on OUTCOME, not on source text: zero errors, a 200 (not a 207),
    and the exact 5.25 forwarded to the update. A future "make it consistent
    with bill" refactor that routed this through the pydantic model would go
    RED here even though the annotation fix means the model now accepts 5.25 —
    the assertion is the completion result, not the annotation.
    """
    from entities.expense.business.service import ExpenseService

    line = _existing_line(quantity=FRACTIONAL)
    line.project_id = None  # skips the SharePoint/Excel per-project branches
    expense = SimpleNamespace(
        id=55, public_id="expense-55", status="draft", is_draft=True, vendor_id=9
    )

    svc = ExpenseService(repo=MagicMock())
    svc.repo.finalize_by_id.return_value = expense
    svc.read_by_public_id = MagicMock(return_value=expense)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(
        id=9, public_id="vendor-9"
    )
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [line]
    svc._expense_line_item_service = eli_svc
    svc._upload_to_general_receipts_folder = MagicMock(return_value={"errors": []})
    svc._enqueue_box_uploads = MagicMock()

    result = svc.complete_expense("expense-55")

    assert result["errors"] == []
    assert result["status_code"] == 200
    assert result["expense_finalized"] is True
    _assert_exact_quantity(
        eli_svc.update_by_public_id.call_args.kwargs["quantity"], FRACTIONAL
    )
