"""
U-777 — a late-attached receipt gets a fan-out. (U-777a moved the
`520 - Current Receipts` copy out of the fan-out handler into its own kind; see
test_u777a_receipts_copy.py.) Pure mocks: no DB, Graph or QBO.
"""
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.ms.base.errors import MsServerError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import KIND_EXPENSE_PULL_FANOUT
from integrations.ms.outbox.business.worker import MsOutboxWorker

REALM_ID = "realm-test"
OK_EXCEL = {"success": True, "synced_count": 1, "errors": []}
OK_SP = {"success": True, "synced_count": 1, "skipped_count": 0, "errors": []}


def _expense(eid, status="completed"):
    return SimpleNamespace(id=eid, public_id=f"exp-pub-{eid}", status=status)


def _line(project_id):
    return SimpleNamespace(project_id=project_id)


def _row(payload):
    return MsOutbox(
        id=1, public_id="outbox-1", row_version="rv", kind=KIND_EXPENSE_PULL_FANOUT,
        entity_type="Project", entity_public_id="proj-pub", tenant_id="t", request_id="r",
        payload=json.dumps(payload), status="in_progress", attempts=0,
        ready_after=None, correlation_id=None,
    )


_DEFAULT_ENQUEUE = object()


def _run_handler(*, project_id, expenses, lines, receipts_enqueue=_DEFAULT_ENQUEUE):
    svc = MagicMock()
    by_pub = {e.public_id: e for e in expenses}
    svc.read_by_public_id.side_effect = lambda public_id: by_pub.get(public_id)
    svc.sync_expenses_batch_to_excel.return_value = OK_EXCEL
    svc._upload_attachments_to_module_folder.return_value = OK_SP
    eli = MagicMock()
    eli.read_by_expense_id.side_effect = lambda expense_id: lines.get(expense_id, [])
    payload = {"project_id": project_id, "expense_public_ids": [e.public_id for e in expenses]}
    outbox = MagicMock()
    if receipts_enqueue is not _DEFAULT_ENQUEUE:
        outbox.enqueue_expense_receipts_copy.side_effect = receipts_enqueue
    _run_handler.last = svc  # survives a raising handler
    svc.outbox = outbox  # U-777a: the handler enqueues a receipts copy per completed expense
    with patch("entities.expense.business.service.ExpenseService", return_value=svc), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService", return_value=eli
    ), patch("integrations.ms.outbox.business.service.MsOutboxService", return_value=outbox):
        MsOutboxWorker(repo=MagicMock())._handle_expense_pull_fanout(_row(payload), payload)
    return svc


# --------------------------------------------------------------------------- #
# sync_qbo_purchase — late-attached receipts get a fan-out
# --------------------------------------------------------------------------- #

object_default = object()


def _run_tick(*, changed=(), late_linked=None, link_returns=1, lines_by_expense=None,
              outbox_result=object_default, reread=None, box=False, capture=None,
              receipts_result=object_default, receipts_side_effect=None, identity_fail_on_second=None, project_missing=False,
              identity_none_on_second=False, fanout_side_effect=None):
    """changed: list of (qbo_id, expense) the main loop projects. late_linked: dict qbo_id -> expense
    for UNCHANGED purchases with a new attachable."""
    from scripts.sync_qbo_purchase import sync_qbo_to_local

    late_linked = late_linked or {}
    lines_by_expense = lines_by_expense or {}
    purchases = [SimpleNamespace(id=i + 1, qbo_id=q, total_amt=1, to_dict=dict) for i, (q, _) in enumerate(changed)]
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = purchases
    qps = MagicMock()
    qps.sync_from_qbo.return_value = outcome
    connector = MagicMock()
    connector.sync_from_qbo_purchase.side_effect = [e for _, e in changed]

    attachable = MagicMock()
    attachable.entity_ids_in_snapshot.return_value = set(late_linked) | {q for q, _ in changed}
    attachable.sync_attachables_for_purchase.return_value = [MagicMock()]

    exp_svc = MagicMock()
    projected_by_qbo = dict(changed)
    reads: dict = {}

    def _read_identity(qbo_id, realm):
        # `reread`: qbo_id -> expense returned from the SECOND read of that purchase (post-link).
        reads[qbo_id] = reads.get(qbo_id, 0) + 1
        if identity_fail_on_second is not None and reads[qbo_id] >= 2:
            identity_fail_on_second()
        if identity_none_on_second and reads[qbo_id] >= 2:
            return None
        if reread and qbo_id in reread and reads[qbo_id] >= 2:
            return reread[qbo_id]
        if reads[qbo_id] >= 2 and qbo_id in projected_by_qbo:
            return projected_by_qbo[qbo_id]  # post-link re-read of a changed purchase succeeds by default
        return late_linked.get(qbo_id)

    exp_svc.read_by_qbo_identity.side_effect = _read_identity
    eli = MagicMock()
    eli.read_by_expense_id.side_effect = lambda expense_id: lines_by_expense.get(expense_id, [])
    proj = MagicMock()
    proj.read_by_id.side_effect = lambda pid: None if project_missing else SimpleNamespace(id=pid, public_id=f"proj-pub-{pid}")
    outbox = MagicMock()
    outbox.enqueue_expense_pull_fanout.return_value = MagicMock() if outbox_result is object_default else outbox_result
    if fanout_side_effect is not None:
        outbox.enqueue_expense_pull_fanout.side_effect = fanout_side_effect
    if receipts_side_effect is not None:
        outbox.enqueue_expense_receipts_copy.side_effect = receipts_side_effect
    elif receipts_result is not object_default:
        outbox.enqueue_expense_receipts_copy.return_value = receipts_result
    box_outbox, box_wb = MagicMock(), MagicMock()
    box_wb.read_by_project_id.return_value = {"box_file_id": "bf", "worksheet_name": "DETAILS"}
    box_outbox.enqueue_box_excel_batch.return_value = True
    if capture is not None:
        capture.update(exp_svc=exp_svc, box_outbox=box_outbox, outcome=outcome)

    with patch("integrations.box.outbox.business.service.BoxOutboxService", return_value=box_outbox), patch(
        "integrations.box.excel.business.mapping_service.BoxProjectWorkbookService", return_value=box_wb
    ), patch("scripts.sync_qbo_purchase.QboAttachableService", return_value=attachable), patch(
        "entities.expense.business.service.ExpenseService", return_value=exp_svc
    ), patch("entities.expense_line_item.business.service.ExpenseLineItemService", return_value=eli), patch(
        "integrations.ms.outbox.business.service.MsOutboxService", return_value=outbox
    ), patch("entities.project.business.service.ProjectService", return_value=proj), patch(
        "scripts.sync_qbo_purchase.read_lines_riding_out_race", return_value=[MagicMock()]
    ), patch("scripts.sync_qbo_purchase.with_retry", side_effect=lambda fn, *a, **k: fn(*a)), patch(
        "scripts.sync_qbo_purchase.sync_purchase_attachments_to_expense_line_items", return_value=link_returns
    ), patch("scripts.sync_qbo_purchase.pace_batch"), patch.dict("os.environ", {"ALLOW_BOX_WRITES": "true" if box else "false"}):
        result, _ = sync_qbo_to_local(
            realm_id=REALM_ID, last_sync_time="2026-10-03T11:59:00+00:00",
            qbo_purchase_service=qps, purchase_connector=connector,
        )
    return result, outbox


def _sx(eid, status="completed"):
    return SimpleNamespace(id=eid, public_id=f"exp-pub-{eid}", status=status)


def _enqueued(outbox):
    return {c.kwargs["project_id"]: c.kwargs["expense_public_ids"]
            for c in outbox.enqueue_expense_pull_fanout.call_args_list}


def test_late_linked_completed_expense_on_empty_tick_is_fanned_out_per_project():
    e = _sx(500)
    result, outbox = _run_tick(
        late_linked={"q-old": e}, lines_by_expense={500: [_line(10), _line(20), _line(None)]},
    )
    assert _enqueued(outbox) == {10: ["exp-pub-500"], 20: ["exp-pub-500"]}
    assert result["ms_fanout_enqueued"] == 2
    assert result["purchases_synced"] == 0


def test_late_linked_draft_is_not_enqueued():
    result, outbox = _run_tick(late_linked={"q-old": _sx(500, "draft")}, lines_by_expense={500: [_line(10)]})
    outbox.enqueue_expense_pull_fanout.assert_not_called()
    assert result["ms_fanout_enqueued"] == 0


def test_changed_and_late_linked_expense_appears_once():
    e = _sx(500)
    result, outbox = _run_tick(
        changed=[("q-1", e)], late_linked={"q-1": e}, lines_by_expense={500: [_line(10)]},
    )
    assert _enqueued(outbox) == {10: ["exp-pub-500"]}
    assert outbox.enqueue_expense_pull_fanout.call_count == 1


def test_late_linked_merges_into_the_same_project_row_as_a_changed_expense():
    changed, late = _sx(1), _sx(500)
    _, outbox = _run_tick(
        changed=[("q-1", changed)], late_linked={"q-old": late},
        lines_by_expense={1: [_line(10)], 500: [_line(10)]},
    )
    assert outbox.enqueue_expense_pull_fanout.call_count == 1
    assert sorted(_enqueued(outbox)[10]) == ["exp-pub-1", "exp-pub-500"]


def test_already_linked_receipt_is_still_fanned_out_on_a_replayed_tick():
    """F1: the link was committed on an aborted tick; the replay's link call returns 0."""
    result, outbox = _run_tick(
        late_linked={"q-old": _sx(500)}, link_returns=0, lines_by_expense={500: [_line(10)]},
    )
    assert _enqueued(outbox) == {10: ["exp-pub-500"]}
    assert result["ms_fanout_enqueued"] == 1
    assert result["attachments_linked"] == 0


def test_status_is_taken_from_a_post_link_reread():
    """F3: completed concurrently while the receipt was being linked."""
    result, outbox = _run_tick(
        late_linked={"q-old": _sx(500, "draft")}, reread={"q-old": _sx(500, "completed")},
        lines_by_expense={500: [_line(10)]},
    )
    assert _enqueued(outbox) == {10: ["exp-pub-500"]}


def test_reread_failure_falls_back_to_the_pre_link_object(caplog):
    from scripts.sync_qbo_purchase import _link_attachables_for_unchanged_purchases
    att = MagicMock()
    att.entity_ids_in_snapshot.return_value = {"q-old"}
    pre = _sx(500)
    calls = []

    def _read(qbo_id, realm):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("secret driver text")
        return pre

    svc = MagicMock()
    svc.read_by_qbo_identity.side_effect = _read
    with patch("scripts.sync_qbo_purchase.sync_purchase_attachments_to_expense_line_items", return_value=0), \
            patch("scripts.sync_qbo_purchase._sync_and_link_purchase_attachables", return_value=0), \
            caplog.at_level(logging.WARNING):
        _, fanout = _link_attachables_for_unchanged_purchases(
            realm_id=REALM_ID, attachable_service=att, expense_service=svc, changed_qbo_ids=set(),
        )
    assert [e for e, _ in fanout] == [pre]
    msgs = [r.getMessage() for r in caplog.records]
    assert any("qbo.purchase.late_attach_reread_failed" in m for m in msgs)
    assert not any("secret driver text" in m for m in msgs)


def test_refused_enqueue_on_an_empty_tick_is_counted():
    result, outbox = _run_tick(
        late_linked={"q-old": _sx(500)}, lines_by_expense={500: [_line(10)]}, outbox_result=None,
    )
    assert result["ms_fanout_refused"] == 1
    assert result["ms_fanout_enqueued"] == 0


def test_box_receives_only_the_changed_expense_not_the_late_linked_one():
    changed, late = _sx(1), _sx(500)
    cap: dict = {}
    _, outbox = _run_tick(
        changed=[("q-1", changed)], late_linked={"q-old": late},
        lines_by_expense={1: [_line(10)], 500: [_line(10)]}, box=True, capture=cap,
    )
    assert sorted(_enqueued(outbox)[10]) == ["exp-pub-1", "exp-pub-500"]
    pushed = [c.args[0] for c in cap["exp_svc"]._enqueue_box_uploads.call_args_list]
    assert pushed == [changed]
    batch = cap["box_outbox"].enqueue_box_excel_batch.call_args.kwargs["entities"]
    assert batch == [{"entity_type": "expense", "entity_public_id": "exp-pub-1"}]


def test_late_linked_draft_is_dropped_on_a_tick_with_changed_purchases():
    # The completed-only filter on the NORMAL path (not just the empty tick).
    changed, late_draft = _sx(1), _sx(500, "draft")
    _, outbox = _run_tick(
        changed=[("q-1", changed)], late_linked={"q-old": late_draft},
        lines_by_expense={1: [_line(10)], 500: [_line(10)]},
    )
    assert _enqueued(outbox) == {10: ["exp-pub-1"]}


def test_enqueue_ms_fanout_dedupes_a_repeated_expense():
    # The same expense handed in twice yields ONE id in its project's row.
    from scripts.sync_qbo_purchase import _enqueue_ms_fanout

    e = _sx(500)
    eli = MagicMock()
    eli.read_by_expense_id.return_value = [_line(10)]
    outbox = MagicMock()
    proj = MagicMock()
    proj.read_by_id.side_effect = lambda pid: SimpleNamespace(id=pid, public_id=f"proj-pub-{pid}")
    with patch("integrations.ms.outbox.business.service.MsOutboxService", return_value=outbox), patch(
        "entities.project.business.service.ProjectService", return_value=proj
    ):
        counts, project_map = _enqueue_ms_fanout(
            fanout_expenses=[(e, 500), (e, 500)], expense_line_item_service=eli, lines_by_expense_id={},
        )
    assert _enqueued(outbox) == {10: ["exp-pub-500"]}
    assert counts["enqueued"] == 1 and len(project_map[10]) == 1
