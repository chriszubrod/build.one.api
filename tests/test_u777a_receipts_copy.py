"""
U-777a — the 520 receipts copy is its own per-expense outbox kind
(`expense_receipts_copy`), enqueued by the QBO pull and by complete_expense.
Pure mocks: no DB, Graph or QBO.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.base.errors import MsServerError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_EXPENSE_RECEIPTS_COPY,
    MsOutboxService,
)
from integrations.ms.outbox.business.worker import MsOutboxWorker
from tests.test_ms_outbox_coalesce_discrimination import _FakeMsOutboxRepo
from tests.test_u777_pull_fanout_receipts import _run_tick, _line, _sx

OK_RECEIPTS = {"success": True, "synced_count": 2, "skipped_count": 1, "errors": [], "deferred_holding": False}


# --------------------------------------------------------------------------- #
# enqueue
# --------------------------------------------------------------------------- #

@pytest.fixture
def svc_repo():
    repo = _FakeMsOutboxRepo()
    svc = MsOutboxService(repo=repo)
    with patch("integrations.ms.outbox.business.service._resolve_tenant_id", return_value="tenant-abc"), patch(
        "integrations.ms.outbox.business.service._writes_allowed", return_value=True
    ):
        yield svc, repo


def test_enqueue_row_shape(svc_repo):
    svc, repo = svc_repo
    row = svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    assert row is not None and len(repo.rows) == 1
    assert row.kind == KIND_EXPENSE_RECEIPTS_COPY == "expense_receipts_copy"
    assert row.entity_type == "Expense"
    assert row.entity_public_id == "exp-1"
    assert row.tenant_id == "tenant-abc"
    assert json.loads(row.payload) == {}


def test_enqueue_returns_none_when_gate_refuses():
    repo = _FakeMsOutboxRepo()
    with patch("integrations.ms.outbox.business.service._resolve_tenant_id", return_value="t"), patch(
        "integrations.ms.outbox.business.service._writes_allowed", return_value=False
    ):
        assert MsOutboxService(repo=repo).enqueue_expense_receipts_copy(expense_public_id="exp-1") is None
    assert repo.rows == []


def test_enqueue_returns_none_without_tenant():
    repo = _FakeMsOutboxRepo()
    with patch("integrations.ms.outbox.business.service._resolve_tenant_id", return_value=None):
        assert MsOutboxService(repo=repo).enqueue_expense_receipts_copy(expense_public_id="exp-1") is None
    assert repo.rows == []


def test_second_enqueue_while_pending_coalesces(svc_repo):
    svc, repo = svc_repo
    first = svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    second = svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    assert len(repo.rows) == 1
    assert second.id == first.id
    assert len(repo.ready_after_calls) == 1  # debounce window refreshed


def test_different_expenses_do_not_coalesce(svc_repo):
    svc, repo = svc_repo
    svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    svc.enqueue_expense_receipts_copy(expense_public_id="exp-2")
    assert len(repo.rows) == 2


def test_enqueue_after_the_pending_row_was_claimed_creates_a_new_row(svc_repo):
    svc, repo = svc_repo
    first = svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    first.status = "in_progress"  # claimed by the drain: no longer pending/failed
    svc.enqueue_expense_receipts_copy(expense_public_id="exp-1")
    assert len(repo.rows) == 2


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #

def _row(public_id="exp-pub-1"):
    return MsOutbox(
        id=1, public_id="outbox-1", row_version="rv", kind=KIND_EXPENSE_RECEIPTS_COPY,
        entity_type="Expense", entity_public_id=public_id, tenant_id="t", request_id="r",
        payload="{}", status="in_progress", attempts=0, ready_after=None, correlation_id=None,
    )


def _expense(status="completed"):
    return SimpleNamespace(id=7, public_id="exp-pub-1", status=status)


def _run(*, expense=..., lines=None, result=OK_RECEIPTS, side_effect=None, read_error=None, lines_error=None):
    svc = MagicMock()
    if read_error is not None:
        svc.read_by_public_id.side_effect = read_error
    else:
        svc.read_by_public_id.return_value = _expense() if expense is ... else expense
    if side_effect is not None:
        svc._upload_to_general_receipts_folder.side_effect = side_effect
    else:
        svc._upload_to_general_receipts_folder.return_value = result
    eli = MagicMock()
    if lines_error is not None:
        eli.read_by_expense_id.side_effect = lines_error
    else:
        eli.read_by_expense_id.return_value = lines if lines is not None else []
    with patch("entities.expense.business.service.ExpenseService", return_value=svc), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService", return_value=eli
    ):
        MsOutboxWorker(repo=MagicMock())._handle_expense_receipts_copy(_row(), {})
    return svc, eli


def test_handler_is_registered_in_the_dispatch_table():
    w = MsOutboxWorker(repo=MagicMock())
    assert w._dispatch_table[KIND_EXPENSE_RECEIPTS_COPY] == w._handle_expense_receipts_copy


def test_completed_expense_uploads_with_all_lines():
    ls = [_line(10), _line(None), _line(20)]
    exp = _expense()
    svc, eli = _run(expense=exp, lines=ls)
    svc._upload_to_general_receipts_folder.assert_called_once_with(expense=exp, line_items=ls)
    eli.read_by_expense_id.assert_called_once_with(expense_id=7)


@pytest.mark.parametrize("status", ["draft", "in_review", "approved", None])
def test_not_completed_is_done_without_upload(status):
    svc, _ = _run(expense=_expense(status))
    svc._upload_to_general_receipts_folder.assert_not_called()


def test_missing_expense_is_done_without_upload():
    svc, _ = _run(expense=None)
    svc._upload_to_general_receipts_folder.assert_not_called()


def test_deferred_holding_does_not_raise():
    deferred = {"success": True, "synced_count": 0, "skipped_count": 0, "errors": [], "deferred_holding": True}
    svc, _ = _run(result=deferred)
    svc._upload_to_general_receipts_folder.assert_called_once()


def _assert_label_only(exc, secret):
    assert exc.is_retryable is True
    assert "receipts:failed expense_public_id=exp-pub-1" in str(exc)
    assert secret not in str(exc)


def test_errors_list_is_retryable_label_only():
    bad = {"success": False, "synced_count": 0, "skipped_count": 0, "errors": [{"error": "SECRET graph text"}]}
    with pytest.raises(MsServerError) as ei:
        _run(result=bad)
    _assert_label_only(ei.value, "SECRET")


def test_success_false_with_empty_errors_is_retryable():
    bad = {"success": False, "message": "SECRET navigate failed", "synced_count": 0, "skipped_count": 0, "errors": []}
    with pytest.raises(MsServerError) as ei:
        _run(result=bad)
    _assert_label_only(ei.value, "SECRET")


def test_exception_is_retryable_label_only_and_message_is_logged(caplog):
    with caplog.at_level("WARNING"), pytest.raises(MsServerError) as ei:
        _run(side_effect=RuntimeError("SECRET graph down"))
    _assert_label_only(ei.value, "SECRET")
    rec = next(r for r in caplog.records if r.getMessage() == "ms.outbox.expense_receipts_copy.exception")
    assert rec.error_message == "SECRET graph down"


def test_transient_db_error_on_expense_read_is_retryable():
    with pytest.raises(MsServerError) as ei:
        _run(read_error=RuntimeError("SECRET driver"))
    assert ei.value.is_retryable is True
    assert "SECRET" not in str(ei.value)


def test_transient_db_error_on_lines_read_is_retryable_and_skips_upload():
    with pytest.raises(MsServerError) as ei:
        _run(lines_error=RuntimeError("SECRET driver"))
    assert ei.value.is_retryable is True
    assert "SECRET" not in str(ei.value)


# --------------------------------------------------------------------------- #
# fanout handler no longer does receipts
# --------------------------------------------------------------------------- #

def test_pull_fanout_handler_no_longer_uploads_receipts():
    from tests.test_u777_pull_fanout_receipts import _run_handler

    e = SimpleNamespace(id=1, public_id="exp-pub-1", status="completed")
    svc = _run_handler(project_id=10, expenses=[e], lines={1: [_line(10)]})
    svc._upload_to_general_receipts_folder.assert_not_called()
    svc.sync_expenses_batch_to_excel.assert_called_once()
    svc._upload_attachments_to_module_folder.assert_called_once()


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #

def _receipts_ids(outbox):
    return [c.kwargs["expense_public_id"] for c in outbox.enqueue_expense_receipts_copy.call_args_list]


def test_completed_expense_without_any_project_gets_a_receipts_copy():
    result, outbox = _run_tick(changed=[("q-1", _sx(1))], lines_by_expense={1: [_line(None)]})
    assert _receipts_ids(outbox) == ["exp-pub-1"]
    outbox.enqueue_expense_pull_fanout.assert_not_called()
    assert result["receipts_copy_enqueued"] == 1


def test_draft_gets_no_receipts_copy():
    result, outbox = _run_tick(changed=[("q-1", _sx(1, "draft"))], lines_by_expense={1: [_line(10)]})
    assert _receipts_ids(outbox) == []
    assert result["receipts_copy_enqueued"] == 0


def test_multi_project_expense_gets_exactly_one_receipts_copy():
    result, outbox = _run_tick(
        changed=[("q-1", _sx(1))], late_linked={"q-1": _sx(1)},
        lines_by_expense={1: [_line(10), _line(20), _line(None)]},
    )
    assert _receipts_ids(outbox) == ["exp-pub-1"]
    assert outbox.enqueue_expense_pull_fanout.call_count == 2
    assert result["receipts_copy_enqueued"] == 1


def test_empty_tick_late_linked_completed_expense_gets_receipts_copy_and_counts():
    result, outbox = _run_tick(late_linked={"q-old": _sx(500)}, lines_by_expense={500: [_line(None)]})
    assert _receipts_ids(outbox) == ["exp-pub-500"]
    assert result["purchases_synced"] == 0
    assert (result["receipts_copy_enqueued"], result["receipts_copy_refused"], result["receipts_copy_failed"]) == (1, 0, 0)


def test_refused_receipts_enqueue_is_counted():
    result, outbox = _run_tick(
        changed=[("q-1", _sx(1))], lines_by_expense={1: [_line(10)]},
        receipts_result=None,
    )
    assert (result["receipts_copy_enqueued"], result["receipts_copy_refused"], result["receipts_copy_failed"]) == (0, 1, 0)
    assert result["ms_fanout_enqueued"] == 1


def test_receipts_enqueue_failure_is_isolated_from_fanout_and_other_expenses():
    outcomes = {"exp-pub-1": RuntimeError("boom"), "exp-pub-2": MagicMock()}

    def _enq(expense_public_id):
        r = outcomes[expense_public_id]
        if isinstance(r, Exception):
            raise r
        return r

    result, outbox = _run_tick(
        changed=[("q-1", _sx(1)), ("q-2", _sx(2))],
        lines_by_expense={1: [_line(10)], 2: [_line(10)]},
        receipts_side_effect=_enq,
    )
    assert _receipts_ids(outbox) == ["exp-pub-1", "exp-pub-2"]
    assert (result["receipts_copy_enqueued"], result["receipts_copy_failed"]) == (1, 1)
    assert result["ms_fanout_enqueued"] == 1
    assert sorted(outbox.enqueue_expense_pull_fanout.call_args.kwargs["expense_public_ids"]) == ["exp-pub-1", "exp-pub-2"]


# --------------------------------------------------------------------------- #
# complete_expense step 3b
# --------------------------------------------------------------------------- #

def _complete_svc():
    from entities.expense.business.service import ExpenseService

    exp = SimpleNamespace(id=55, public_id="exp-pub-55", status="draft", is_draft=True, vendor_id=9,
                          qbo_id=None, realm_id=None)
    repo = MagicMock()
    repo.finalize_by_id.return_value = SimpleNamespace(**{**exp.__dict__, "is_draft": False, "status": "completed"})
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=exp)
    svc.vendor_service = MagicMock()
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    svc._upload_to_general_receipts_folder = MagicMock(return_value={"errors": []})
    svc._enqueue_box_uploads = MagicMock()
    svc._enqueue_box_excel = MagicMock()
    svc._enqueue_coding_recode_on_complete = MagicMock(return_value={})
    return svc


def test_complete_expense_step_3b_enqueues_instead_of_uploading_inline():
    svc = _complete_svc()
    with patch("integrations.ms.outbox.business.service.MsOutboxService") as outbox_cls:
        outbox_cls.return_value.enqueue_expense_receipts_copy.return_value = MagicMock()
        result = svc.complete_expense(public_id="exp-pub-55")
    svc._upload_to_general_receipts_folder.assert_not_called()
    outbox_cls.return_value.enqueue_expense_receipts_copy.assert_called_once_with(expense_public_id="exp-pub-55")
    assert result["status_code"] == 200
    assert result["errors"] == []
    assert result["receipts_upload"]["queued"] is True


def test_complete_expense_refused_receipts_enqueue_surfaces_as_error_entry():
    svc = _complete_svc()
    with patch("integrations.ms.outbox.business.service.MsOutboxService") as outbox_cls:
        outbox_cls.return_value.enqueue_expense_receipts_copy.return_value = None
        result = svc.complete_expense(public_id="exp-pub-55")
    assert result["status_code"] == 207
    assert len(result["errors"]) == 1
    assert result["receipts_upload"]["success"] is False


def test_complete_expense_receipts_enqueue_exception_does_not_abort_completion():
    svc = _complete_svc()
    with patch("integrations.ms.outbox.business.service.MsOutboxService") as outbox_cls:
        outbox_cls.return_value.enqueue_expense_receipts_copy.side_effect = RuntimeError("db down")
        result = svc.complete_expense(public_id="exp-pub-55")
    assert result["expense_finalized"] is True
    assert result["status_code"] == 207
    svc._enqueue_box_uploads.assert_called_once()  # later steps still ran


# --------------------------------------------------------------------------- #
# Round 1 — F1: the persisted fanout row owns the receipts-copy enqueue
# --------------------------------------------------------------------------- #

from tests.test_u777_pull_fanout_receipts import _run_handler, _expense as _fx  # noqa: E402


def test_legacy_fanout_row_enqueues_a_receipts_copy_for_a_completed_expense():
    e = _fx(1)
    svc = _run_handler(project_id=10, expenses=[e], lines={1: [_line(10)]})
    svc.outbox.enqueue_expense_receipts_copy.assert_called_once_with(expense_public_id="exp-pub-1")
    svc._upload_to_general_receipts_folder.assert_not_called()


def test_fanout_row_enqueues_no_receipts_copy_for_a_draft():
    svc = _run_handler(project_id=10, expenses=[_fx(1, "draft")], lines={1: [_line(10)]})
    svc.outbox.enqueue_expense_receipts_copy.assert_not_called()
    svc.sync_expenses_batch_to_excel.assert_called_once()


def test_refused_receipts_enqueue_is_a_retryable_labeled_error_and_others_still_run():
    e1, e2 = _fx(1), _fx(2)
    seen = []

    def _enq(expense_public_id):
        seen.append(expense_public_id)
        return None if expense_public_id == "exp-pub-1" else MagicMock()

    with pytest.raises(MsServerError, match="receipts:failed expense_public_id=exp-pub-1") as ei:
        _run_handler(project_id=10, expenses=[e1, e2], lines={1: [_line(10)], 2: [_line(10)]},
                     receipts_enqueue=_enq)
    assert seen == ["exp-pub-1", "exp-pub-2"]
    assert ei.value.is_retryable is True
    assert "exp-pub-2" not in str(ei.value)


def test_raising_receipts_enqueue_is_label_only_retryable_and_isolated():
    e1, e2 = _fx(1), _fx(2)
    seen = []

    def _enq(expense_public_id):
        seen.append(expense_public_id)
        if expense_public_id == "exp-pub-1":
            raise RuntimeError("SECRET db text")
        return MagicMock()

    with pytest.raises(MsServerError) as ei:
        _run_handler(project_id=10, expenses=[e1, e2], lines={1: [_line(10)], 2: [_line(10)]},
                     receipts_enqueue=_enq)
    assert seen == ["exp-pub-1", "exp-pub-2"]
    assert "receipts:failed expense_public_id=exp-pub-1" in str(ei.value)
    assert "SECRET" not in str(ei.value)


def test_receipts_enqueue_failure_does_not_stop_excel_and_module_work():
    with pytest.raises(MsServerError):
        _run_handler(project_id=10, expenses=[_fx(1)], lines={1: [_line(10)]},
                     receipts_enqueue=lambda expense_public_id: None)
    svc = _run_handler.last
    svc.sync_expenses_batch_to_excel.assert_called_once()
    svc._upload_attachments_to_module_folder.assert_called_once()


# --------------------------------------------------------------------------- #
# Round 1 — F2: status for the receipts enqueue is read AFTER attachment linking
# --------------------------------------------------------------------------- #

def test_changed_purchase_projected_draft_but_completed_after_link_gets_receipts_copy():
    result, outbox = _run_tick(
        changed=[("q-1", _sx(1, "draft"))], reread={"q-1": _sx(1, "completed")},
        lines_by_expense={1: [_line(10)]},
    )
    assert _receipts_ids(outbox) == ["exp-pub-1"]
    assert result["receipts_copy_enqueued"] == 1


def test_changed_purchase_projected_completed_but_draft_after_link_gets_none():
    result, outbox = _run_tick(
        changed=[("q-1", _sx(1, "completed"))], reread={"q-1": _sx(1, "draft")},
        lines_by_expense={1: [_line(10)]},
    )
    assert _receipts_ids(outbox) == []


def test_reread_failure_after_link_falls_back_to_projected_object(caplog):
    # Projected object is completed; the post-link re-read raises -> use it, log error_type only.
    def _boom(*a, **k):
        raise RuntimeError("SECRET driver")

    cap = {}
    with caplog.at_level("WARNING"):
        result, outbox = _run_tick(
            changed=[("q-1", _sx(1, "completed"))], lines_by_expense={1: [_line(10)]},
            capture=cap, identity_fail_on_second=_boom,
        )
    assert _receipts_ids(outbox) == ["exp-pub-1"]
    rec = next(r for r in caplog.records if r.getMessage() == "qbo.purchase.receipts_copy_reread_failed")
    assert rec.error_type == "RuntimeError"
    assert "SECRET" not in " ".join(r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Round 1 — F3: a project-less expense has no fanout row to retry it -> hold
# --------------------------------------------------------------------------- #

def _qx(eid, status="completed"):
    return SimpleNamespace(id=eid, public_id=f"exp-pub-{eid}", status=status, qbo_id=f"q-{eid}")


def test_project_less_expense_whose_receipts_enqueue_is_refused_holds_the_watermark():
    cap = {}
    result, _ = _run_tick(
        changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(None)]},
        receipts_result=None, capture=cap,
    )
    outcome = cap["outcome"]
    assert outcome.should_hold is True
    assert outcome.projection_failed_ids == ["q-1"]
    assert result["receipts_copy_refused"] == 1


def test_project_less_expense_whose_receipts_enqueue_raises_holds_the_watermark():
    cap = {}
    _run_tick(
        changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(None)]},
        receipts_side_effect=RuntimeError("boom"), capture=cap,
    )
    assert cap["outcome"].should_hold is True
    assert cap["outcome"].projection_failed_ids == ["q-1"]


def test_project_bound_expense_receipts_failure_does_not_hold_the_watermark():
    cap = {}
    _run_tick(
        changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(10)]},
        receipts_side_effect=RuntimeError("boom"), capture=cap,
    )
    assert cap["outcome"].should_hold is False
    assert cap["outcome"].projection_failed_ids == []


def test_project_less_late_linked_expense_on_an_empty_tick_holds_on_failure():
    cap = {}
    _run_tick(
        late_linked={"q-old": _qx(500)}, lines_by_expense={500: [_line(None)]},
        receipts_result=None, capture=cap,
    )
    assert cap["outcome"].should_hold is True


def test_project_less_expense_with_successful_enqueue_does_not_hold():
    cap = {}
    _run_tick(changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(None)]}, capture=cap)
    assert cap["outcome"].should_hold is False


# --------------------------------------------------------------------------- #
# Round 2 — G1/G2/G3: hold logic
# --------------------------------------------------------------------------- #

def _boom_identity(*a, **k):
    raise RuntimeError("SECRET driver")


def test_g1_project_bound_expense_with_refused_fanout_and_refused_receipts_holds():
    cap = {}
    _run_tick(changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(10)]},
              outbox_result=None, receipts_result=None, capture=cap)
    assert cap["outcome"].should_hold is True
    assert cap["outcome"].projection_failed_ids == ["q-1"]


def test_g1_project_bound_expense_with_missing_project_and_refused_receipts_holds():
    cap = {}
    _run_tick(changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(10)]},
              project_missing=True, receipts_result=None, capture=cap)
    assert cap["outcome"].should_hold is True


def test_g1_covered_by_a_successful_fanout_row_does_not_hold():
    cap = {}
    _run_tick(changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(10)]},
              receipts_result=None, capture=cap)
    assert cap["outcome"].should_hold is False


def test_g1_one_covered_project_is_enough_when_another_project_is_refused():
    cap = {}
    calls = []

    def _fan(**kw):
        calls.append(kw["project_id"])
        return None if kw["project_id"] == 10 else MagicMock()

    # project 10 refused, project 20 enqueued -> a persisted row still owns the expense
    result, outbox = _run_tick(changed=[("q-1", _qx(1))], lines_by_expense={1: [_line(10), _line(20)]},
                               receipts_result=None, capture=cap, fanout_side_effect=_fan)
    assert sorted(calls) == [10, 20]
    assert cap["outcome"].should_hold is False


def test_g2_failed_post_link_reread_holds_even_for_a_project_less_draft():
    cap = {}
    _run_tick(changed=[("q-1", _sx(1, "draft"))], lines_by_expense={1: [_line(None)]},
              identity_fail_on_second=_boom_identity, capture=cap)
    assert cap["outcome"].should_hold is True
    assert cap["outcome"].projection_failed_ids == ["q-1"]


def test_g2_reread_returning_none_holds():
    cap = {}
    _run_tick(changed=[("q-1", _sx(1, "completed"))], lines_by_expense={1: [_line(10)]},
              identity_none_on_second=True, capture=cap)
    assert cap["outcome"].should_hold is True
    assert cap["outcome"].projection_failed_ids == ["q-1"]


def test_g3_identity_less_expense_records_the_purchase_qbo_id_once():
    cap = {}
    _run_tick(changed=[("q-1", _sx(1))], lines_by_expense={1: [_line(None)]},
              identity_fail_on_second=_boom_identity, receipts_result=None, capture=cap)
    assert cap["outcome"].projection_failed_ids == ["q-1"]


def test_g3_late_linked_identity_less_expense_records_the_iterated_purchase_qbo_id():
    cap = {}
    _run_tick(late_linked={"q-old": _sx(500)}, lines_by_expense={500: [_line(None)]},
              receipts_result=None, capture=cap)
    assert cap["outcome"].projection_failed_ids == ["q-old"]


def test_g3_no_purchase_id_anywhere_uses_a_namespaced_token_and_logs(caplog):
    from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
    from scripts.sync_qbo_purchase import _hold_watermark_for_unretryable_receipts

    outcome = SyncOutcome.for_service_pull()
    e = SimpleNamespace(id=77, public_id="exp-pub-77", status="completed")
    with caplog.at_level("WARNING"):
        _hold_watermark_for_unretryable_receipts(outcome, [(e, 77)], {})
    assert outcome.projection_failed_ids == ["expense:77"]
    assert outcome.should_hold is True
    assert any(r.getMessage() == "qbo.purchase.receipts_hold_without_purchase_id" for r in caplog.records)
