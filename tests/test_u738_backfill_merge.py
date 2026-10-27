"""U-738: backfill merge decision logic for recode leftover ExpenseLineItems."""
from decimal import Decimal

from scripts.backfill_merge_recode_leftover_expense_lines import (
    LocalLine,
    classify_local_lines_for_merge,
)
from integrations.intuit.qbo.purchase.connector.recode_leftover import (
    compute_kept_billing_fields,
)


def _line(
    row_id: int,
    qbo_id: str,
    *,
    att: int = 0,
    inv: int = 0,
    sub_cost_code_id: int | None = None,
    amount: Decimal = Decimal("10"),
    is_billed: bool = False,
    is_billable: bool = True,
    billable_decided_at=None,
    public_id: str | None = None,
) -> LocalLine:
    return LocalLine(
        id=row_id,
        public_id=public_id or f"p-{row_id}",
        row_version=b"rv",
        qbo_id=qbo_id,
        realm_id="realm-1",
        sub_cost_code_id=sub_cost_code_id,
        project_id=1,
        description="d",
        quantity=Decimal("1"),
        rate=amount,
        amount=amount,
        is_billable=is_billable,
        is_billed=is_billed,
        billable_decided_by_user_id=17 if billable_decided_at else None,
        billable_decided_at=billable_decided_at,
        markup=Decimal("0"),
        price=amount,
        is_draft=False,
        created_datetime=None,
        attachment_count=att,
        invoice_ref_count=inv,
    )


def _classify(lines, live, **kw):
    holding = kw.pop("holding", frozenset())
    workbook = kw.pop("workbook", frozenset())
    return classify_local_lines_for_merge(
        lines, live, holding_sub_cost_code_ids=holding, workbook_public_ids=workbook
    )


def test_unambiguous_pair_keeps_newer_moves_leftover_attachment():
    leftover = _line(10, "1", att=1)
    newer = _line(20, "2", att=0, sub_cost_code_id=99)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert reason is None
    assert plan is not None
    assert plan.newer.id == 20
    assert plan.leftover.id == 10
    assert plan.move_attachments is True


def test_both_have_attachments_flags_manual():
    leftover = _line(10, "1", att=1)
    newer = _line(20, "2", att=1)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None
    assert reason == "flag_manual:both_have_attachments"


def test_both_invoice_linked_flags_manual():
    leftover = _line(10, "1", inv=1)
    newer = _line(20, "2", inv=1)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None
    assert reason == "flag_manual:both_invoice_linked"


def test_amount_mismatch_flags_manual():
    leftover = _line(10, "1", amount=Decimal("10"))
    newer = _line(20, "2", amount=Decimal("11"))
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None
    assert reason == "flag_manual:amount_mismatch"


def test_leftover_in_workbook_flags_manual():
    leftover = _line(10, "1", public_id="aaa-bbb")
    newer = _line(20, "2", public_id="ccc-ddd")
    plan, reason, flags = _classify(
        [leftover, newer], ["2"], workbook=frozenset(["aaa-bbb"])
    )
    assert plan is None
    assert reason == "flag_manual:leftover_in_workbook"
    assert flags["leftover_in_workbook"] is True


def test_is_billed_or_and_invoice_forces_one():
    kept = _line(20, "2", is_billed=False)
    removed = _line(10, "1", is_billed=True)
    billing = compute_kept_billing_fields(kept, removed, kept_has_invoice_ref=False)
    assert billing["is_billed"] is True

    billing2 = compute_kept_billing_fields(
        _line(20, "2", is_billed=False),
        _line(10, "1", is_billed=False),
        kept_has_invoice_ref=True,
    )
    assert billing2["is_billed"] is True


def test_billable_decision_carried_from_leftover_when_human_decided():
    leftover = _line(
        10,
        "1",
        is_billable=False,
        billable_decided_at="2026-01-01 00:00:00",
    )
    newer = _line(20, "2", is_billable=True)
    billing = compute_kept_billing_fields(newer, leftover, kept_has_invoice_ref=False)
    assert billing["is_billable"] is False


def test_invoice_move_from_leftover_to_newer():
    leftover = _line(10, "1", inv=1)
    newer = _line(20, "2", inv=0)
    plan, _, _ = _classify([leftover, newer], ["2"])
    assert plan is not None
    assert plan.move_invoice_refs is True


# --- EM fixes after the round-2 re-review (U-738) ---------------------------------
from dataclasses import replace as _replace
from unittest.mock import MagicMock

import pytest

from integrations.intuit.qbo.purchase.connector.recode_leftover import (
    public_ids_in_workbook_outbox_payload,
)
from scripts.backfill_merge_recode_leftover_expense_lines import _load_workbook_public_ids


def test_coded_non_holding_leftover_flags_may_be_in_box():
    leftover = _line(10, "1", sub_cost_code_id=55)
    newer = _line(20, "2", sub_cost_code_id=99)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None and reason == "flag_manual:coded_leftover_may_be_in_box"


def test_holding_coded_leftover_still_merges():
    leftover = _line(10, "1", sub_cost_code_id=7)
    newer = _line(20, "2", sub_cost_code_id=99)
    plan, reason, _ = _classify([leftover, newer], ["2"], holding=frozenset({7}))
    assert reason is None and plan is not None


def test_project_mismatch_flags():
    leftover = _replace(_line(10, "1", inv=1), project_id=1)
    newer = _replace(_line(20, "2", sub_cost_code_id=99), project_id=2)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None and reason == "flag_manual:project_mismatch"


def test_conflicting_billable_decisions_flag():
    leftover = _line(10, "1", is_billable=False, billable_decided_at="2026-10-01")
    newer = _line(20, "2", sub_cost_code_id=99, is_billable=True, billable_decided_at="2026-10-20")
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None and reason == "flag_manual:conflicting_billable_decision"


def test_non_dict_payload_is_ignored_not_fatal():
    assert public_ids_in_workbook_outbox_payload("[1, 2, 3]") == set()


def test_workbook_loader_fails_closed_on_query_error_and_skips_bad_rows():
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        _load_workbook_public_ids(cur)

    good = '{"values": [[' + ",".join(['""'] * 25) + ', "ABC-1"]]}'
    cur2 = MagicMock()
    cur2.fetchall.return_value = [MagicMock(Payload="{not json"), MagicMock(Payload=good)]
    assert _load_workbook_public_ids(cur2) == frozenset({"abc-1"})


def test_same_attachment_on_both_lines_merges_and_drops_duplicate_link():
    leftover = _replace(_line(10, "1", att=1), attachment_ids=(500,))
    newer = _replace(_line(20, "2", att=1, sub_cost_code_id=99), attachment_ids=(500,))
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert reason is None and plan is not None
    assert plan.drop_duplicate_attachment_links is True and plan.move_attachments is False


def test_different_attachments_on_both_lines_still_flags():
    leftover = _replace(_line(10, "1", att=1), attachment_ids=(500,))
    newer = _replace(_line(20, "2", att=1, sub_cost_code_id=99), attachment_ids=(501,))
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None and reason == "flag_manual:both_have_attachments"


def test_leftover_with_project_and_newer_without_flags():
    leftover = _replace(_line(10, "1"), project_id=1)
    newer = _replace(_line(20, "2", sub_cost_code_id=99), project_id=None)
    plan, reason, _ = _classify([leftover, newer], ["2"])
    assert plan is None and reason == "flag_manual:project_mismatch"
