"""U-743b: a reviewer's emailed decision carries the billable-to-client judgement.

A reply such as "DO NOT BILL CLIENT" sets ``is_billable=False`` (approval only), the
engine stamps the matched reviewer on the expense line, and a completed expense whose
staged QBO line disagrees gets a billable-only repoint (expense only, never a
HasBeenBilled line).

Every persistence seam is mocked: no connection is opened, no QBO call is made, and
nothing touches Azure. The worktree .env is production; these tests never load it
into a live client."""
import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.bill_credit.api.schemas import BillCreditApplyReviewerDecisionRequest
from entities.bill.api.schemas import BillApplyReviewerDecisionRequest
from entities.bill.business.service import BillService  # noqa: F401  (import-only: the seam exists)
from entities.bill.intelligence.tools import ApplyReviewerDecisionArgs, _apply_reviewer_decision
from entities.bill_line_item.business.service import BillLineItemService
from entities.coding_suggestion.business.model import CodingSuggestion
from entities.expense.api.schemas import ExpenseApplyReviewerDecisionRequest  # noqa: F401
from entities.expense.business.service import ExpenseService
from entities.expense.intelligence.tools import ApplyExpenseReviewerDecisionArgs, _apply_expense_reviewer_decision
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.review.business.reviewer_decision import (
    ReviewerDecisionSpec,
    apply_emailed,
    apply_transactional,
)
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
    RepointLineBilledError,
)
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector

PURCHASE_CLIENT = "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient"
CONNECTOR = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
REVIEW_STATUS = "entities.review_status.business.service.ReviewStatusService"
REVIEW_REPO = "entities.review.persistence.repo.ReviewRepository"
RECIPIENTS = "entities.review.business.recipient_service.ReviewRecipientService"
BIND = "shared.authz.provenance.bind_reviewer_reply_with_notification"

REVIEWER_USER_ID = 42
REVIEWER_EMAIL = "pm@example.com"


# ============================================================================ engine (apply_emailed / apply_transactional)


def _engine_spec(line_service, *, read_lines_in_transaction=True):
    line = SimpleNamespace(id=3, row_version="RV1")
    scc_svc = MagicMock()
    scc_svc.read_by_public_id.return_value = SimpleNamespace(id=101, is_holding_code=False)
    spec = ReviewerDecisionSpec(
        parent="expense",
        entity_type="Expense",
        provenance_what="expense",
        kind="expense",
        read_parent=lambda _pid: SimpleNamespace(id=9, is_draft=True),
        resolve_recipients=lambda _svc, _pid: {
            "to": [SimpleNamespace(email=REVIEWER_EMAIL, user_id=REVIEWER_USER_ID)],
            "cc": [],
        },
        line_service_factory=lambda: line_service,
        read_lines=lambda _svc, _pid: [line],
        count_lines_for_update=lambda _svc, _pid, _conn: 1,
        lock_current_review=lambda _repo, _pid, _conn: None,
        apply_error=lambda code, **_kw: RuntimeError(str(code)),
        get_connection=_conn_cm,
        sub_cost_code_service=lambda: scc_svc,
        assert_may_act_as=MagicMock(return_value=False),
        logger=logging.getLogger("u743b.test"),
        emailed_fresh_status_service_for_readback=False,
        read_lines_in_transaction=(lambda _svc, _pid, _conn: [line]) if read_lines_in_transaction else None,
    )
    return spec


@contextmanager
def _conn_cm():
    yield MagicMock(name="conn")


def _statuses():
    return [
        SimpleNamespace(id=1, is_final=True, is_declined=False, name="Approved"),
        SimpleNamespace(id=2, is_final=False, is_declined=True, name="Declined"),
    ]


def _run_emailed(*, decision, is_billable=None, has_kwarg=True):
    """Call apply_emailed with the persistence seams mocked. Returns the line service mock."""
    line_service = MagicMock(name="line_service")
    spec = _engine_spec(line_service)
    review_repo = MagicMock(name="review_repo")
    review_repo.create.return_value = SimpleNamespace(
        review_status_id=1, user_id=REVIEWER_USER_ID, public_id="00000000-0000-0000-0000-000000000001",
        review_kind="approved",
    )
    status_service = MagicMock(name="status_service")
    status_service.read_all.return_value = _statuses()
    status_service.read_by_id.return_value = SimpleNamespace(name="Approved")
    kwargs = dict(
        public_id="EXP-1",
        decision=decision,
        reviewer_email=REVIEWER_EMAIL,
        sub_cost_code_public_id="scc-1",
        description=None,
        raw_reply_text="do not bill client",
        reviewer_email_message_public_id="msg-1",
    )
    if has_kwarg:
        kwargs["is_billable"] = is_billable
    with patch(REVIEW_STATUS, return_value=status_service), \
            patch(REVIEW_REPO, return_value=review_repo), \
            patch(RECIPIENTS), \
            patch(BIND, return_value=SimpleNamespace(id=77)):
        apply_emailed(spec, **kwargs)
    return line_service


def test_engine_emailed_approved_passes_is_billable_and_reviewer_id():
    line_service = _run_emailed(decision="approved", is_billable=False)
    kwargs = line_service.update_coding_in_transaction.call_args.kwargs
    assert kwargs["is_billable"] is False
    assert kwargs["billable_decided_by_user_id"] == REVIEWER_USER_ID


def test_engine_emailed_approved_omits_billable_kwargs_when_none():
    line_service = _run_emailed(decision="approved", is_billable=None)
    kwargs = line_service.update_coding_in_transaction.call_args.kwargs
    assert "is_billable" not in kwargs
    assert "billable_decided_by_user_id" not in kwargs


def test_engine_emailed_approved_without_is_billable_argument_is_unchanged():
    line_service = _run_emailed(decision="approved", has_kwarg=False)
    kwargs = line_service.update_coding_in_transaction.call_args.kwargs
    assert "is_billable" not in kwargs


def test_engine_emailed_rejected_ignores_is_billable():
    line_service = _run_emailed(decision="rejected", is_billable=False)
    line_service.update_coding_in_transaction.assert_not_called()


def test_engine_transactional_never_passes_is_billable():
    line_service = MagicMock(name="line_service")
    spec = _engine_spec(line_service)
    review_repo = MagicMock(name="review_repo")
    review_repo.read_by_idempotency_key.return_value = None
    review_repo.create.return_value = SimpleNamespace(
        review_status_id=1, user_id=REVIEWER_USER_ID, public_id="00000000-0000-0000-0000-000000000001",
        review_kind="approved",
    )
    rs = MagicMock(name="rs")
    rs.get_approved_status.return_value = _statuses()[0]
    with patch(REVIEW_STATUS, return_value=rs), patch(REVIEW_REPO, return_value=review_repo), \
            patch(RECIPIENTS):
        apply_transactional(
            spec,
            public_id="EXP-1",
            decision="approved",
            sub_cost_code_public_id="scc-1",
            description=None,
            idempotency_key="idem-1",
            line_row_version="RV1",
            expected_review_public_id=None,
            caller_user_id=REVIEWER_USER_ID,
        )
    kwargs = line_service.update_coding_in_transaction.call_args.kwargs
    assert "is_billable" not in kwargs
    assert "billable_decided_by_user_id" not in kwargs


# ============================================================================ line services


def _expense_line_service():
    svc = ExpenseLineItemService.__new__(ExpenseLineItemService)
    svc.repo = MagicMock(name="repo")
    return svc


def _expense_line():
    return SimpleNamespace(
        row_version=None, sub_cost_code_id=None, description=None,
        is_billable=None, billable_decided_by_user_id=None, billable_decided_at=None,
    )


def test_expense_line_stamps_reviewer_and_timestamp_when_is_billable_given():
    svc = _expense_line_service()
    line = _expense_line()
    svc.update_coding_in_transaction(
        line_item=line, row_version="RV1", sub_cost_code_id=101, description=None, conn=MagicMock(),
        is_billable=False, billable_decided_by_user_id=REVIEWER_USER_ID,
    )
    assert line.is_billable is False
    assert line.billable_decided_by_user_id == REVIEWER_USER_ID
    assert isinstance(line.billable_decided_at, datetime)
    assert line.billable_decided_at.tzinfo is None


def test_expense_line_none_is_billable_changes_nothing_billable():
    svc = _expense_line_service()
    line = _expense_line()
    svc.update_coding_in_transaction(
        line_item=line, row_version="RV1", sub_cost_code_id=101, description=None, conn=MagicMock(),
        is_billable=None, billable_decided_by_user_id=REVIEWER_USER_ID,
    )
    assert line.is_billable is None
    assert line.billable_decided_by_user_id is None
    assert line.billable_decided_at is None


def test_expense_line_is_billable_without_reviewer_id_sets_flag_but_no_stamp():
    svc = _expense_line_service()
    line = _expense_line()
    svc.update_coding_in_transaction(
        line_item=line, row_version="RV1", sub_cost_code_id=101, description=None, conn=MagicMock(),
        is_billable=True,
    )
    assert line.is_billable is True
    assert line.billable_decided_at is None


def test_bill_line_sets_is_billable_and_no_stamp():
    svc = BillLineItemService.__new__(BillLineItemService)
    svc.repo = MagicMock(name="repo")
    line = SimpleNamespace(row_version=None, sub_cost_code_id=None, description=None,
                           coding_prefill_state=None, is_billable=None)
    svc.update_coding_in_transaction(
        line_item=line, row_version="RV1", sub_cost_code_id=101, description=None, conn=MagicMock(),
        is_billable=False, billable_decided_by_user_id=REVIEWER_USER_ID,
    )
    assert line.is_billable is False
    assert not hasattr(line, "billable_decided_by_user_id")
    assert not hasattr(line, "billable_decided_at")


# ============================================================================ request models


def test_expense_request_accepts_is_billable():
    assert "is_billable" in ExpenseApplyReviewerDecisionRequest.model_fields
    body = ExpenseApplyReviewerDecisionRequest(
        decision="approved", reviewer_email=REVIEWER_EMAIL, reviewer_email_message_public_id="m",
        is_billable=False,
    )
    assert body.is_billable is False


def test_bill_request_accepts_is_billable():
    assert "is_billable" in BillApplyReviewerDecisionRequest.model_fields
    body = BillApplyReviewerDecisionRequest(
        decision="approved", reviewer_email=REVIEWER_EMAIL, reviewer_email_message_public_id="m",
        is_billable=True,
    )
    assert body.is_billable is True


def test_bill_credit_request_does_not_carry_is_billable():
    assert "is_billable" not in BillCreditApplyReviewerDecisionRequest.model_fields


# ============================================================================ agent tools


def _ctx():
    ctx = MagicMock(name="ctx")
    ctx.call_api = AsyncMock(return_value={"ok": True})
    return ctx


def _posted_body(handler, args, ctx):
    asyncio.run(handler(args, ctx))
    return ctx.call_api.call_args.kwargs["body"]


BASE_ARGS = {
    "decision": "approved",
    "reviewer_email": REVIEWER_EMAIL,
    "sub_cost_code_public_id": "scc-1",
    "reviewer_email_message_public_id": "msg-1",
}


def test_expense_tool_args_has_is_billable_and_posts_it():
    assert "is_billable" in ApplyExpenseReviewerDecisionArgs.model_fields
    body = _posted_body(
        _apply_expense_reviewer_decision,
        {"expense_public_id": "E1", **BASE_ARGS, "is_billable": False},
        _ctx(),
    )
    assert body["is_billable"] is False


def test_expense_tool_posts_is_billable_none_when_omitted():
    body = _posted_body(_apply_expense_reviewer_decision, {"expense_public_id": "E1", **BASE_ARGS}, _ctx())
    assert "is_billable" in body
    assert body["is_billable"] is None


def test_bill_tool_args_has_is_billable_and_posts_it():
    assert "is_billable" in ApplyReviewerDecisionArgs.model_fields
    body = _posted_body(
        _apply_reviewer_decision,
        {"bill_public_id": "B1", **BASE_ARGS, "is_billable": True},
        _ctx(),
    )
    assert body["is_billable"] is True


def test_bill_tool_posts_is_billable_none_when_omitted():
    body = _posted_body(_apply_reviewer_decision, {"bill_public_id": "B1", **BASE_ARGS}, _ctx())
    assert "is_billable" in body
    assert body["is_billable"] is None


# ============================================================================ divergence (completion repoint, billable)

DIV_LOG = "expense.complete.repoint_billable_check_unavailable"


def _div_full():
    return SimpleNamespace(
        written_lines=[{"qbo_line_id": "10", "item_ref_value": "ITEM-A"}],
        written_sync_token="TOK-9",
        qbo_purchase_qbo_id="P1",
        realm_id="R1",
        expense_id=5,
    )


def _local(*, decided=True, is_billable=False, project_id=7):
    return {"10": SimpleNamespace(
        qbo_id="10", sub_cost_code_id=101, project_id=project_id, description="d",
        amount=Decimal("5.00"), is_billable=is_billable,
        billable_decided_at=datetime(2026, 11, 8) if decided else None,
    )}


def _run_billable(*, local, staged_item="ITEM-A", staged_billable="Billable", staged_customer="CUST-NEW",
                  new_customer="CUST-NEW", item="ITEM-A", staged_read_error=None):
    svc = ExpenseService()
    connector = MagicMock()
    connector._get_qbo_customer_ref.return_value = (
        SimpleNamespace(value=new_customer, name="n") if new_customer else None
    )
    staged = [SimpleNamespace(qbo_line_id="10", item_ref_value=staged_item,
                              customer_ref_value=staged_customer, billable_status=staged_billable)]
    read = {"side_effect": staged_read_error} if staged_read_error else {"return_value": staged}
    with patch.object(ExpenseService, "_read_staged_purchase_lines", **read), \
            patch(CONNECTOR, return_value=connector):
        return svc._compute_repoint_divergence(
            "S1", _div_full(), local, "R1",
            lambda scc, realm_id=None, sub_cost_code_service=None: SimpleNamespace(value=item),
        )


def test_divergence_stamped_false_vs_staged_billable_emits_billable_target():
    out = _run_billable(local=_local(is_billable=False), staged_billable="Billable")
    assert out is not None
    target = out["targets"][0]
    assert target["billable_changed"] is True
    assert target["repoint_from_item_ref_value"] == "ITEM-A"
    assert target["qbo_line_id"] == "10"
    assert "customer_changed" not in target


def test_divergence_unstamped_line_emits_no_billable_target():
    assert _run_billable(local=_local(decided=False, is_billable=False), staged_billable="Billable") is None


def test_divergence_staged_has_been_billed_emits_no_billable_target():
    assert _run_billable(local=_local(is_billable=False), staged_billable="HasBeenBilled") is None


def test_divergence_staging_read_failure_skips_billable_judgement_and_logs(caplog):
    caplog.set_level(logging.WARNING)
    out = _run_billable(local=_local(is_billable=False), staged_read_error=RuntimeError("staging down"))
    assert out is None
    assert DIV_LOG in caplog.text


def test_divergence_item_change_and_billable_change_is_one_target():
    out = _run_billable(local=_local(is_billable=False), item="ITEM-NEW", staged_billable="Billable")
    assert out is not None
    assert len(out["targets"]) == 1
    target = out["targets"][0]
    assert target["billable_changed"] is True
    assert target["repoint_from_item_ref_value"] == "ITEM-A"


def test_divergence_billable_already_matching_emits_no_target():
    assert _run_billable(local=_local(is_billable=False), staged_billable="NotBillable") is None


# ============================================================================ model


def _suggestion_with_targets(targets):
    return SimpleNamespace(repoint_targets_json=json.dumps(targets))


def _target(**extra):
    base = {"qbo_line_id": "10", "repoint_from_item_ref_value": "ITEM-A", "sub_cost_code_id": 101,
            "project_id": 7, "description": "d"}
    base.update(extra)
    return base


def test_model_billable_changed_non_bool_fails_closed():
    rec = _suggestion_with_targets([_target(billable_changed="yes")])
    assert CodingSuggestion.repoint_targets.fget(rec) is None


def test_model_billable_changed_true_is_accepted():
    rec = _suggestion_with_targets([_target(billable_changed=True)])
    assert CodingSuggestion.repoint_targets.fget(rec) == [_target(billable_changed=True)]


# ============================================================================ worker


def _worker():
    return QboOutboxWorker.__new__(QboOutboxWorker)


def _drain_item(targets):
    return SimpleNamespace(
        repoint_targets=targets,
        written_lines=[{"qbo_line_id": "10", "item_ref_value": "ITEM-A"}],
        confirmed_project_id=None,
    )


def _drain_resolve(item):
    worker = _worker()
    local = SimpleNamespace(quantity=None, rate=None, amount=Decimal("5.00"), is_billable=False,
                            billable_decided_at=datetime(2026, 11, 8))
    row = SimpleNamespace(entity_public_id="S1")
    with patch.object(worker, "_resolve_recode_line_economics", return_value=(local, None)), \
            patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-A")):
        return worker._resolve_repoint_targets(row, item, "R1")


def test_drain_item_equal_billable_changed_is_not_skipped_and_flag_passed():
    item = _drain_item([_target(billable_changed=True)])
    targets, err = _drain_resolve(item)
    assert err is None
    assert len(targets) == 1
    assert targets[0]["target_qbo_line_id"] == "10"
    assert targets[0]["billable_changed"] is True
    assert targets[0]["repoint_from_item_ref_value"] == "ITEM-A"


def test_drain_item_equal_without_billable_change_still_skipped():
    targets, err = _drain_resolve(_drain_item([_target()]))
    assert err is None
    assert targets == []


# ============================================================================ writer (connector)


def _conn():
    return PurchaseExpenseConnector.__new__(PurchaseExpenseConnector)


def _line(line_id="10", *, item="ITEM-A", customer="CUST-OLD", billable="Billable", amount=5):
    return {
        "Id": line_id,
        "DetailType": "ItemBasedExpenseLineDetail",
        "Amount": amount,
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": item},
            "CustomerRef": {"value": customer},
            "BillableStatus": billable,
        },
    }


def _raw(*lines, token="5"):
    return {"Id": "P1", "SyncToken": token, "Line": list(lines)}


def _spec(**extra):
    base = {"target_qbo_line_id": "10", "sub_cost_code_id": 101, "project_id": 7, "description": "d",
            "quantity": None, "rate": None, "amount": None, "is_billable": None,
            "repoint_from_item_ref_value": "ITEM-A"}
    base.update(extra)
    return base


def _recode(raw, *, spec, expected="5", customer_value="CUST-NEW", item_value="ITEM-A"):
    conn = _conn()
    client = MagicMock()
    client.get_purchase_raw.return_value = raw
    client.update_purchase_raw.return_value = {"SyncToken": "6"}
    with patch(PURCHASE_CLIENT) as cls:
        cls.return_value.__enter__.return_value = client
        with patch.object(conn, "_get_qbo_item_ref", return_value=SimpleNamespace(value=item_value, name="i")), \
                patch.object(conn, "_get_qbo_customer_ref",
                             return_value=SimpleNamespace(value=customer_value, name="c")):
            out = conn.recode_purchase_lines(
                realm_id="R1", qbo_purchase_qbo_id="P1", lines=[spec], expected_sync_token=expected,
            )
    return out, client


def test_writer_billable_changed_item_equal_mutates_billable_status_and_keeps_sibling():
    sibling = _line("11", item="ITEM-A", customer="CUST-NEW", billable="Billable", amount=7)
    raw = _raw(_line("10", item="ITEM-A", customer="CUST-NEW", billable="Billable"), sibling)
    out, client = _recode(raw, spec=_spec(billable_changed=True, is_billable=False))
    assert out["status"] == "written"
    client.update_purchase_raw.assert_called_once()
    sent_lines = client.update_purchase_raw.call_args[0][0]["Line"]
    target_line = next(line for line in sent_lines if str(line["Id"]) == "10")
    assert target_line["ItemBasedExpenseLineDetail"]["BillableStatus"] == "NotBillable"
    untouched = next(line for line in sent_lines if str(line["Id"]) == "11")
    assert untouched == sibling


def test_writer_billable_changed_on_has_been_billed_line_raises():
    raw = _raw(_line("10", item="ITEM-A", customer="CUST-NEW", billable="HasBeenBilled"))
    with pytest.raises(RepointLineBilledError):
        _recode(raw, spec=_spec(billable_changed=True, is_billable=False))


def test_writer_without_billable_changed_item_equal_is_already_recoded():
    raw = _raw(_line("10", item="ITEM-A", customer="CUST-NEW", billable="Billable"))
    out, client = _recode(raw, spec=_spec(is_billable=False))
    assert out["status"] == "already_recoded"
    assert len(out["verified_lines"]) == 1
    client.update_purchase_raw.assert_not_called()


def test_writer_billable_changed_token_mismatch_still_fenced():
    raw = _raw(_line("10", item="ITEM-A", customer="CUST-NEW", billable="Billable"), token="5")
    with pytest.raises(PurchaseChangedInQboError):
        _recode(raw, spec=_spec(billable_changed=True, is_billable=False), expected="4")


# ============================================================================ U-743b fix round 1 (F1 / F2 / F3)


def _fix_line(line_id="10", *, item="ITEM-A", customer="CUST-OLD", billable="Billable"):
    """A live ItemBased line carrying every field the narrow path must leave byte-identical."""
    return {
        "Id": line_id,
        "DetailType": "ItemBasedExpenseLineDetail",
        "Amount": 5,
        "Description": "d",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": item, "name": "i"},
            "CustomerRef": {"value": customer, "name": "old"},
            "ClassRef": {"value": "C1"},
            "TaxCodeRef": {"value": "TAX"},
            "BillableStatus": billable,
            "Qty": 1,
            "UnitPrice": 5,
        },
    }


def _recode_with_customer(raw, *, spec, customer_ref, item_value="ITEM-A", expected="5"):
    conn = _conn()
    client = MagicMock()
    client.get_purchase_raw.return_value = raw
    client.update_purchase_raw.return_value = {"SyncToken": "6"}
    with patch(PURCHASE_CLIENT) as cls:
        cls.return_value.__enter__.return_value = client
        with patch.object(conn, "_get_qbo_item_ref", return_value=SimpleNamespace(value=item_value, name="i")), \
                patch.object(conn, "_get_qbo_customer_ref", return_value=customer_ref):
            out = conn.recode_purchase_lines(
                realm_id="R1", qbo_purchase_qbo_id="P1", lines=[spec], expected_sync_token=expected,
            )
    return out, client


def test_u743b_fix_billable_only_narrow_write_changes_only_billable_status():
    import copy

    target = _fix_line("10", customer="CUST-OLD", billable="Billable")
    sibling = _line("11", item="ITEM-A", customer="CUST-NEW", billable="Billable", amount=7)
    raw = _raw(copy.deepcopy(target), copy.deepcopy(sibling))
    spec = _spec(billable_changed=True, is_billable=False, description="d")
    out, client = _recode_with_customer(raw, spec=spec, customer_ref=SimpleNamespace(value="CUST-NEW", name="new"))
    assert out["status"] == "written"
    assert out["written_lines"] == [
        {"qbo_line_id": "10", "item_ref_value": "ITEM-A", "sub_cost_code_id": 101}
    ]
    sent_lines = client.update_purchase_raw.call_args[0][0]["Line"]
    sent_target = next(line for line in sent_lines if str(line["Id"]) == "10")
    expected_target = copy.deepcopy(target)
    expected_target["ItemBasedExpenseLineDetail"]["BillableStatus"] = "NotBillable"
    assert sent_target == expected_target
    sent_sibling = next(line for line in sent_lines if str(line["Id"]) == "11")
    assert sent_sibling == sibling


def test_u743b_fix_billable_only_billable_against_foreign_customer_raises_and_writes_nothing():
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    raw = _raw(_fix_line("10", customer="CUST-OLD", billable="NotBillable"))
    spec = _spec(billable_changed=True, is_billable=True)
    with pytest.raises(RepointBillableNoCustomerError) as exc:
        _recode_with_customer(raw, spec=spec, customer_ref=SimpleNamespace(value="CUST-NEW", name="new"))
    assert exc.value.qbo_line_id == "10"
    assert RepointBillableNoCustomerError.WRITE_ERROR == "repoint_billable_no_customer"


def test_u743b_fix_stamped_billable_item_change_with_no_project_customer_raises():
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    raw = _raw(_fix_line("10", item="ITEM-A", customer="CUST-OLD", billable="NotBillable"))
    spec = _spec(billable_changed=True, is_billable=True)
    with pytest.raises(RepointBillableNoCustomerError):
        _recode_with_customer(raw, spec=spec, customer_ref=None, item_value="ITEM-B")
    raw_check = raw["Line"][0]["ItemBasedExpenseLineDetail"]
    assert raw_check["ItemRef"]["value"] == "ITEM-A"


def test_u743b_fix_stamped_billable_billable_only_with_no_project_customer_raises():
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    raw = _raw(_fix_line("10", customer="CUST-OLD", billable="NotBillable"))
    spec = _spec(billable_changed=True, is_billable=True)
    with pytest.raises(RepointBillableNoCustomerError):
        _recode_with_customer(raw, spec=spec, customer_ref=None)


def test_u743b_fix_stamped_billable_without_project_id_raises():
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    raw = _raw(_fix_line("10", customer="CUST-OLD", billable="NotBillable"))
    spec = _spec(billable_changed=True, is_billable=True, project_id=None)
    with pytest.raises(RepointBillableNoCustomerError):
        _recode_with_customer(raw, spec=spec, customer_ref=SimpleNamespace(value="CUST-NEW", name="new"))


def test_u743b_fix_not_billable_with_no_project_customer_writes_not_billable():
    raw = _raw(_fix_line("10", item="ITEM-A", customer="CUST-OLD", billable="Billable"))
    spec = _spec(billable_changed=True, is_billable=False)
    out, client = _recode_with_customer(raw, spec=spec, customer_ref=None, item_value="ITEM-B")
    assert out["status"] == "written"
    client.update_purchase_raw.assert_called_once()
    sent = client.update_purchase_raw.call_args[0][0]["Line"][0]["ItemBasedExpenseLineDetail"]
    assert sent["BillableStatus"] == "NotBillable"
    assert sent["ItemRef"]["value"] == "ITEM-B"


def test_u743b_fix_billable_only_token_drift_still_fenced_on_narrow_path():
    raw = _raw(_fix_line("10", customer="CUST-OLD", billable="Billable"), token="5")
    spec = _spec(billable_changed=True, is_billable=False)
    with pytest.raises(PurchaseChangedInQboError):
        _recode_with_customer(raw, spec=spec, customer_ref=SimpleNamespace(value="CUST-OLD", name="old"),
                              expected="4")


def test_u743b_fix_worker_marks_billable_no_customer_error_terminal():
    from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    worker = _worker()
    item = SimpleNamespace(status="enqueued", confirmation_seq=4, qbo_purchase_qbo_id="P1",
                           sync_token_at_suggest="5", confirmation_source=REPOINT_CONFIRMATION_SOURCE,
                           written_lines=[], written_sync_token="5")
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    svc.mark_error.return_value = SimpleNamespace(public_id="S1")
    conn = MagicMock()
    conn.recode_purchase_lines.side_effect = RepointBillableNoCustomerError(qbo_line_id="10")
    row = SimpleNamespace(entity_public_id="S1", realm_id="R1")
    with patch("entities.coding_suggestion.business.service.CodingSuggestionService", return_value=svc), \
            patch(CONNECTOR, return_value=conn), \
            patch.object(worker, "_resolve_recode_purchase_line_targets",
                         return_value=([_spec(billable_changed=True, is_billable=True)], None)):
        worker._drain_recode_purchase_line_locked(row)
    assert svc.mark_error.call_args.kwargs["write_error"] == "repoint_billable_no_customer"


def test_u743b_fix_repoint_key_differs_when_only_billable_changed_differs():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    key = CodingSuggestionService._repoint_target_key
    assert key(_target()) != key(_target(billable_changed=True))


def test_u743b_fix_repoint_key_differs_when_only_customer_changed_differs():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    key = CodingSuggestionService._repoint_target_key
    assert key(_target()) != key(_target(customer_changed=True))


def test_u743b_fix_repoint_key_absent_flag_equals_false():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    key = CodingSuggestionService._repoint_target_key
    assert key(_target()) == key(_target(billable_changed=False, customer_changed=False))


# ============================================================================ U-743b fix round 2 (F1 residual)


def test_u743b_fix2_stamped_billable_item_change_without_billable_changed_raises():
    """Codex r1: an item repoint whose staged status already matches (billable_changed False) still carries a
    stamped Billable; with no project customer the writer must refuse, never downgrade to NotBillable."""
    from integrations.intuit.qbo.purchase.connector.expense.business.errors import RepointBillableNoCustomerError

    raw = _raw(_fix_line("10", item="ITEM-A", customer="CUST-OLD", billable="Billable"))
    spec = _spec(is_billable=True, billable_decided=True)
    with pytest.raises(RepointBillableNoCustomerError):
        _recode_with_customer(raw, spec=spec, customer_ref=None, item_value="ITEM-B")


def test_u743b_fix2_undecided_billable_item_change_without_customer_keeps_derived_downgrade():
    """An UNSTAMPED (derived) Billable keeps the U-099 rule: no project customer => NotBillable, written."""
    raw = _raw(_fix_line("10", item="ITEM-A", customer="CUST-OLD", billable="Billable"))
    spec = _spec(is_billable=True)
    out, client = _recode_with_customer(raw, spec=spec, customer_ref=None, item_value="ITEM-B")
    assert out["status"] == "written"
    sent = client.update_purchase_raw.call_args[0][0]["Line"][0]["ItemBasedExpenseLineDetail"]
    assert sent["BillableStatus"] == "NotBillable"


def test_u743b_fix2_drain_passes_billable_decided_for_a_stamped_line():
    item = _drain_item([_target(billable_changed=True)])
    targets, err = _drain_resolve(item)
    assert err is None
    assert targets[0]["billable_decided"] is True


def test_u743b_fix2_drain_billable_decided_false_for_an_unstamped_line():
    worker = _worker()
    local = SimpleNamespace(quantity=None, rate=None, amount=Decimal("5.00"), is_billable=False,
                            billable_decided_at=None)
    row = SimpleNamespace(entity_public_id="S1")
    item = _drain_item([_target(billable_changed=True)])
    with patch.object(worker, "_resolve_recode_line_economics", return_value=(local, None)), \
            patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-A")):
        targets, err = worker._resolve_repoint_targets(row, item, "R1")
    assert err is None
    assert targets[0]["billable_decided"] is False


# ============================================================================ routes carry is_billable (mutation gap)


@pytest.mark.parametrize(
    "router_path,svc_name,base,pid_kw",
    [
        ("entities.expense.api.router", "ExpenseService", "/api/v1/expense", "expense_public_id"),
        ("entities.bill.api.router", "BillService", "/api/v1/bill", "bill_public_id"),
    ],
)
@pytest.mark.parametrize("value", [False, True, None])
def test_apply_route_passes_is_billable_through(router_path, svc_name, base, pid_kw, value):
    """The emailed apply route hands the body's is_billable to the service unchanged (omitted -> None)."""
    import importlib
    import inspect

    from fastapi.testclient import TestClient

    from app import app

    router_mod = importlib.import_module(router_path)
    dep = inspect.signature(router_mod.apply_reviewer_decision_router).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub", "id": 1}
    body = {
        "decision": "approved",
        "reviewer_email": "pm@example.com",
        "sub_cost_code_public_id": "SCC",
        "reviewer_email_message_public_id": "MSG",
    }
    if value is not None:
        body["is_billable"] = value
    try:
        with patch.object(router_mod, svc_name) as svc:
            svc.return_value.apply_reviewer_decision.return_value = {"decision_applied": "approved"}
            resp = TestClient(app, raise_server_exceptions=False).post(f"{base}/PID-1/apply-reviewer-decision", json=body)
        assert resp.status_code == 200
        kwargs = svc.return_value.apply_reviewer_decision.call_args.kwargs
        assert kwargs[pid_kw] == "PID-1"
        assert kwargs["is_billable"] is value
    finally:
        app.dependency_overrides.pop(dep, None)
