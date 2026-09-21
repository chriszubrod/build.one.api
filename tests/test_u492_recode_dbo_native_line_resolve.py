"""U-492: recode handler resolves dbo-native ExpenseLineItem economics (no mapping table)."""
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.test_u488_recode_carries_qty_rate_billable import (
    PUBLIC_ID,
    PURCHASE_QBO_ID,
    REALM_ID,
    TARGET_LINE_ID,
    _make_coding_item,
    _make_outbox_row,
)


def _run_handler_with_mocks(
    *,
    expense_return,
    line_return,
    expense_side_effect=None,
    line_side_effect=None,
):
    patches = [
        patch("entities.expense_coding_item.business.service.ExpenseCodingItemService"),
        patch(
            "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
        ),
        patch("entities.expense.business.service.ExpenseService"),
        patch("entities.expense_line_item.business.service.ExpenseLineItemService"),
        patch(
            "integrations.intuit.qbo.base.reconciliation_recorder.record_mapping_issue"
        ),
    ]
    with patches[0] as mock_eci_cls, patches[1] as mock_connector_cls, patches[
        2
    ] as mock_expense_svc_cls, patches[3] as mock_eli_svc_cls, patches[4] as mock_record:
        svc = MagicMock()
        mock_eci_cls.return_value = svc
        svc.read_by_public_id.return_value = _make_coding_item()

        expense_mock = mock_expense_svc_cls.return_value
        if expense_side_effect is not None:
            expense_mock.read_by_qbo_identity.side_effect = expense_side_effect
        else:
            expense_mock.read_by_qbo_identity.return_value = expense_return

        eli_mock = mock_eli_svc_cls.return_value
        if line_side_effect is not None:
            eli_mock.read_by_qbo_identity.side_effect = line_side_effect
        else:
            eli_mock.read_by_qbo_identity.return_value = line_return

        connector = MagicMock()
        mock_connector_cls.return_value = connector
        connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}

        QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

        return SimpleNamespace(
            svc=svc,
            connector=connector,
            expense_mock=expense_mock,
            eli_mock=eli_mock,
            record_mapping_issue=mock_record,
        )


# S1: map-miss population now resolves via dbo-native identity reads.


def test_s1_map_miss_population_resolves_economics():
    line = SimpleNamespace(
        quantity=2,
        rate=Decimal("10.50"),
        amount=Decimal("21.00"),
        is_billable=False,
    )
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=9001),
        line_return=line,
    )
    kwargs = ctx.connector.recode_purchase_line.call_args.kwargs
    assert kwargs["quantity"] == 2
    assert kwargs["rate"] == Decimal("10.50")
    assert kwargs["amount"] == Decimal("21.00")
    assert kwargs["is_billable"] is False
    ctx.record_mapping_issue.assert_not_called()


# S2: line read is parent-scoped by expense.id.


def test_s2_line_read_uses_resolved_expense_id():
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=4242),
        line_return=SimpleNamespace(
            quantity=1,
            rate=Decimal("1"),
            amount=Decimal("1"),
            is_billable=True,
        ),
    )
    ctx.eli_mock.read_by_qbo_identity.assert_called_once_with(4242, TARGET_LINE_ID)


# S3: expense read uses purchase QBO id + realm.


def test_s3_expense_read_uses_purchase_qbo_id_and_realm():
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=1),
        line_return=SimpleNamespace(
            quantity=1,
            rate=Decimal("1"),
            amount=Decimal("1"),
            is_billable=True,
        ),
    )
    ctx.expense_mock.read_by_qbo_identity.assert_called_once_with(
        PURCHASE_QBO_ID, REALM_ID
    )


# S4: unresolvable line still recodes; warning + reconciliation issue recorded.


def test_s4_unresolvable_logs_records_issue_and_still_writes(caplog):
    with caplog.at_level("WARNING"):
        ctx = _run_handler_with_mocks(expense_return=None, line_return=None)

    assert any(PUBLIC_ID in m for m in caplog.messages)
    ctx.record_mapping_issue.assert_called_once()
    call_kwargs = ctx.record_mapping_issue.call_args.kwargs
    assert call_kwargs["drift_type"] == "missing_mapping"
    assert call_kwargs["entity_type"] == "ExpenseCodingItem"
    assert call_kwargs["entity_public_id"] == PUBLIC_ID
    ctx.connector.recode_purchase_line.assert_called_once()
    ctx.svc.mark_written.assert_called_once()


# S5: retired mapping repo must not appear in worker source.


def test_s5_worker_has_no_purchase_line_expense_line_item_repo():
    from integrations.intuit.qbo.outbox.business import worker as worker_module

    source = inspect.getsource(worker_module)
    assert "PurchaseLineExpenseLineItemRepository" not in source


# S6: resolve exception is logged and does not block recode.


def test_s6_resolve_exception_logs_and_still_recodes(caplog):
    with caplog.at_level("WARNING"):
        ctx = _run_handler_with_mocks(
            expense_return=None,
            line_return=None,
            expense_side_effect=RuntimeError("db blew up"),
        )

    assert any("db blew up" in m or PUBLIC_ID in m for m in caplog.messages)
    ctx.record_mapping_issue.assert_not_called()
    ctx.connector.recode_purchase_line.assert_called_once()
    ctx.svc.mark_written.assert_called_once()


# ---------------------------------------------------------------------------
# S9-S11 — U-492 round 2 (Pass-1 P1): the resolved line's realm is checked.
#
# The line read is parent-scoped by ExpenseId and the parent is realm-scoped,
# so realm is pinned TRANSITIVELY -- but only while every ExpenseLineItem.RealmId
# agrees with its parent Expense's. Nothing enforces that: it is a data
# invariant, not a constraint (0 violations live, 1 realm in prod). The new SQL
# readers assert eli.RealmId = p.RealmId outright; this path did not, and this
# is the path that copies money onto a live QuickBooks line.
# ---------------------------------------------------------------------------


def test_s9_line_from_another_realm_is_refused_and_recorded():
    """A line whose realm disagrees with the outbox row must NOT have its
    economics copied onto that realm's live QBO Purchase line."""
    line = SimpleNamespace(
        quantity=3,
        rate=Decimal("5.00"),
        amount=Decimal("15.00"),
        is_billable=True,
        realm_id="a-different-realm",
    )
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=9001),
        line_return=line,
    )
    kwargs = ctx.connector.recode_purchase_line.call_args.kwargs
    assert kwargs["quantity"] is None, "cross-realm economics must not be sent"
    assert kwargs["rate"] is None
    assert kwargs["amount"] is None
    assert kwargs["is_billable"] is None
    # observable, not silent -- the whole point of the unit
    ctx.record_mapping_issue.assert_called_once()
    details = ctx.record_mapping_issue.call_args.kwargs["details"]
    assert "a-different-realm" in details and "across realms" in details
    # and the recode itself still proceeds (the approved unresolvable behaviour)
    ctx.connector.recode_purchase_line.assert_called_once()
    ctx.svc.mark_written.assert_called_once()


def test_s10_null_realm_line_still_resolves():
    """124 live ExpenseLineItem rows carry no RealmId. NULL is absence, not
    disagreement -- they must keep resolving, or this guard becomes a
    regression for exactly the rows it was not aimed at."""
    line = SimpleNamespace(
        quantity=4,
        rate=Decimal("2.50"),
        amount=Decimal("10.00"),
        is_billable=False,
        realm_id=None,
    )
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=9002),
        line_return=line,
    )
    kwargs = ctx.connector.recode_purchase_line.call_args.kwargs
    assert kwargs["quantity"] == 4
    assert kwargs["amount"] == Decimal("10.00")
    ctx.record_mapping_issue.assert_not_called()


def test_s11_matching_realm_line_resolves():
    """The ordinary case: same realm, economics carried."""
    line = SimpleNamespace(
        quantity=6,
        rate=Decimal("1.25"),
        amount=Decimal("7.50"),
        is_billable=True,
        realm_id=REALM_ID,
    )
    ctx = _run_handler_with_mocks(
        expense_return=SimpleNamespace(id=9003),
        line_return=line,
    )
    kwargs = ctx.connector.recode_purchase_line.call_args.kwargs
    assert kwargs["quantity"] == 6
    assert kwargs["is_billable"] is True
    ctx.record_mapping_issue.assert_not_called()


# ---------------------------------------------------------------------------
# S12-S13 — Pass-3 gap: the realm guard's inputs must exist on the REAL model.
#
# S9-S11 drive the handler with SimpleNamespace mocks, so if the production
# ExpenseLineItem stopped carrying realm_id the getattr would silently yield
# None, the guard would never fire, and every one of those specs would still
# pass. That is the vacuous-guard shape this repo has been bitten by before,
# so pin the real plumbing rather than only the handler's logic.
# ---------------------------------------------------------------------------


def test_s12_expense_line_item_model_really_carries_realm_id():
    from dataclasses import fields

    from entities.expense_line_item.business.model import ExpenseLineItem

    names = {f.name for f in fields(ExpenseLineItem)}
    assert "realm_id" in names, (
        "the recode realm guard reads local_line.realm_id via getattr; without "
        "this field the guard is silently vacuous and S9 still passes"
    )


def test_s13_line_read_sproc_projects_realm_id():
    """The guard is only as good as what the sproc returns."""
    from pathlib import Path

    from tests.sproc_text import sproc_body, strip_sql_comments

    sql = Path("entities/expense_line_item/sql/dbo.expense_line_item.sql")
    body = strip_sql_comments(
        sproc_body(sql, "ReadExpenseLineItemByExpenseIdAndQboId")
    )
    assert "[RealmId]" in body, (
        "ReadExpenseLineItemByExpenseIdAndQboId must project RealmId or the "
        "resolved line always arrives realm-less and the guard cannot fire"
    )


def test_s14_unresolvable_issue_is_not_recorded_as_critical():
    """record_mapping_issue defaults severity='critical'. 76 of the ~77
    unresolvable items are an already-booked backlog; minting criticals for a
    known population is how a real alert gets ignored."""
    ctx = _run_handler_with_mocks(expense_return=None, line_return=None)
    ctx.record_mapping_issue.assert_called_once()
    assert ctx.record_mapping_issue.call_args.kwargs["severity"] == "low"
