"""U-674 — Submit for Review and Complete refuse a Bill line whose cost code is an untouched pre-fill."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_line_item.business.prefill_guard import (
    assert_no_unreviewed_prefill,
    count_unreviewed_prefill,
)
from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import CompletionRefused, UnreviewedPrefillRefused


def _line(state, scc=10):
    return SimpleNamespace(coding_prefill_state=state, sub_cost_code_id=scc, is_draft=True)


# --- the predicate ---------------------------------------------------------- #


def test_only_prefilled_counts_as_unreviewed():
    lines = [_line("prefilled"), _line("accepted"), _line("changed"), _line(None), _line("prefilled")]
    assert count_unreviewed_prefill(lines) == 2


def test_refusal_is_a_422_completion_refusal_with_its_own_code():
    with pytest.raises(UnreviewedPrefillRefused) as exc:
        assert_no_unreviewed_prefill([_line("prefilled")], action="completed")
    assert isinstance(exc.value, CompletionRefused)
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.UNREVIEWED_PREFILL
    assert "1 line still carries" in exc.value.detail


def test_reviewed_lines_pass():
    assert_no_unreviewed_prefill([_line("accepted"), _line("changed"), _line(None)], action="completed")


# --- complete_bill ---------------------------------------------------------- #


def _bill_service(lines):
    from entities.bill.business.service import BillService

    repo = MagicMock()
    repo.read_by_bill_number_and_vendor_id.return_value = None
    svc = BillService(repo=repo)
    bill = SimpleNamespace(id=1, public_id="pub-1", vendor_id=2, bill_number="B-1", bill_date="2026-10-01", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=bill)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v-pub")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = lines
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    return svc, repo


def test_complete_bill_refuses_an_untouched_prefill_before_finalize():
    svc, repo = _bill_service([_line("accepted"), _line("prefilled")])
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code", return_value=False
    ):
        with pytest.raises(UnreviewedPrefillRefused):
            svc.complete_bill("pub-1")
    repo.finalize_by_id.assert_not_called()


def test_completable_lines_reads_the_lines_once_and_still_checks_holding():
    from shared.lifecycle.completion_gate import HoldingCodeRefused

    svc, _repo = _bill_service([_line("accepted")])
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code", return_value=True
    ):
        with pytest.raises(HoldingCodeRefused):
            svc._assert_completable_lines(1)
    svc.bill_line_item_service.read_by_bill_id.assert_called_once()


def test_complete_bill_router_refuses_prefill_before_enqueue():
    from fastapi import BackgroundTasks

    from entities.bill.api.router import complete_bill_router

    svc = MagicMock()
    svc.read_by_public_id.return_value = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc._assert_completable_lines.side_effect = UnreviewedPrefillRefused("x")
    with patch("entities.bill.api.router.BillService", return_value=svc), patch(
        "entities.bill.api.router.gate_completion"
    ), patch("entities.completion_job.business.service.CompletionJobService") as job_cls:
        with pytest.raises(UnreviewedPrefillRefused):
            complete_bill_router(public_id="b1", background_tasks=BackgroundTasks(), current_user={"sub": "u"})
    job_cls.return_value.enqueue.assert_not_called()


def test_background_completion_records_the_refusal_code():
    from entities.bill.api import router

    job_service = MagicMock()
    with patch.object(router, "BillService") as svc_cls, patch.object(router, "BillRepository") as repo_cls, patch(
        "entities.completion_job.business.service.CompletionJobService", return_value=job_service
    ):
        svc_cls.return_value.complete_bill.side_effect = UnreviewedPrefillRefused("nobody reviewed it")
        router._run_complete_bill("b1", "job-1")
    result = repo_cls.return_value.set_completion_result.call_args[0][1]
    assert result["status_code"] == 422
    assert result["errors"][0]["error_code"] == ErrorCode.UNREVIEWED_PREFILL
    job_service.mark_failure.assert_called_once()


# --- submit for review ------------------------------------------------------ #


def _review_service():
    from entities.review.business.service import ReviewService

    svc = ReviewService.__new__(ReviewService)
    svc._resolve_parent = MagicMock(return_value=SimpleNamespace(id=7, status="draft", is_draft=True))
    svc._assert_parent_open = MagicMock()
    svc._get_current_by_id = MagicMock(return_value=None)
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_first_status.return_value = SimpleNamespace(id=1)
    svc._build_payload = MagicMock(return_value={"ok": True})
    return svc


@pytest.mark.parametrize("states, refused", [(["prefilled"], True), (["accepted", "changed", None], False)])
def test_bill_submit_for_review_refuses_untouched_prefill(states, refused):
    from entities.review.business.service import ParentType

    svc = _review_service()
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_bill_id",
        return_value=[_line(s) for s in states],
    ):
        if refused:
            with pytest.raises(UnreviewedPrefillRefused):
                svc.build_submit_payload(parent_type=ParentType.BILL, parent_public_id="b", user_id=1)
            svc._build_payload.assert_not_called()
        else:
            assert svc.build_submit_payload(parent_type=ParentType.BILL, parent_public_id="b", user_id=1) == {"ok": True}


def test_expense_submit_is_not_checked_for_prefill():
    from entities.review.business.service import ParentType

    svc = _review_service()
    with patch("entities.bill_line_item.business.service.BillLineItemService.read_by_bill_id") as read:
        svc.build_submit_payload(parent_type=ParentType.EXPENSE, parent_public_id="e", user_id=1)
    read.assert_not_called()


# --- create-time auto-submit ------------------------------------------------ #


def test_create_refuses_auto_submit_of_a_prefilled_line_before_any_write():
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.attachment_service = MagicMock()
    with pytest.raises(UnreviewedPrefillRefused):
        svc.create(
            user_id=5,
            bill_date="2026-10-01",
            due_date="2026-10-01",
            attachment_public_id="att",
            line_project_public_id="p",
            line_coding_prefill_state="prefilled",
            submit_for_review=None,
        )
    svc.attachment_service.read_by_public_id.assert_not_called()
    svc.repo.create.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"submit_for_review": False},  # Save For Later
        {"user_id": None},  # no auto-submit without a caller
        {"line_project_public_id": None},  # no auto-submit without a project
        {"line_coding_prefill_state": "accepted"},
    ],
)
def test_create_without_an_auto_submit_of_a_prefill_is_not_refused(overrides):
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.side_effect = RuntimeError("reached the write path")
    kwargs = dict(
        user_id=5,
        bill_date="2026-10-01",
        due_date="2026-10-01",
        attachment_public_id="att",
        line_project_public_id="p",
        line_coding_prefill_state="prefilled",
        submit_for_review=None,
    )
    kwargs.update(overrides)
    with pytest.raises(RuntimeError, match="reached the write path"):
        svc.create(**kwargs)


def test_create_router_refuses_before_the_workflow_engine():
    import asyncio

    from entities.bill.api import router
    from entities.bill.api.schemas import BillCreate

    body = BillCreate(
        vendor_public_id="00000000-0000-0000-0000-000000000003",
        bill_number="B-1",
        bill_date="2026-10-01",
        due_date="2026-10-01",
        attachment_public_id="00000000-0000-0000-0000-000000000001",
        line_project_public_id="00000000-0000-0000-0000-000000000002",
        line_coding_prefill_state="prefilled",
    )
    with patch.object(router, "resolve_user_id", return_value=5), patch.object(router, "ProcessEngine") as engine:
        with pytest.raises(UnreviewedPrefillRefused):
            asyncio.run(router.create_bill_router(body=body, current_user={"username": "pat"}))
    engine.assert_not_called()


# --- fix round 1 ------------------------------------------------------------ #


@pytest.mark.parametrize("new_scc, expected", [(10, "accepted"), (22, "changed")])
def test_r1_reviewer_recode_settles_an_untouched_prefill(new_scc, expected):
    from entities.bill_line_item.business.service import BillLineItemService

    svc = BillLineItemService.__new__(BillLineItemService)
    svc.repo = MagicMock()
    svc.repo.update_by_id.side_effect = lambda li, **_: li
    line = SimpleNamespace(coding_prefill_state="prefilled", sub_cost_code_id=10, row_version=None, description="d")
    out = svc.update_coding_in_transaction(
        line_item=line, row_version="rv", sub_cost_code_id=new_scc, description=None, conn=object()
    )
    assert out.coding_prefill_state == expected
    assert out.sub_cost_code_id == new_scc


@pytest.mark.parametrize("state", [None, "accepted", "changed"])
def test_r1_reviewer_recode_leaves_other_states_alone(state):
    from entities.bill_line_item.business.service import BillLineItemService

    svc = BillLineItemService.__new__(BillLineItemService)
    svc.repo = MagicMock()
    svc.repo.update_by_id.side_effect = lambda li, **_: li
    line = SimpleNamespace(coding_prefill_state=state, sub_cost_code_id=10, row_version=None, description="d")
    out = svc.update_coding_in_transaction(
        line_item=line, row_version="rv", sub_cost_code_id=22, description=None, conn=object()
    )
    assert out.coding_prefill_state == state


def test_r1_complete_router_checks_lines_before_the_gate_can_record_an_approval():
    from fastapi import BackgroundTasks

    from entities.bill.api.router import complete_bill_router

    svc = MagicMock()
    svc.read_by_public_id.return_value = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc._assert_completable_lines.side_effect = UnreviewedPrefillRefused("x")
    with patch("entities.bill.api.router.BillService", return_value=svc), patch(
        "entities.bill.api.router.gate_completion"
    ) as gate:
        with pytest.raises(UnreviewedPrefillRefused):
            complete_bill_router(public_id="b1", background_tasks=BackgroundTasks(), current_user={"sub": "u"})
    gate.assert_not_called()


def test_r1_bill_advance_refuses_untouched_prefill():
    from entities.review.business.service import ParentType

    svc = _review_service()
    svc._get_current_by_id = MagicMock(
        return_value=SimpleNamespace(status_is_final=False, status_is_declined=False, status_sort_order=1)
    )
    svc.review_status_service.get_next_status.return_value = SimpleNamespace(id=3, is_final=True, is_declined=False)
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_bill_id",
        return_value=[_line("prefilled")],
    ), patch("entities.review.business.service.refuse_if_parent_has_holding_line"):
        with pytest.raises(UnreviewedPrefillRefused):
            svc.build_advance_payload(parent_type=ParentType.BILL, parent_public_id="b", user_id=1)
    svc._build_payload.assert_not_called()


def test_r1_redrive_of_a_completed_bill_skips_the_prefill_check():
    svc, _repo = _bill_service([_line("prefilled")])
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code", return_value=False
    ):
        svc._assert_completable_lines(1, check_prefill=False)
        with pytest.raises(UnreviewedPrefillRefused):
            svc._assert_completable_lines(1)


def test_r1_complete_bill_passes_draft_status_to_the_line_check():
    svc, _repo = _bill_service([_line("prefilled")])
    svc.read_by_public_id.return_value.is_draft = False
    svc._assert_completable_lines = MagicMock(side_effect=RuntimeError("stop after the check"))
    svc.complete_bill("pub-1")
    svc._assert_completable_lines.assert_called_once_with(1, check_prefill=False)
