"""U-740 — enqueue QBO recode from complete_expense when staging is still uncoded."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.business.service import ExpenseService

_GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
_HOLDING = "entities.expense.business.service.any_holding_sub_cost_code"
_UNCODED = "entities.expense.business.service.ExpenseService._expense_has_genuine_uncoded_staged_lines"
_ENQUEUE_SUBMIT = "entities.expense.business.service.ExpenseService.enqueue_coding_recode_on_submit"


@pytest.fixture(autouse=True)
def _mock_expense_line_items_for_enqueue_holding_gate(monkeypatch):
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    monkeypatch.setattr(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        lambda *a, **k: eli,
    )


def _exp(**over):
    base = dict(
        id=55,
        public_id="pub-55",
        row_version="cm9ja2V0",
        vendor_id=7,
        expense_date="2026-09-01",
        reference_number="INV-1",
        total_amount=None,
        memo=None,
        is_draft=True,
        is_credit=False,
        qbo_id="P-100",
        realm_id="realm-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _complete_svc(*, finalize_returns=...):
    repo = MagicMock()
    repo.finalize_by_id.return_value = _exp(is_draft=False) if finalize_returns is ... else finalize_returns
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_exp())
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    svc._upload_to_general_receipts_folder = MagicMock(return_value={"errors": []})
    svc._enqueue_box_uploads = MagicMock()
    svc._enqueue_box_excel = MagicMock()
    svc.sync_to_excel_workbook = MagicMock(return_value={"errors": []})
    svc._upload_attachments_to_module_folder = MagicMock(return_value={"errors": []})
    return svc


def test_complete_enqueues_recode_when_staging_uncoded_and_gates_on():
    svc = _complete_svc()
    with patch(_UNCODED, return_value=True), patch(
        _ENQUEUE_SUBMIT,
        return_value={"enqueued": 1, "skipped": 0, "writes_disabled": 0, "invalid": 0, "items": []},
    ) as mock_enqueue, patch(_GATE, return_value=None):
        result = svc.complete_expense(public_id="pub-55")

    assert result["expense_finalized"] is True
    mock_enqueue.assert_called_once()
    assert result["qbo_sync"]["enqueued"] == 1


def test_complete_does_not_call_enqueue_when_staging_already_item_coded():
    svc = _complete_svc()
    with patch(_UNCODED, return_value=False), patch(_ENQUEUE_SUBMIT) as mock_enqueue:
        result = svc.complete_expense(public_id="pub-55")

    mock_enqueue.assert_not_called()
    assert result["qbo_sync"].get("skip_reason") == "no_uncoded_staged_lines"


def test_complete_skips_when_suggestion_already_enqueued():
    svc = ExpenseService()
    state = {
        "public_id": "ci-1",
        "status": "enqueued",
        "suggested_project_id": 10,
        "suggested_sub_cost_code_id": 20,
    }
    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), patch.object(
        ExpenseService,
        "_resolve_dominant_expense_coding_for_submit",
        return_value={"project_id": 77, "sub_cost_code_id": 481, "description": "x", "confirmed_mixed_codes": False},
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_confirm.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["items"][0]["reason"] == "already_enqueued"


def test_complete_path_succeeds_when_gates_off_and_recode_not_enqueued():
    svc = _complete_svc()
    with patch(_UNCODED, return_value=True), patch(
        _ENQUEUE_SUBMIT,
        return_value={
            "enqueued": 0,
            "skipped": 0,
            "writes_disabled": 1,
            "invalid": 0,
            "items": [{"outcome": "writes_disabled"}],
        },
    ):
        result = svc.complete_expense(public_id="pub-55")

    assert result["expense_finalized"] is True
    assert result["status_code"] == 200
    assert result["qbo_sync"]["enqueued"] == 0


def test_complete_path_succeeds_when_enqueue_raises():
    svc = _complete_svc()
    with patch(_UNCODED, return_value=True), patch(
        _ENQUEUE_SUBMIT, side_effect=RuntimeError("boom")
    ):
        result = svc.complete_expense(public_id="pub-55")

    assert result["expense_finalized"] is True
    assert result["qbo_sync"]["enqueued"] == 0
    assert result["qbo_sync"]["skip_reason"] == "RuntimeError"


def test_enqueue_on_submit_skips_holding_code_on_line():
    svc = ExpenseService()
    state = {
        "public_id": "ci-1",
        "status": "pending",
        "suggested_project_id": 10,
        "suggested_sub_cost_code_id": 20,
    }

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: [state]},
    ), patch(_HOLDING, return_value=True), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
    ) as mock_confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)

    mock_confirm.assert_not_called()
    assert summary["items"][0]["reason"] == "holding_code_on_line"


def _connection_ctx_with_cursor(cursor: MagicMock):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_expense_has_genuine_uncoded_staged_lines_reads_probe_sql():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    svc = ExpenseService()
    with patch(
        "shared.database.get_connection",
        return_value=_connection_ctx_with_cursor(cursor),
    ):
        assert svc._expense_has_genuine_uncoded_staged_lines(expense_id=740) is True

    sql = cursor.execute.call_args[0][0]
    assert "NEED TO CATEGORIZE" in sql
    assert "ItemRefValue" in sql
    assert cursor.execute.call_args[0][1] == 740


def test_expense_has_genuine_uncoded_staged_lines_false_when_probe_zero():
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    svc = ExpenseService()
    with patch(
        "shared.database.get_connection",
        return_value=_connection_ctx_with_cursor(cursor),
    ):
        assert svc._expense_has_genuine_uncoded_staged_lines(expense_id=12) is False


@pytest.mark.parametrize(
    "summary,expected",
    [
        ({"enqueued": 1, "items": []}, None),
        ({"enqueued": 0, "writes_disabled": 1, "items": []}, "writes_disabled"),
        (
            {"enqueued": 0, "skipped": 1, "items": [{"reason": "already_enqueued"}]},
            "already_enqueued",
        ),
        (
            {"enqueued": 0, "skipped": 1, "items": [{"confirm": {"status": "mapping_missing"}}]},
            "mapping_missing",
        ),
    ],
)
def test_recode_enqueue_skip_reason(summary, expected):
    assert ExpenseService._recode_enqueue_skip_reason(summary) == expected


def test_enqueue_on_complete_real_path_confirm_uses_dominant_local_coding():
    """Completed expense: probe → submit → real confirm (DB stubbed only)."""
    expense_id = 740
    user_id = 17
    probe_cursor = MagicMock()
    probe_cursor.fetchone.return_value = (1,)

    coding_state = {
        "public_id": "11111111-1111-1111-1111-111111111111",
        "status": "suggested",
        "suggested_project_id": 10,
        "suggested_sub_cost_code_id": 20,
    }
    dominant = {
        "project_id": 42,
        "sub_cost_code_id": 481,
        "description": "Local line coding",
        "confirmed_mixed_codes": True,
    }
    coding_item = SimpleNamespace(
        public_id=coding_state["public_id"],
        realm_id="realm-1",
        qbo_purchase_qbo_id="P-100",
        qbo_purchase_id=99,
        expense_id=expense_id,
    )
    outbox = MagicMock()
    outbox.enqueue.return_value = SimpleNamespace(public_id="outbox-1")

    confirm_calls: list[dict] = []
    real_confirm = CodingSuggestionService.confirm

    def _spy_confirm(self, **kwargs):
        confirm_calls.append(kwargs)
        return real_confirm(self, **kwargs)

    svc = ExpenseService()
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    svc._expense_line_item_service = eli
    # Isolation: pin the acting user (completion reads current_user_id) and keep the
    # staged-purchase read off the live DB (conftest blocks real connects).
    from shared.authz import current_user_id

    user_token = current_user_id.set(user_id)
    try:
      with patch(
        "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository.read_by_id",
        return_value=None,
      ), patch(
        "shared.database.get_connection",
        return_value=_connection_ctx_with_cursor(probe_cursor),
    ), patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={expense_id: [coding_state]},
    ), patch.object(
        svc,
        "_resolve_dominant_expense_coding_for_submit",
        return_value=dominant,
    ), patch(
        "integrations.intuit.qbo.base.client.recode_write_gate_reason",
        return_value=None,
    ), patch.object(
        CodingSuggestionService,
        "read_by_public_id",
        return_value=coding_item,
    ), patch.object(
        CodingSuggestionService,
        "_count_uncategorized_purchase_lines",
        return_value=2,
    ), patch.object(
        CodingSuggestionService,
        "_uncategorized_staging_qbo_line_ids",
        return_value=["1", "2"],
    ), patch(
        "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
        return_value={"value": "item-481", "name": "SCC 481"},
    ), patch.object(
        CodingSuggestionService,
        "record_confirmation",
    ) as mock_record, patch.object(
        CodingSuggestionService,
        "mark_enqueued",
    ), patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService",
        return_value=outbox,
    ), patch.object(CodingSuggestionService, "confirm", _spy_confirm):
        result = svc._enqueue_coding_recode_on_complete(expense_id=expense_id)
    finally:
        current_user_id.reset(user_token)

    assert result["enqueued"] == 1
    assert len(confirm_calls) == 1
    confirm_kwargs = confirm_calls[0]
    assert confirm_kwargs["project_id"] == 42
    assert confirm_kwargs["sub_cost_code_id"] == 481
    assert confirm_kwargs["description"] == "Local line coding"
    assert confirm_kwargs["was_overridden"] is True
    assert confirm_kwargs["user_id"] == user_id
    assert confirm_kwargs["confirmed_mixed_codes"] is True
    assert confirm_kwargs["allow_multi_line"] is True
    mock_record.assert_called_once()


def test_complete_recode_nothing_to_do_logs_info(caplog):
    svc = ExpenseService()
    with caplog.at_level(logging.INFO), patch.object(
        ExpenseService,
        "_expense_has_genuine_uncoded_staged_lines",
        return_value=False,
    ):
        svc._enqueue_coding_recode_on_complete(expense_id=901)

    assert any(
        r.levelno == logging.INFO
        and r.getMessage() == "expense.complete.recode_enqueue_skipped expense_id=901 reason=no_uncoded_staged_lines"
        for r in caplog.records
    )
    assert not any(
        r.levelno == logging.WARNING and "recode_enqueue_skipped" in r.getMessage()
        for r in caplog.records
    )


def test_complete_recode_real_skip_logs_warning_with_expense_id(caplog):
    svc = ExpenseService()
    with caplog.at_level(logging.WARNING), patch.object(
        ExpenseService,
        "_expense_has_genuine_uncoded_staged_lines",
        return_value=True,
    ), patch.object(
        ExpenseService,
        "enqueue_coding_recode_on_submit",
        return_value={
            "enqueued": 0,
            "skipped": 1,
            "writes_disabled": 0,
            "invalid": 0,
            "items": [{"reason": "holding_code_on_line"}],
        },
    ):
        svc._enqueue_coding_recode_on_complete(expense_id=902)

    assert any(
        r.levelno == logging.WARNING
        and r.getMessage()
        == "expense.complete.recode_enqueue_skipped expense_id=902 reason=holding_code_on_line"
        for r in caplog.records
    )
