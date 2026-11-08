"""U-551b-f1-f1 F1: the completion recode / repoint step runs BEFORE the parent flips.

`complete_expense` used to finalize the parent (Status=completed) first and probe the
local lines last. The QBO pull preserves a reviewer's local coding only while the parent
is under review (submitted / in_review / approved), so a pull landing in that gap wrote
QBO's coding over the reviewer's line and the probe then saw no divergence - the
reviewer's recode was silently lost. The step now runs while the parent is still
`approved`. Persistence is mocked; nothing here opens a DB/HTTP connection.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService

_ENQUEUE_SUBMIT = "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit"
_UNCODED = "entities.expense.business.service.ExpenseService._expense_has_genuine_uncoded_staged_lines"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    monkeypatch.setattr(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        lambda *a, **k: eli,
    )
    monkeypatch.setattr(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        lambda self, ids: {},
    )


def _exp(**over):
    base = dict(
        id=55, public_id="pub-55", row_version="cm9ja2V0", vendor_id=7,
        expense_date="2026-09-01", reference_number="INV-1", total_amount=None,
        memo=None, is_draft=True, is_credit=False, qbo_id="P-100", realm_id="realm-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _svc(events, *, finalize=...):
    """A completion-ready service whose persistence records its calls into `events`."""
    repo = MagicMock()

    def _finalize(id):
        events.append("finalize")
        if finalize is ...:
            return _exp(is_draft=False)
        if isinstance(finalize, Exception):
            raise finalize
        return finalize

    repo.finalize_by_id.side_effect = _finalize
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_exp())
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    svc._enqueue_receipts_copy = MagicMock(return_value={"success": True, "queued": True, "errors": []})
    svc._enqueue_box_uploads = MagicMock()
    svc._enqueue_box_excel = MagicMock()
    svc.sync_to_excel_workbook = MagicMock(return_value={"errors": []})
    svc._upload_attachments_to_module_folder = MagicMock(return_value={"errors": []})
    return svc


def _recorder(events, result=None):
    def _fake(*, expense_id, qbo_linked=True, expense=None):
        events.append("recode")
        return result if result is not None else {"success": True, "enqueued": 1, "message": "ok"}

    return _fake


def test_recode_step_runs_before_finalize_by_id():
    """Kills: recode step moved back after finalize (the completion window)."""
    events: list = []
    svc = _svc(events)
    svc._enqueue_coding_recode_on_complete = _recorder(events)
    result = svc.complete_expense(public_id="pub-55")
    assert result["expense_finalized"] is True
    assert events == ["recode", "finalize"]


def test_recode_step_still_runs_after_the_holding_gate():
    """Kills: recode hoisted above the pre-finalize holding-code gate."""
    events: list = []
    svc = _svc(events)
    svc._enqueue_coding_recode_on_complete = _recorder(events)
    svc._assert_no_holding_code_on_complete = lambda expense_id: events.append("gate")
    svc.complete_expense(public_id="pub-55")
    assert events == ["gate", "recode", "finalize"]


def test_probe_reads_lines_while_parent_is_still_approved():
    """Kills: the probe reading lines after the parent flipped to completed."""
    events: list = []
    parent = {"status": "approved"}
    svc = _svc(events)
    original_finalize = svc.repo.finalize_by_id.side_effect

    def _finalize(id):
        parent["status"] = "completed"
        return original_finalize(id)

    svc.repo.finalize_by_id.side_effect = _finalize
    seen: list = []

    def _probe(*, expense_id, expense=None):
        svc.expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        seen.append(parent["status"])
        return None  # no repoint candidates -> first-write path

    svc._repoint_on_complete = _probe
    with patch(_UNCODED, return_value=False):
        result = svc.complete_expense(public_id="pub-55")
    assert seen == ["approved"]
    assert parent["status"] == "completed"
    assert result["qbo_sync"]["skip_reason"] == "no_uncoded_staged_lines"


def test_response_envelope_still_carries_qbo_sync():
    """Kills: qbo_sync dropped/renamed after the step moved."""
    events: list = []
    svc = _svc(events)
    payload = {"success": True, "enqueued": 2, "message": "Purchase line recode enqueued"}
    svc._enqueue_coding_recode_on_complete = _recorder(events, payload)
    result = svc.complete_expense(public_id="pub-55")
    assert result["qbo_sync"] == payload
    assert result["status_code"] == 200


def test_recode_is_called_once_with_the_linkage_and_expense():
    """Kills: qbo_linked computed wrongly / expense not forwarded at the new position."""
    events: list = []
    svc = _svc(events)
    svc._enqueue_coding_recode_on_complete = MagicMock(return_value={"success": True})
    svc.complete_expense(public_id="pub-55")
    svc._enqueue_coding_recode_on_complete.assert_called_once()
    kwargs = svc._enqueue_coding_recode_on_complete.call_args.kwargs
    assert kwargs["expense_id"] == 55 and kwargs["qbo_linked"] is True
    assert kwargs["expense"].public_id == "pub-55"

    svc2 = _svc([])
    svc2.read_by_public_id = MagicMock(return_value=_exp(qbo_id=None))
    svc2._enqueue_coding_recode_on_complete = MagicMock(return_value={"success": True})
    svc2.complete_expense(public_id="pub-55")
    assert svc2._enqueue_coding_recode_on_complete.call_args.kwargs["qbo_linked"] is False


def test_finalize_exception_does_not_undo_the_enqueue():
    """Kills: a rollback / compensating delete of the enqueue when finalize fails."""
    events: list = []
    svc = _svc(events, finalize=RuntimeError("db down"))
    with patch(_UNCODED, return_value=True), patch(
        _ENQUEUE_SUBMIT,
        return_value={"enqueued": 1, "skipped": 0, "writes_disabled": 0, "invalid": 0, "items": []},
    ) as enqueue:
        result = svc.complete_expense(public_id="pub-55")
    assert result["status_code"] == 500
    assert result["expense_finalized"] is False
    enqueue.assert_called_once()  # the persisted approval stands; the drain writes it
    # Nothing in the repo was called to withdraw it - finalize is the only repo call.
    assert [c[0] for c in svc.repo.method_calls] == ["finalize_by_id"]


def test_finalize_returning_none_does_not_undo_the_enqueue():
    """Kills: a rollback on the deleted-during-finalize 404 path."""
    events: list = []
    svc = _svc(events, finalize=None)
    svc._enqueue_coding_recode_on_complete = _recorder(events)
    result = svc.complete_expense(public_id="pub-55")
    assert result["status_code"] == 404
    assert result["expense_finalized"] is False
    assert events == ["recode", "finalize"]
